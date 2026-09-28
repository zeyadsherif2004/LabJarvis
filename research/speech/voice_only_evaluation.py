"""Behavioural evaluation of voice-only (wake-phrase) confirmation. No paid calls.

Evidence class: SYNTHETIC speech (two Windows TTS voices) through REAL local
models: faster-whisper small.en (primary) and base.en (high-integrity
cross-check), the production ``LabJarvisService`` with voice-only confirmation
on. Own recordings are added automatically when
``research/real_media/private/voice-only.jsonl`` exists (fields: id, media_path,
proposal, expected), and are reported separately as self-recorded.

Each clip answers a *fresh* proposal of the stated kind, so no case depends on
another. Expected outcomes:

* ``commit``  - a correct value-bearing confirmation;
* ``reject``  - an explicit voice rejection (recorded as rejected, nothing
  committed);
* ``no_commit`` - anything that must never commit: bare acknowledgements,
  wrong / second values, unaddressed or background speech, echoes of the
  assistant's own prompts, and confirmations of a mismatching label.

The headline metric is **false commits** (target 0). A refused correct reply
("false refusal") costs one repetition and is reported separately.

    python -m research.speech.voice_only_evaluation [--device cuda]
"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from labjarvis.application.service import LabJarvisService
from labjarvis.domain.models import EntrySource, PendingActionStatus, VerificationDecision
from labjarvis.settings import ModelMode, Settings
from research.real_media.run_chain import build_service, start

ROOT = Path(__file__).resolve().parents[2]
MEDIA = ROOT / "research" / "data" / "media" / "voice-only"
PRIVATE = ROOT / "research" / "real_media" / "private" / "voice-only.jsonl"
OUT = ROOT / "research" / "final_report_evidence" / "hands-free"
VOICES = ["Microsoft David Desktop", "Microsoft Zira Desktop"]
MATCH_LABEL = ROOT / "research" / "data" / "media" / "vision" / "label-001.png"

# (id, spoken text, proposal kind, expected outcome)
CASES: list[tuple[str, str, str, str]] = [
    ("m-valid-1", "Jarvis, confirm 10 millilitres.", "measurement", "commit"),
    ("m-valid-2", "Hey Jarvis, confirm ten millilitres.", "measurement", "commit"),
    ("m-bare-yes", "Jarvis, yes.", "measurement", "no_commit"),
    ("m-bare-confirm", "Jarvis, confirm.", "measurement", "no_commit"),
    ("m-bare-ok", "Jarvis, okay.", "measurement", "no_commit"),
    ("m-bare-doit", "Jarvis, do it.", "measurement", "no_commit"),
    ("m-wrong-value", "Jarvis, confirm 50 millilitres.", "measurement", "no_commit"),
    ("m-wrong-unit", "Jarvis, confirm 10 microlitres.", "measurement", "no_commit"),
    ("m-two-values", "Jarvis, confirm 10 or 50 millilitres.", "measurement", "no_commit"),
    ("m-no-unit", "Jarvis, confirm 10.", "measurement", "no_commit"),
    ("m-no-wake", "Confirm 10 millilitres.", "measurement", "no_commit"),
    ("m-background", "I think the last reading was 10 millilitres.", "measurement", "no_commit"),
    ("m-mention", "Tell Jarvis to confirm 10 millilitres.", "measurement", "no_commit"),
    ("m-echo-prompt", "Say the wake word, then: confirm 10 millilitres.", "measurement", "no_commit"),
    ("m-echo-refusal", "I could not act on that. Nothing was recorded. Say the wake word, then confirm 10 millilitres.", "measurement", "no_commit"),
    ("m-reject", "Jarvis, reject.", "measurement", "reject"),
    ("t-valid", "Jarvis, confirm 10 minutes.", "timer", "commit"),
    ("t-wrong", "Jarvis, confirm 5 minutes.", "timer", "no_commit"),
    ("t-bare", "Jarvis, yes.", "timer", "no_commit"),
    ("s-valid", "Jarvis, confirm step 1.", "step", "commit"),
    ("s-wrong", "Jarvis, confirm step 2.", "step", "no_commit"),
    ("p-valid", "Jarvis, confirm pause.", "pause", "commit"),
    ("p-wrong", "Jarvis, confirm resume.", "pause", "no_commit"),
    ("r-match-valid", "Jarvis, confirm ethanol.", "reagent_match", "commit"),
    ("r-match-wrong", "Jarvis, confirm methanol.", "reagent_match", "no_commit"),
    ("r-match-bare", "Jarvis, yes.", "reagent_match", "no_commit"),
    ("r-mismatch-confirm", "Jarvis, confirm ethanol.", "reagent_mismatch", "no_commit"),
    ("r-mismatch-reject", "Jarvis, reject.", "reagent_mismatch", "reject"),
    ("r-echo", "Label check: match for ethanol. To record it, say the wake word, then confirm ethanol.", "reagent_match", "no_commit"),
]  # fmt: skip

RENDER = r"""
Add-Type -AssemblyName System.Speech
$spec = Get-Content -Raw -Encoding UTF8 $args[0] | ConvertFrom-Json
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
foreach ($item in $spec) {
    $synth.SelectVoice($item.voice)
    $synth.Rate = 0
    $synth.SetOutputToWaveFile($item.path)
    $synth.Speak($item.text)
    $synth.SetOutputToNull()
}
$synth.Dispose()
"""


def render_missing() -> None:
    MEDIA.mkdir(parents=True, exist_ok=True)
    spec = [
        {"voice": voice, "text": text, "path": str(MEDIA / f"{case_id}-v{index}.wav")}
        for case_id, text, _, _ in CASES
        for index, voice in enumerate(VOICES, start=1)
        if not (MEDIA / f"{case_id}-v{index}.wav").exists()
    ]
    if not spec:
        return
    spec_path, script = MEDIA / "_spec.json", MEDIA / "_render.ps1"
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    script.write_text(RENDER, encoding="utf-8")
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
         str(spec_path)],
        check=True,
    )  # fmt: skip
    spec_path.unlink()
    script.unlink()


def mismatch_label() -> Path:
    manifest = ROOT / "research" / "data" / "manifests" / "label-images.jsonl"
    for line in manifest.read_text(encoding="utf-8").splitlines():
        row = json.loads(line) if line.strip() else {}
        if row.get("truth") == "mismatch" and row.get("variant") == "clean":
            return ROOT / "research" / "data" / "media" / "vision" / Path(row["media_path"]).name
    raise SystemExit("no clean mismatch label in the manifest")


def to_reagent_step(service: LabJarvisService, session):  # type: ignore[no-untyped-def]
    version = service.get_protocol_version(session.protocol_version_id)
    while not next(s for s in version.steps if s.id == session.active_step_id).requirements:
        service.invoke_tool(
            session.id, "complete_step", {}, source=EntrySource.TYPED_INPUT,
            model_proposed=False, correlation_id="eval", expected_version=session.version,
        )  # fmt: skip
        session = service.get_session(session.id)
    return session


def run_case(service: LabJarvisService, kind: str, audio: bytes, key: str) -> dict[str, Any]:
    session = start(service)
    if kind.startswith("reagent"):
        session = to_reagent_step(service, session)
        image = (MATCH_LABEL if kind == "reagent_match" else mismatch_label()).read_bytes()
        reading = service.verify_reagent(
            session.id, image, content_type="image/png", correlation_id="eval",
            expected_version=session.version, idempotency_key=f"{key}-label",
        )  # fmt: skip
        result = service.confirm_reagent_verification_by_voice(
            reading.id, audio, content_type="audio/wav", correlation_id="eval",
            expected_version=session.version, idempotency_key=key,
        )  # fmt: skip
        decision = result.verification.decision
        return {
            "label_outcome": reading.outcome.value,
            "decision": result.decision,
            "committed_as": decision.value,
            "committed": decision is VerificationDecision.CONFIRMED,
            "rejected": decision is VerificationDecision.REJECTED,
            "response_text": result.response_text,
        }
    tool, parameters = {
        "measurement": ("record_measurement", {"kind": "volume", "value": "10", "unit": "mL"}),
        "timer": ("set_timer", {"duration_seconds": "600"}),
        "step": ("complete_step", {}),
        "pause": ("pause_session", {}),
    }[kind]
    proposal = service.invoke_tool(
        session.id, tool, parameters, source=EntrySource.MODEL_PROPOSAL, model_proposed=True,
        correlation_id="eval", expected_version=session.version, idempotency_key=f"{key}-p",
    )  # fmt: skip
    assert proposal.pending_action is not None
    spoken = service.confirm_pending_action_by_voice(
        proposal.pending_action.id, audio, content_type="audio/wav", idempotency_key=key,
        correlation_id="eval", via_wake_phrase=True,
    )  # fmt: skip
    with service._uow_factory() as uow:  # authoritative state, not the response
        action = uow.pending_actions.get(proposal.pending_action.id)
        assert action is not None
        status = action.status
    return {
        "decision": spoken.decision,
        "heard": spoken.heard,
        "committed": status is PendingActionStatus.ACCEPTED,
        "rejected": status is PendingActionStatus.REJECTED,
        "response_text": spoken.response_text,
    }


def classify(expected: str, row: dict[str, Any]) -> str:
    if row["committed"]:
        return "correct_commit" if expected == "commit" else "FALSE_COMMIT"
    if expected == "commit":
        return "false_refusal"
    if expected == "reject":
        return "correct_reject" if row["rejected"] else "reject_not_recorded"
    return "correct_refusal"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default="cuda")
    args = parser.parse_args()
    render_missing()
    gpu = args.device == "cuda"
    settings = Settings(
        model_mode=ModelMode.LOCAL,
        speech_device=args.device,
        speech_compute_type="float16" if gpu else "int8",
        vision_gpu=gpu,
        high_integrity_transcription_enabled=True,
        high_integrity_transcription_model_name="base.en",
        hands_free_listening_enabled=True,
        voice_only_confirmation_enabled=True,
        model_cache_dir=ROOT / ".labjarvis" / "models",
        data_dir=ROOT / ".labjarvis",
    )
    service, adapters = build_service(settings)
    service._voice_only_confirmation = True  # the chain builder predates the flag
    rows: list[dict[str, Any]] = []
    try:
        clips = [
            (f"{case_id}-v{i}", MEDIA / f"{case_id}-v{i}.wav", text, kind, expected, "synthetic")
            for case_id, text, kind, expected in CASES
            for i in (1, 2)
        ]
        if PRIVATE.exists():
            for line in PRIVATE.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    item = json.loads(line)
                    clips.append((item["id"], ROOT / item["media_path"], item.get("text", ""),
                                  item["proposal"], item["expected"], "self-recorded"))  # fmt: skip
        for clip_id, path, text, kind, expected, evidence in clips:
            outcome = run_case(service, kind, path.read_bytes(), f"voice-eval-{clip_id}")
            rows.append({"id": clip_id, "text": text, "proposal": kind, "expected": expected,
                         "evidence_class": evidence, **outcome,
                         "result": classify(expected, outcome)})  # fmt: skip
            print(f"{clip_id:28} {rows[-1]['result']:18} heard={outcome.get('heard', '')!r}")
    finally:
        adapters.close()

    summary: dict[str, Any] = {}
    for evidence in sorted({row["evidence_class"] for row in rows}):
        subset = [row for row in rows if row["evidence_class"] == evidence]
        counts = Counter(row["result"] for row in subset)
        summary[evidence] = {
            "clips": len(subset),
            "results": dict(sorted(counts.items())),
            "false_commits": counts.get("FALSE_COMMIT", 0),
            "should_commit": sum(row["expected"] == "commit" for row in subset),
            "correct_commits": counts.get("correct_commit", 0),
            "must_not_commit": sum(row["expected"] != "commit" for row in subset),
        }
    payload = {
        "created_at": datetime.now(UTC).isoformat(),
        "evidence_class": "synthetic Windows TTS speech through real local models "
        "(faster-whisper small.en + base.en cross-check); self-recorded clips reported separately",
        "models": {"primary": settings.speech_model_name, "verification": "base.en",
                   "device": args.device},  # fmt: skip
        "summary": summary,
        "rows": rows,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "voice-only-confirmation.json").write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
