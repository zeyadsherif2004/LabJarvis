"""Chained real-model evaluation through the production application service.

Speech: audio -> speech model -> interpreter/cascade -> deterministic validation
-> state gate -> pending action -> confirmation -> notebook.
Vision: frame(s) -> OCR -> normalisation -> deterministic comparator /
unanimous multi-frame consensus -> MATCH/MISMATCH/UNCERTAIN -> provenance.

Nothing is shortcut: the runner builds ``LabJarvisService`` with the adapters
chosen by ``build_model_adapters`` for the given profile (default: local), a
throwaway SQLite database, and imports/approves a protocol exactly as the API
does. It fails a scenario unless the *expected* command or reagent result is
reached - "some valid intent" is not enough.

The same runner evaluates today's synthetic clips and, later, real recordings
and photographs: point ``--speech``/``--labels`` at a manifest (see
``research/real_media/README.md``). Every output records the evidence class of
its inputs.

    python -m research.real_media.run_chain --scenario demo
    python -m research.real_media.run_chain --speech <manifest.jsonl> --labels <manifest.jsonl>
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from itertools import count
from pathlib import Path
from time import perf_counter
from typing import Any

import psutil

from labjarvis.application.service import LabJarvisService
from labjarvis.domain.commands import validate_and_normalise_command
from labjarvis.domain.models import EntrySource, ParsedCommand
from labjarvis.infrastructure.database import SqlAlchemyUnitOfWork, create_session_factory
from labjarvis.infrastructure.documents import DOCX_MIME, DocumentExtractor
from labjarvis.infrastructure.inference import DirectInferenceRunner
from labjarvis.infrastructure.media_storage import DiscardRawMediaStore
from labjarvis.infrastructure.model_factory import ModelAdapters, build_model_adapters
from labjarvis.settings import ModelMode, Settings

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "sample_data" / "protocols" / "development-only-ethanol-protocol.docx"
CHAIN_MEDIA = ROOT / "research" / "data" / "media" / "chain"
OUT = ROOT / "research" / "final_report_evidence" / "real-chain"


def _wer(reference: str, hypothesis: str) -> float:
    ref = [w for w in "".join(c.lower() if c.isalnum() or c.isspace() else " " for c in reference).split()]
    hyp = [w for w in "".join(c.lower() if c.isalnum() or c.isspace() else " " for c in hypothesis).split()]
    previous = list(range(len(hyp) + 1))
    for i, word in enumerate(ref, 1):
        current = [i] + [0] * len(hyp)
        for j, other in enumerate(hyp, 1):
            current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (word != other))
        previous = current
    return previous[-1] / max(1, len(ref))


def build_service(settings: Settings) -> tuple[LabJarvisService, ModelAdapters]:
    adapters = build_model_adapters(settings)
    factory = create_session_factory("sqlite+pysqlite://", create_schema=True)
    ids = count(1)
    service = LabJarvisService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(factory),
        document_extractor=DocumentExtractor(max_bytes=10_000_000, max_pages=50),
        speech=adapters.speech,
        verification_speech=adapters.verification_speech,
        text=adapters.text,
        labels=adapters.labels,
        inference_runner=DirectInferenceRunner(),
        clock=lambda: datetime.now(UTC),
        new_id=lambda: f"chain-{next(ids)}",
        command_confidence_threshold=settings.command_confidence_threshold,
        reagent_match_threshold=settings.reagent_match_threshold,
        reagent_uncertain_threshold=settings.reagent_uncertain_threshold,
        raw_media_store=DiscardRawMediaStore(),
        retain_transcripts=True,
        wake_phrase=settings.wake_phrase,
    )
    return service, adapters


def start(service: LabJarvisService):  # type: ignore[no-untyped-def]
    imported = service.import_protocol(SAMPLE.name, DOCX_MIME, SAMPLE.read_bytes())
    approved = service.approve_protocol(imported.version.id, expected_revision=0)
    session = service.create_session(approved.id)
    return service.start_session(session.id, expected_version=0)


def _expected_matches(command: ParsedCommand, expected_intent: str, expected: dict[str, str]) -> bool:
    canonical = validate_and_normalise_command(
        ParsedCommand(command.intent, command.parameters, command.confidence)
    )
    if canonical.intent != expected_intent:
        return False
    for key, value in expected.items():
        got = canonical.parameters.get(key, "")
        try:
            if Decimal(got) != Decimal(value):
                return False
        except InvalidOperation:
            if got.casefold() != value.casefold():
                return False
    return True


def run_speech(service: LabJarvisService, rows: Iterable[dict[str, Any]], *, confirm: bool) -> list[dict[str, Any]]:
    records = []
    for row in rows:
        session = start(service)
        audio = (ROOT / row["media_path"]).read_bytes()
        started = perf_counter()
        record: dict[str, Any] = {"id": row["id"], "evidence_class": row.get("evidence_class", "synthetic")}
        try:
            proposal = service.handle_audio_command(
                session.id,
                audio,
                content_type=row.get("content_type", "audio/wav"),
                idempotency_key=f"chain-{row['id']}",
                expected_version=session.version,
                correlation_id=f"chain-{row['id']}",
            )
        except Exception as exc:  # recorded, never hidden
            record.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            records.append(record)
            continue
        total_ms = (perf_counter() - started) * 1000
        review = service.get_session_review(session.id)
        speech = next((i for i in review.inferences if i.modality == "speech"), None)
        text = next((i for i in review.inferences if i.modality == "text"), None)
        expected_intent = row["expected_intent"]
        expected = dict(row.get("expected_parameters", {}))
        reached = _expected_matches(proposal.parsed_command, expected_intent, expected)
        record.update(
            status="succeeded",
            transcript=proposal.transcript,
            wer=round(_wer(row.get("expected_text", ""), proposal.transcript), 4) if row.get("expected_text") else None,
            intent=proposal.parsed_command.intent,
            parameters=dict(proposal.parsed_command.parameters),
            expected_intent=expected_intent,
            expected_parameters=expected,
            expected_reached=reached,
            pending_action=proposal.pending_action is not None,
            route=(text.output.get("cascade_route", "") if text else ""),
            speech_model=speech.model_name if speech else "",
            speech_device=(speech.output.get("device", "") if speech else ""),
            text_model=text.model_name if text else "",
            speech_ms=speech.latency_ms if speech else None,
            text_ms=text.latency_ms if text else None,
            total_ms=round(total_ms),
        )
        if confirm and proposal.pending_action is not None and reached:
            confirmed = service.confirm_pending_action(
                proposal.pending_action.id, accepted=True, correlation_id=f"chain-confirm-{row['id']}"
            )
            entry = confirmed.entry
            record["recorded"] = dict(entry.payload) if entry is not None else {}
            record["notebook_entry_type"] = entry.entry_type if entry is not None else ""
        records.append(record)
    return records


def run_labels(service: LabJarvisService, rows: Iterable[dict[str, Any]], *, frames: int) -> list[dict[str, Any]]:
    records = []
    for row in rows:
        session = start(service)
        paths = row.get("frame_paths") or [row["media_path"]] * frames
        images = [(ROOT / path).read_bytes() for path in paths[:frames]]
        started = perf_counter()
        record: dict[str, Any] = {"id": row["id"], "evidence_class": row.get("evidence_class", "synthetic")}
        try:
            result = service.verify_reagent(
                session.id,
                images[0],
                content_type=row.get("content_type", "image/png"),
                correlation_id=f"chain-{row['id']}",
                expected_version=session.version,
                idempotency_key=f"chain-label-{row['id']}",
                additional_frames=tuple(images[1:]),
            )
        except Exception as exc:
            record.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            records.append(record)
            continue
        record.update(
            status="succeeded",
            observed=result.observed_label,
            outcome=result.outcome.value,
            expected_outcome=row["expected_outcome"],
            expected_reached=result.outcome.value == row["expected_outcome"],
            frames=len(images),
            total_ms=round((perf_counter() - started) * 1000),
        )
        records.append(record)
    return records


def demo_rows() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    speech = [
        {"id": "demo-grammar-measurement", "media_path": "research/data/media/chain/chain-measure-volume.wav",
         "expected_text": "Jarvis, record the volume as 10 millilitres",
         "expected_intent": "RECORD_MEASUREMENT", "expected_parameters": {"value": "10", "unit": "mL"}},
        {"id": "demo-llm-paraphrase-timer", "media_path": "research/data/media/chain/chain-paraphrase-timer.wav",
         "expected_text": "Jarvis, give me a 10 minute timer",
         "expected_intent": "SET_TIMER", "expected_parameters": {"duration_seconds": "600"},
         "note": "human-written paraphrase from CLINC150 (held-out B pool wording), spoken by TTS"},
        {"id": "demo-llm-paraphrase-temperature", "media_path": "research/data/media/chain/chain-paraphrase-temperature.wav",
         "expected_text": "Jarvis, log the temperature at 37 celsius",
         "expected_intent": "RECORD_MEASUREMENT", "expected_parameters": {"value": "37", "unit": "degC"}},
        {"id": "demo-check-reagent", "media_path": "research/data/media/chain/chain-check-reagent.wav",
         "expected_text": "Jarvis, check reagent", "expected_intent": "VERIFY_REAGENT"},
    ]  # fmt: skip
    for row in speech:
        row["evidence_class"] = "synthetic speech (Windows TTS)"
    labels = [
        {"id": "demo-label-match", "media_path": "research/data/media/vision/label-001.png",
         "expected_outcome": "MATCH", "evidence_class": "synthetic rendered label"},
    ]  # fmt: skip
    mismatch = next(
        (json.loads(line) for line in (ROOT / "research/data/manifests/label-images.jsonl").read_text(encoding="utf-8").splitlines()
         if line.strip() and json.loads(line).get("truth") == "mismatch" and json.loads(line).get("variant") == "clean"),
        None,
    )  # fmt: skip
    if mismatch is not None:
        labels.append({
            "id": f"demo-label-mismatch-{mismatch['id']}",
            "media_path": "research/data/media/vision/" + Path(mismatch["media_path"]).name,
            "expected_outcome": "MISMATCH",
            "evidence_class": "synthetic rendered label",
            "note": f"printed '{mismatch.get('printed_text')}' where ethanol is expected",
        })  # fmt: skip
    return speech, labels


def load_manifest(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def summarise(records: list[dict[str, Any]], key: str) -> dict[str, Any]:
    done = [r for r in records if r.get("status") == "succeeded"]
    latencies = sorted(r[key] for r in done if r.get(key) is not None)
    warm = latencies[1:] if len(latencies) > 1 else latencies
    return {
        "n": len(records),
        "failed": len(records) - len(done),
        "expected_reached": sum(bool(r.get("expected_reached")) for r in records),
        f"{key}_p50": statistics.median(warm) if warm else None,
        f"{key}_p95": warm[min(len(warm) - 1, round(0.95 * (len(warm) - 1)))] if warm else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=("demo", "manifest"), default="demo")
    parser.add_argument("--speech", type=Path)
    parser.add_argument("--labels", type=Path)
    parser.add_argument("--frames", type=int, default=1)
    parser.add_argument("--mode", choices=("local", "fake"), default="local")
    parser.add_argument("--speech-device", default="cpu")
    parser.add_argument("--vision-gpu", action="store_true")
    parser.add_argument("--out", type=Path, default=OUT / "chain-demo.json")
    args = parser.parse_args()
    settings = Settings(
        model_mode=ModelMode(args.mode),
        speech_device=args.speech_device,
        speech_compute_type="float16" if args.speech_device == "cuda" else "int8",
        vision_gpu=args.vision_gpu,
        model_cache_dir=ROOT / ".labjarvis" / "models",
        data_dir=ROOT / ".labjarvis",
    )
    service, adapters = build_service(settings)
    try:
        if args.scenario == "demo":
            speech_rows, label_rows = demo_rows()
        else:
            speech_rows = load_manifest(args.speech) if args.speech else []
            label_rows = load_manifest(args.labels) if args.labels else []
        speech = run_speech(service, speech_rows, confirm=True)
        labels = run_labels(service, label_rows, frames=args.frames)
    finally:
        adapters.close()
    payload = {
        "created_at": datetime.now(UTC).isoformat(),
        "profile": settings.execution_profile(),
        "stages": {
            "speech": f"{settings.speech_model_name} ({args.speech_device})",
            "text": f"{settings.effective_text_interpreter().value} + {settings.text_model_name}",
            "vision": f"{settings.vision_model_name} ({'gpu' if args.vision_gpu else 'cpu'})",
        },
        "platform": platform.platform(),
        "peak_rss_mb": round(psutil.Process().memory_info().rss / 1e6, 1),
        "speech": {"summary": summarise(speech, "total_ms"), "records": speech},
        "labels": {"summary": summarise(labels, "total_ms"), "records": labels},
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    for record in speech + labels:
        print(record["id"], record.get("status"), "reached" if record.get("expected_reached") else "NOT REACHED",
              record.get("route", ""), record.get("intent", record.get("outcome", "")), record.get("error", ""))  # fmt: skip
    failures = [r for r in speech + labels if not r.get("expected_reached")]
    return 1 if failures else 0


_ = EntrySource  # imported for type clarity in extensions

if __name__ == "__main__":
    raise SystemExit(main())
