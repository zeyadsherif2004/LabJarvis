"""E1: real faster-whisper transcription of synthetic-speech clips + chained pipeline.

Speech input is machine-synthesised (Windows SAPI); results are evidence about
synthetic speech only and are labelled as such.
"""

from __future__ import annotations

import sys
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (
    RESEARCH_ROOT,
    condition_outcomes_for_command,
    environment_record,
    interpret_command,
    load_manifest,
    verify_production_thresholds,
    write_raw,
)

from labjarvis.domain.commands import validate_and_normalise_command
from labjarvis.evaluation.metrics import character_error_rate, word_error_rate
from labjarvis.infrastructure.model_errors import ModelAdapterError
from labjarvis.infrastructure.fake_models import FakeTextUnderstanding
from labjarvis.infrastructure.real_models import FasterWhisperSpeechToText
from labjarvis.ports.models import CommandContext
from labjarvis.settings import Settings


def main() -> None:
    verify_production_thresholds()
    settings = Settings()
    adapter = FasterWhisperSpeechToText(
        model_name=settings.speech_model_name,
        device=settings.speech_device,
        compute_type=settings.speech_compute_type,
        cache_dir=settings.model_cache_dir / "faster-whisper",
    )
    interpreter = FakeTextUnderstanding()
    clips = load_manifest("speech-clips.jsonl")
    text_samples = {sample["id"]: sample for sample in load_manifest("text-commands.jsonl")}
    records = []
    for clip in clips:
        media_path = (RESEARCH_ROOT / "data" / "manifests" / clip["media_path"]).resolve()
        audio = media_path.read_bytes()
        record: dict[str, object] = {
            "clip_id": clip["id"],
            "text_sample_id": clip["text_sample_id"],
            "voice": clip["voice"],
            "augmentation": clip["augmentation"],
            "duration_seconds": clip["duration_seconds"],
            "expected_text": clip["expected_text"],
        }
        try:
            started = perf_counter()
            result = adapter.transcribe(audio, content_type=clip["content_type"])
            latency_ms = round((perf_counter() - started) * 1000)
            transcript = result.transcript
            record.update(
                status="succeeded",
                transcript=transcript,
                speech_confidence=result.confidence,
                latency_ms=latency_ms,
                real_time_factor=round(latency_ms / 1000 / clip["duration_seconds"], 3),
                wer=word_error_rate(clip["expected_text"], transcript),
                cer=character_error_rate(clip["expected_text"], transcript),
            )
        except ModelAdapterError as exc:
            record.update(status="failed", error_code=exc.code, wer=1.0, cer=1.0)
            records.append(record)
            continue

        # Chained pipeline: ASR transcript -> rule grammar -> validation -> policies.
        sample = text_samples[clip["text_sample_id"]]
        context = CommandContext(
            session_id="research-session",
            session_state=sample.get("session_state", "ACTIVE"),
            active_step_id="research-step",
            active_instruction="Add 250 mL of buffer to the flask.",
        )
        proposal = interpret_command(interpreter, transcript, context)
        canonical = validate_and_normalise_command(proposal)
        record["chained"] = {
            "proposal_intent": proposal.intent,
            "canonical_intent": canonical.intent,
            "canonical_parameters": dict(canonical.parameters),
            "intent_correct": int(canonical.intent == sample["expected_intent"]),
            "conditions": condition_outcomes_for_command(
                proposal, sample, session_state=sample.get("session_state", "ACTIVE")
            ),
        }
        records.append(record)

    payload = {
        "experiment_id": "E1-speech",
        "speech_model": {
            "adapter": adapter.adapter_name,
            "model_name": settings.speech_model_name,
            "device": settings.speech_device,
            "compute_type": settings.speech_compute_type,
        },
        "synthetic_speech_only": True,
        "environment": environment_record(),
        "sample_count": len(records),
        "failure_count": sum(record.get("status") != "succeeded" for record in records),
        "records": records,
    }
    path = write_raw("e1-speech-results.json", payload)
    print(path)


if __name__ == "__main__":
    main()
