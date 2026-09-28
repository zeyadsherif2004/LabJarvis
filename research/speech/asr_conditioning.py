"""Protocol-conditioned ASR A/B and the dual-transcription cross-check.

Runs the production faster-whisper adapter over a speech manifest with
recognition conditioning ``off`` / ``prompt`` / ``hotwords`` (vocabulary built by
``protocol_vocabulary`` from the manifest's own reagent/unit terms plus the base
scientific vocabulary), and reports WER, number accuracy, unit accuracy and
latency. It then evaluates the high-integrity cross-check (primary vs verifier
model, ``compare_transcripts``): recall of primary number/unit errors, false
alarms (nuisance re-asks on correct transcripts) and latency cost.

Evidence class of the default manifest: SYNTHETIC TTS speech. The identical
command on a real-speech manifest (research/real_media/README.md) produces the
real-speech result.

    python -m research.speech.asr_conditioning [--manifest ...] [--device cuda]
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from time import perf_counter
from typing import Any

from labjarvis.domain.entity_grounding import UNIT_EVIDENCE, unit_is_grounded, utterance_numbers
from labjarvis.domain.models import (
    ProtocolRequirement,
    ProtocolStepDraft,
    ProtocolVersion,
    ProtocolVersionStatus,
    SourceReference,
)
from labjarvis.domain.speech_vocabulary import protocol_vocabulary
from labjarvis.domain.transcripts import compare_transcripts
from labjarvis.infrastructure.real_models import FasterWhisperSpeechToText
from research.real_media.run_chain import _wer

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "research" / "data" / "manifests" / "speech-clips.jsonl"
OUT = ROOT / "research" / "final_report_evidence" / "speech"
REAGENTS = ("ethanol", "methanol", "isopropanol", "acetonitrile", "sodium chloride", "tris buffer")


def entities(text: str) -> tuple[frozenset[str], frozenset[str]]:
    numbers = frozenset(str(n.normalize()) for n in utterance_numbers(text) if n != 1)
    units = frozenset(unit for unit in UNIT_EVIDENCE if unit_is_grounded(text, unit))
    return numbers, units


def vocabulary() -> tuple[str, ...]:
    from datetime import UTC, datetime

    step = ProtocolStepDraft(
        id="s1", number=1, title="t", instruction="i",
        requirements=tuple(ProtocolRequirement(requirement_id=f"r{i}", name=name, unit="mL")
                           for i, name in enumerate(REAGENTS)),
        duration_seconds=None, safety_notes=(), source=SourceReference(page=1, section=None, excerpt="i"),
    )  # fmt: skip
    version = ProtocolVersion(
        id="v", protocol_id="p", version_number=1, revision=1, status=ProtocolVersionStatus.APPROVED,
        steps=(step,), source_sha256="x", created_at=datetime.now(UTC),
    )  # fmt: skip
    return protocol_vocabulary(version)


def run(adapter: FasterWhisperSpeechToText, rows: list[dict[str, Any]], vocab: tuple[str, ...]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        audio = (MANIFEST.parent / row["media_path"]).resolve().read_bytes()
        started = perf_counter()
        transcript = adapter.transcribe(audio, content_type="audio/wav", vocabulary=vocab).transcript
        ms = (perf_counter() - started) * 1000
        expected_numbers, expected_units = entities(row["expected_text"])
        numbers, units = entities(transcript)
        out.append(
            {
                "id": row["id"],
                "augmentation": row.get("augmentation", ""),
                "expected": row["expected_text"],
                "transcript": transcript,
                "wer": _wer(row["expected_text"], transcript),
                "numbers_ok": numbers == expected_numbers,
                "units_ok": units == expected_units,
                "has_entities": bool(expected_numbers or expected_units),
                "ms": ms,
            }
        )
    return out


def summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    entity_rows = [r for r in records if r["has_entities"]]
    warm = sorted(r["ms"] for r in records[1:])
    return {
        "n": len(records),
        "mean_wer": round(statistics.mean(r["wer"] for r in records), 4),
        "number_accuracy": round(sum(r["numbers_ok"] for r in entity_rows) / max(1, len(entity_rows)), 4),
        "unit_accuracy": round(sum(r["units_ok"] for r in entity_rows) / max(1, len(entity_rows)), 4),
        "entity_items": len(entity_rows),
        "p50_ms": round(warm[len(warm) // 2], 1),
        "p95_ms": round(warm[min(len(warm) - 1, round(0.95 * (len(warm) - 1)))], 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--primary", default="small.en")
    parser.add_argument("--verifiers", nargs="*", default=["base.en", "tiny.en"])
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.manifest.read_text(encoding="utf-8").splitlines() if line.strip()]
    compute = "float16" if args.device == "cuda" else "int8"
    cache = ROOT / ".labjarvis" / "models" / "faster-whisper"
    vocab = vocabulary()
    results: dict[str, Any] = {"evidence_class": "synthetic TTS speech" if args.manifest == MANIFEST else "see manifest",
                               "device": args.device, "vocabulary": list(vocab), "conditioning": {}, "cross_check": {}}  # fmt: skip
    primary_records: list[dict[str, Any]] = []
    for mode in ("off", "prompt", "hotwords"):
        adapter = FasterWhisperSpeechToText(model_name=args.primary, device=args.device, compute_type=compute,
                                            cache_dir=cache, allow_download=True, conditioning=mode)  # fmt: skip
        records = run(adapter, rows, vocab)
        results["conditioning"][mode] = {"summary": summary(records), "records": records}
        if mode == "off":
            primary_records = records
        print(mode, results["conditioning"][mode]["summary"], flush=True)
    for verifier_name in args.verifiers:
        verifier = FasterWhisperSpeechToText(model_name=verifier_name, device=args.device, compute_type=compute,
                                             cache_dir=cache, allow_download=True)  # fmt: skip
        verified = run(verifier, rows, ())
        errors = caught = alarms = correct = 0
        for primary, second in zip(primary_records, verified, strict=True):
            if not primary["has_entities"]:
                continue
            flagged = compare_transcripts(primary["transcript"], second["transcript"]).canonical is None
            primary_wrong = not (primary["numbers_ok"] and primary["units_ok"])
            if primary_wrong:
                errors += 1
                caught += flagged
            else:
                correct += 1
                alarms += flagged
        extra = statistics.median(r["ms"] for r in verified[1:])
        results["cross_check"][f"{args.primary}->{verifier_name}"] = {
            "primary_entity_errors": errors,
            "caught_by_disagreement": caught,
            "detection_recall": round(caught / errors, 4) if errors else None,
            "primary_entity_correct": correct,
            "nuisance_reasks": alarms,
            "nuisance_rate": round(alarms / correct, 4) if correct else None,
            "median_added_latency_ms": round(extra, 1),
        }
        print(verifier_name, results["cross_check"][f"{args.primary}->{verifier_name}"], flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "asr-conditioning-and-crosscheck.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
