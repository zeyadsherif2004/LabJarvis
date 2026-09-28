"""Compare local ASR and OCR candidates on the frozen synthetic media sets."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Protocol

from labjarvis.domain.reagent import classify_reagent
from labjarvis.evaluation.metrics import character_error_rate, word_error_rate
from labjarvis.infrastructure.model_errors import ModelAdapterError
from labjarvis.infrastructure.real_models import (
    EasyOcrLabelRecognition,
    FasterWhisperSpeechToText,
)
from labjarvis.ports.models import LabelResult, SpeechResult

ROOT = Path(__file__).resolve().parents[2]


class SpeechAdapter(Protocol):
    adapter_name: str
    model_name: str

    def transcribe(self, audio: bytes, *, content_type: str) -> SpeechResult: ...


class VisionAdapter(Protocol):
    adapter_name: str
    model_name: str

    def recognise(self, image: bytes, *, content_type: str) -> LabelResult: ...


def _mean(records: Sequence[dict[str, Any]], key: str) -> float:
    values = [float(record[key]) for record in records if record.get(key) is not None]
    return sum(values) / max(1, len(values))


def evaluate_speech(adapter: SpeechAdapter, samples: Sequence[dict[str, Any]]) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for sample in samples:
        audio = Path(sample["media_file"]).read_bytes()
        started = perf_counter()
        try:
            result = adapter.transcribe(audio, content_type=sample["content_type"])
            latency = max(0, round((perf_counter() - started) * 1000))
            records.append(
                {
                    "sample_id": sample["id"],
                    "input_sha256": hashlib.sha256(audio).hexdigest(),
                    "status": "succeeded",
                    "wer": word_error_rate(sample["expected_text"], result.transcript),
                    "cer": character_error_rate(sample["expected_text"], result.transcript),
                    "latency_ms": latency,
                    "real_time_factor": latency / 1000 / sample["duration_seconds"],
                    "prediction": result.transcript,
                }
            )
        except ModelAdapterError as exc:
            records.append(
                {
                    "sample_id": sample["id"],
                    "input_sha256": hashlib.sha256(audio).hexdigest(),
                    "status": "failed",
                    "error_code": exc.code,
                    "wer": 1.0,
                    "cer": 1.0,
                }
            )
    return {
        "evidence_class": "synthetic_media_post_review",
        "adapter": adapter.adapter_name,
        "model_name": adapter.model_name,
        "sample_count": len(records),
        "failure_count": sum(record["status"] == "failed" for record in records),
        "metrics": {
            "wer": _mean(records, "wer"),
            "cer": _mean(records, "cer"),
            "mean_latency_ms": _mean(records, "latency_ms"),
            "mean_real_time_factor": _mean(records, "real_time_factor"),
        },
        "records": records,
    }


def evaluate_vision(adapter: VisionAdapter, samples: Sequence[dict[str, Any]]) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for sample in samples:
        image = Path(sample["media_file"]).read_bytes()
        started = perf_counter()
        try:
            result = adapter.recognise(image, content_type=sample["content_type"])
            latency = max(0, round((perf_counter() - started) * 1000))
            decision = classify_reagent(
                result.visible_text,
                sample["expected_reagent"],
                match_threshold=92.0,
                uncertain_threshold=72.0,
            )
            false_accept = int(sample["truth"] != "match" and decision.outcome.value == "MATCH")
            records.append(
                {
                    "sample_id": sample["id"],
                    "input_sha256": hashlib.sha256(image).hexdigest(),
                    "status": "succeeded",
                    "cer": (
                        character_error_rate(sample["printed_text"], result.visible_text)
                        if sample["printed_text"]
                        else None
                    ),
                    "latency_ms": latency,
                    "false_accept": false_accept,
                    "outcome": decision.outcome.value,
                    "prediction": result.visible_text,
                }
            )
        except ModelAdapterError as exc:
            records.append(
                {
                    "sample_id": sample["id"],
                    "input_sha256": hashlib.sha256(image).hexdigest(),
                    "status": "failed",
                    "error_code": exc.code,
                    "false_accept": 0,
                }
            )
    return {
        "evidence_class": "synthetic_media_post_review",
        "adapter": adapter.adapter_name,
        "model_name": adapter.model_name,
        "sample_count": len(records),
        "failure_count": sum(record["status"] == "failed" for record in records),
        "metrics": {
            "cer": _mean(records, "cer"),
            "false_accept_rate": _mean(records, "false_accept"),
            "mean_latency_ms": _mean(records, "latency_ms"),
        },
        "records": records,
    }


def _load_manifest(name: str) -> tuple[list[dict[str, Any]], str]:
    path = ROOT / "research" / "data" / "manifests" / name
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    for record in records:
        record["media_file"] = (path.parent / record["media_path"]).resolve()
    return records, hashlib.sha256(path.read_bytes()).hexdigest()


def run(modality: str, model_name: str, output: Path) -> Path:
    cache = ROOT / ".labjarvis" / "models"
    if modality == "speech":
        samples, dataset_hash = _load_manifest("speech-clips.jsonl")
        adapter = FasterWhisperSpeechToText(
            model_name=model_name,
            device="cpu",
            compute_type="int8",
            cache_dir=cache / "faster-whisper",
            language="en",
            beam_size=5,
            allow_download=True,
        )
        candidate = evaluate_speech(adapter, samples)
    elif modality == "vision":
        samples, dataset_hash = _load_manifest("label-images.jsonl")
        adapter = EasyOcrLabelRecognition(
            languages=("en",),
            gpu=False,
            cache_dir=cache / "easyocr",
            model_name=model_name,
            allow_download=True,
        )
        candidate = evaluate_vision(adapter, samples)
    else:
        raise ValueError(f"unsupported modality: {modality}")
    payload = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "dataset_sha256": dataset_hash,
        "candidate": candidate,
        "prohibited_claims": ["real media performance", "human performance", "laboratory safety"],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(output)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--modality", choices=("speech", "vision"), required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(run(args.modality, args.model, args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
