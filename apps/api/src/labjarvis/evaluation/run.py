"""Run configured multimodal candidates and write machine-readable evidence."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from time import perf_counter
from typing import Any

import psutil
import yaml

from labjarvis.domain.reagent import classify_reagent
from labjarvis.evaluation.metrics import (
    character_error_rate,
    exact_slot_match,
    precision_recall_f1,
    slot_counts,
    word_error_rate,
)
from labjarvis.evaluation.schemas import (
    EvaluationCandidate,
    EvaluationConfig,
    EvaluationSample,
)
from labjarvis.infrastructure.fake_models import (
    FakeLabelRecognition,
    FakeSpeechToText,
    FakeTextUnderstanding,
)
from labjarvis.infrastructure.hosted_models import (
    HostedClientConfig,
    OpenAIClient,
    OpenAITextUnderstanding,
)
from labjarvis.infrastructure.model_errors import ModelAdapterError
from labjarvis.infrastructure.real_models import (
    EasyOcrLabelRecognition,
    FasterWhisperSpeechToText,
    OllamaTextUnderstanding,
)
from labjarvis.ports.models import CommandContext, TextUnderstandingPort
from labjarvis.settings import Settings

ROOT = Path(__file__).resolve().parents[5]


def _openai_text_adapter(*, model_name: str, settings: Settings) -> OpenAITextUnderstanding:
    """Build one hosted evaluation adapter from validated secret settings."""

    if settings.hosted_api_key is None:
        raise ValueError("hosted evaluation requires LABJARVIS_HOSTED_API_KEY")
    client = OpenAIClient(
        HostedClientConfig(
            api_key=settings.hosted_api_key,
            base_url=settings.hosted_base_url,
            timeout_seconds=settings.inference_timeout_seconds,
            max_response_bytes=settings.hosted_max_response_bytes,
            max_retries=settings.hosted_max_retries,
        )
    )
    return OpenAITextUnderstanding(
        client,
        model_name=model_name,
        wake_phrase=settings.wake_phrase,
    )


def _load_config(path: Path) -> EvaluationConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return EvaluationConfig.model_validate(raw)


def _load_samples(path: Path, modality: str) -> tuple[EvaluationSample, ...]:
    samples: list[EvaluationSample] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            sample = EvaluationSample.model_validate_json(line)
        except ValueError as exc:
            raise ValueError(f"invalid evaluation sample at {path}:{line_number}") from exc
        if sample.modality != modality:
            raise ValueError(f"unexpected modality at {path}:{line_number}")
        samples.append(sample)
    if not samples:
        raise ValueError(f"evaluation dataset is empty: {path}")
    return tuple(samples)


def _media(sample: EvaluationSample, dataset_path: Path) -> bytes:
    if sample.media_path is None:
        raise ValueError(f"real sample {sample.id} requires media_path")
    path = (dataset_path.parent / sample.media_path).resolve()
    if not path.is_file():
        raise ValueError(f"media fixture does not exist for sample {sample.id}")
    return path.read_bytes()


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _timed[T](call: Callable[[], T]) -> tuple[T, int, float]:
    started = perf_counter()
    result = call()
    latency = max(0, round((perf_counter() - started) * 1000))
    process_rss_mb = round(psutil.Process().memory_info().rss / 1024 / 1024, 2)
    return result, latency, process_rss_mb


def _speech_records(
    candidate: EvaluationCandidate,
    samples: Sequence[EvaluationSample],
    dataset_path: Path,
    settings: Settings,
) -> list[dict[str, Any]]:
    adapter = (
        FakeSpeechToText()
        if candidate.adapter == "fake"
        else FasterWhisperSpeechToText(
            model_name=candidate.model_name,
            device=candidate.device,
            compute_type=candidate.compute_type,
            cache_dir=settings.model_cache_dir / "faster-whisper",
        )
    )
    records: list[dict[str, Any]] = []
    for sample in samples:
        assert sample.expected_text is not None
        media = (
            f"FAKE:{sample.expected_text}".encode()
            if candidate.adapter == "fake"
            else _media(sample, dataset_path)
        )
        try:
            content_type = (
                "audio/fake"
                if candidate.adapter == "fake"
                else sample.content_type or "audio/wav"
            )
            result, latency, process_rss_mb = _timed(
                partial(adapter.transcribe, media, content_type=content_type)
            )
            record = {
                "sample_id": sample.id,
                "input_sha256": _hash(media),
                "status": "succeeded",
                "latency_ms": latency,
                "process_rss_mb": process_rss_mb,
                "wer": word_error_rate(sample.expected_text, result.transcript),
                "cer": character_error_rate(sample.expected_text, result.transcript),
            }
            if sample.duration_seconds is not None:
                record["real_time_factor"] = latency / 1000 / sample.duration_seconds
            record["prediction"] = result.transcript
        except ModelAdapterError as exc:
            record = {
                "sample_id": sample.id,
                "input_sha256": _hash(media),
                "status": "failed",
                "error_code": exc.code,
                "wer": 1.0,
                "cer": 1.0,
            }
        records.append(record)
    return records


def _text_records(
    candidate: EvaluationCandidate,
    samples: Sequence[EvaluationSample],
    settings: Settings,
) -> list[dict[str, Any]]:
    adapter: TextUnderstandingPort
    if candidate.adapter == "fake":
        adapter = FakeTextUnderstanding()
    elif candidate.adapter == "openai":
        adapter = _openai_text_adapter(model_name=candidate.model_name, settings=settings)
    else:
        adapter = OllamaTextUnderstanding(
            model_name=candidate.model_name,
            base_url=settings.ollama_base_url,
            timeout_seconds=settings.inference_timeout_seconds,
            context_tokens=settings.text_context_tokens,
        )
    records: list[dict[str, Any]] = []
    try:
        for sample in samples:
            assert sample.transcript is not None and sample.expected_intent is not None
            try:
                context = CommandContext(
                    session_id="evaluation-session",
                    session_state="ACTIVE",
                    active_step_id="evaluation-step",
                    active_instruction=sample.active_instruction,
                )
                text_result, latency, process_rss_mb = _timed(
                    partial(adapter.interpret, sample.transcript, context)
                )
                result = text_result.command
                slot_tp, slot_fp, slot_fn = slot_counts(
                    sample.expected_parameters,
                    result.parameters,
                )
                record = {
                    "sample_id": sample.id,
                    "input_sha256": _hash(sample.transcript.encode()),
                    "status": "succeeded",
                    "latency_ms": latency,
                    "process_rss_mb": process_rss_mb,
                    "intent_correct": int(result.intent == sample.expected_intent),
                    "slots_exact": exact_slot_match(
                        sample.expected_parameters,
                        result.parameters,
                    ),
                    "schema_valid": 1,
                    "slot_tp": slot_tp,
                    "slot_fp": slot_fp,
                    "slot_fn": slot_fn,
                    "prediction": {
                        "intent": result.intent,
                        "parameters": dict(result.parameters),
                        "confidence": result.confidence,
                    },
                }
                if sample.expected_intent == "UNSUPPORTED":
                    record["unsupported_rejected"] = int(result.intent == "UNSUPPORTED")
                    record["unsafe_action"] = int(result.intent != "UNSUPPORTED")
                    record["ambiguity_detected"] = int(bool(result.ambiguities))
            except ModelAdapterError as exc:
                record = {
                    "sample_id": sample.id,
                    "input_sha256": _hash(sample.transcript.encode()),
                    "status": "failed",
                    "error_code": exc.code,
                    "intent_correct": 0,
                    "slots_exact": 0,
                    "schema_valid": 0,
                }
            records.append(record)
    finally:
        close = getattr(adapter, "close", None)
        if callable(close):
            close()
    return records


def _vision_records(
    candidate: EvaluationCandidate,
    samples: Sequence[EvaluationSample],
    dataset_path: Path,
    settings: Settings,
) -> list[dict[str, Any]]:
    adapter = (
        FakeLabelRecognition()
        if candidate.adapter == "fake"
        else EasyOcrLabelRecognition(
            languages=settings.vision_languages,
            gpu=candidate.device != "cpu",
            cache_dir=settings.model_cache_dir / "easyocr",
            model_name=candidate.model_name,
        )
    )
    records: list[dict[str, Any]] = []
    for sample in samples:
        assert sample.expected_text is not None
        assert sample.expected_reagent is not None
        assert sample.expected_verification is not None
        media = (
            f"FAKE:{sample.expected_text}".encode()
            if candidate.adapter == "fake"
            else _media(sample, dataset_path)
        )
        try:
            content_type = (
                "image/fake"
                if candidate.adapter == "fake"
                else sample.content_type or "image/jpeg"
            )
            result, latency, process_rss_mb = _timed(
                partial(adapter.recognise, media, content_type=content_type)
            )
            verification = classify_reagent(
                result.visible_text,
                sample.expected_reagent,
                match_threshold=settings.reagent_match_threshold,
                uncertain_threshold=settings.reagent_uncertain_threshold,
            )
            predicted_outcome = verification.outcome.value
            false_accept = int(
                predicted_outcome == "MATCH" and sample.expected_verification != "MATCH"
            )
            record = {
                "sample_id": sample.id,
                "input_sha256": _hash(media),
                "status": "succeeded",
                "latency_ms": latency,
                "process_rss_mb": process_rss_mb,
                "cer": character_error_rate(sample.expected_text, result.visible_text),
                "text_exact": int(
                    sample.expected_text.casefold().strip()
                    == result.visible_text.casefold().strip()
                ),
                "verification_correct": int(
                    predicted_outcome == sample.expected_verification
                ),
                "false_accept": false_accept,
                "uncertain": int(predicted_outcome == "UNCERTAIN"),
                "expected_verification": sample.expected_verification,
                "predicted_verification": predicted_outcome,
                "prediction": {
                    "visible_text": result.visible_text,
                    "verification": predicted_outcome,
                },
            }
        except ModelAdapterError as exc:
            record = {
                "sample_id": sample.id,
                "input_sha256": _hash(media),
                "status": "failed",
                "error_code": exc.code,
                "cer": 1.0,
                "text_exact": 0,
                "verification_correct": 0,
                "false_accept": 0,
                "uncertain": 1,
            }
        records.append(record)
    return records


def _average(records: Sequence[Mapping[str, Any]], key: str) -> float | None:
    values = [float(record[key]) for record in records if key in record]
    return sum(values) / len(values) if values else None


def _summary(modality: str, records: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    keys = {
        "speech": (
            "wer",
            "cer",
            "latency_ms",
            "real_time_factor",
            "process_rss_mb",
        ),
        "text": (
            "intent_correct",
            "slots_exact",
            "schema_valid",
            "unsupported_rejected",
            "unsafe_action",
            "ambiguity_detected",
            "latency_ms",
            "process_rss_mb",
        ),
        "vision": (
            "cer",
            "text_exact",
            "verification_correct",
            "false_accept",
            "uncertain",
            "latency_ms",
            "process_rss_mb",
        ),
    }[modality]
    summary = {
        key: average for key in keys if (average := _average(records, key)) is not None
    }
    rss_values = [
        float(record["process_rss_mb"])
        for record in records
        if "process_rss_mb" in record
    ]
    if rss_values:
        summary["peak_process_rss_mb"] = max(rss_values)

    if modality == "text":
        true_positive = sum(int(record.get("slot_tp", 0)) for record in records)
        false_positive = sum(int(record.get("slot_fp", 0)) for record in records)
        false_negative = sum(int(record.get("slot_fn", 0)) for record in records)
        precision, recall, f1 = precision_recall_f1(
            true_positive,
            false_positive,
            false_negative,
        )
        summary.update(
            slot_precision=precision,
            slot_recall=recall,
            slot_f1=f1,
        )
    elif modality == "vision":
        classified = [
            record
            for record in records
            if "expected_verification" in record and "predicted_verification" in record
        ]
        true_positive = sum(
            int(
                record["expected_verification"] == "MATCH"
                and record["predicted_verification"] == "MATCH"
            )
            for record in classified
        )
        false_positive = sum(
            int(
                record["expected_verification"] != "MATCH"
                and record["predicted_verification"] == "MATCH"
            )
            for record in classified
        )
        false_negative = sum(
            int(
                record["expected_verification"] == "MATCH"
                and record["predicted_verification"] != "MATCH"
            )
            for record in classified
        )
        precision, recall, f1 = precision_recall_f1(
            true_positive,
            false_positive,
            false_negative,
        )
        summary.update(
            match_precision=precision,
            match_recall=recall,
            match_f1=f1,
        )
    return summary


def _candidate_result(
    candidate: EvaluationCandidate,
    samples: Sequence[EvaluationSample],
    dataset_path: Path,
    settings: Settings,
    *,
    include_predictions: bool,
) -> dict[str, Any]:
    if candidate.modality == "speech":
        records = _speech_records(candidate, samples, dataset_path, settings)
    elif candidate.modality == "text":
        records = _text_records(candidate, samples, settings)
    else:
        records = _vision_records(candidate, samples, dataset_path, settings)
    if not include_predictions:
        for record in records:
            record.pop("prediction", None)
    return {
        "candidate_id": candidate.id,
        "modality": candidate.modality,
        "adapter": candidate.adapter,
        "model_name": candidate.model_name,
        "sample_count": len(records),
        "failure_count": sum(record["status"] != "succeeded" for record in records),
        "metrics": _summary(candidate.modality, records),
        "records": records,
    }


def _write_summary(path: Path, candidates: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(
            output,
            fieldnames=(
                "candidate_id",
                "modality",
                "adapter",
                "model_name",
                "sample_count",
                "failure_count",
                "metrics_json",
            ),
        )
        writer.writeheader()
        for candidate in candidates:
            writer.writerow(
                {
                    **{key: candidate[key] for key in writer.fieldnames if key != "metrics_json"},
                    "metrics_json": json.dumps(candidate["metrics"], sort_keys=True),
                }
            )


def run_evaluation(config_path: Path, *, output_dir: Path) -> Path:
    config_path = config_path.resolve()
    config = _load_config(config_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings()
    datasets: dict[str, tuple[Path, tuple[EvaluationSample, ...]]] = {}
    for modality, relative_path in config.datasets.items():
        dataset_path = (config_path.parent / relative_path).resolve()
        datasets[modality] = (dataset_path, _load_samples(dataset_path, modality))

    candidates = []
    for candidate in config.candidates:
        if not candidate.enabled:
            continue
        dataset_path, samples = datasets[candidate.modality]
        if config.evidence_class == "synthetic_development_only" and not all(
            sample.synthetic for sample in samples
        ):
            raise ValueError("development configs may only use synthetic-labelled samples")
        candidates.append(
            _candidate_result(
                candidate,
                samples,
                dataset_path,
                settings,
                include_predictions=config.include_predictions,
            )
        )

    config_bytes = config_path.read_bytes()
    result = {
        "schema_version": 1,
        "run_name": config.name,
        "created_at": datetime.now(UTC).isoformat(),
        "evidence_class": config.evidence_class,
        "eligible_for_final_claims": config.eligible_for_final_claims,
        "config_sha256": _hash(config_bytes),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "processor": platform.processor() or "not-reported",
        },
        "candidates": candidates,
    }
    result_path = output_dir / f"{config.name}-results.json"
    temporary_path = result_path.with_suffix(".json.tmp")
    temporary_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    temporary_path.replace(result_path)
    _write_summary(output_dir / f"{config.name}-summary.csv", candidates)
    return result_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "evaluation" / "results")
    arguments = parser.parse_args()
    result = run_evaluation(arguments.config, output_dir=arguments.output)
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
