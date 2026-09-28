"""Hands-free bench workflow evaluation (ADR 0018). Local models only; no paid calls.

Evidence class: SYNTHETIC speech (two Windows TTS voices) through REAL local
models - faster-whisper small.en (primary) with base.en as the high-integrity
cross-check, the rule grammar + qwen2.5:3b cascade (Ollama), and EasyOCR on
rendered label images - driving the production ``LabJarvisService`` with the
hands-free options on. Own recordings are added automatically when
``research/real_media/private/hands-free.jsonl`` exists (fields: id,
media_path, part = "confirmation", intent, reply, expected) and are reported
separately as self-recorded.

Parts:

A. voice-only confirmation of every new confirmable intent (skip, correct,
   cancel/pause timer, start/resume session, opt-in observation): the spoken
   command must create the right proposal, then one reply per case - valid,
   bare acknowledgements, wrong and second values, unaddressed speech, an echo
   of the prompt, and a rejection. Headline: false commits (target 0).
B. spoken protocol questions, answerable and unanswerable (never guessed).
C. read-only queries, reagent selection, alarm acknowledgement and interface
   controls.
D. a scripted full-protocol run per voice through one session: touches
   (on-screen fallbacks needed), commands, re-asks, end-to-end time, failures.

    python -m research.speech.hands_free_evaluation [--device cuda]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import count
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
MEDIA = ROOT / "research" / "data" / "media" / "hands-free"
PRIVATE = ROOT / "research" / "real_media" / "private" / "hands-free.jsonl"
OUT = ROOT / "research" / "final_report_evidence" / "hands-free"
VOICES = ["Microsoft David Desktop", "Microsoft Zira Desktop"]
SAMPLE = ROOT / "sample_data" / "protocols" / "development-only-ethanol-protocol.docx"
MATCH_LABEL = ROOT / "research" / "data" / "media" / "vision" / "label-001.png"
AUDIO = "audio/wav"

# Never inherit a hosted configuration from the shell: this evaluation is local.
for _name in [name for name in os.environ if name.upper().startswith("LABJARVIS_")]:
    del os.environ[_name]

from labjarvis.application.errors import ApplicationError  # noqa: E402
from labjarvis.application.service import LabJarvisService  # noqa: E402
from labjarvis.domain.live_scan import ProbeReading, ScanRule, ScanStatus, evaluate_scan  # noqa: E402
from labjarvis.domain.models import (  # noqa: E402
    EntrySource,
    PendingActionStatus,
    SessionState,
    StepState,
    TimerState,
    VerificationDecision,
)
from labjarvis.infrastructure.database import (  # noqa: E402
    SqlAlchemyUnitOfWork,
    create_session_factory,
)
from labjarvis.infrastructure.documents import DOCX_MIME, DocumentExtractor  # noqa: E402
from labjarvis.infrastructure.inference import DirectInferenceRunner  # noqa: E402
from labjarvis.infrastructure.media_storage import DiscardRawMediaStore  # noqa: E402
from labjarvis.infrastructure.model_factory import ModelAdapters, build_model_adapters  # noqa: E402
from labjarvis.settings import ModelMode, Settings  # noqa: E402

# ----------------------------------------------------------------- utterances

COMMON_REPLIES = ("bare_yes", "bare_confirm", "unaddressed", "echo", "reject")


@dataclass(frozen=True)
class IntentCase:
    name: str
    setup: str
    command: str
    expected_intent: str
    valid: str
    wrong: str
    second: str


INTENT_CASES = (
    IntentCase("skip", "active", "Jarvis, skip this step because the reagent is unavailable.",
               "SKIP_STEP_WITH_REASON", "confirm skip step 1", "confirm skip step 2",
               "confirm skip step 1 and step 2"),
    IntentCase("correct", "measured", "Jarvis, correct the last measurement to 12 millilitres.",
               "CORRECT_PREVIOUS_ENTRY", "confirm 12 millilitres", "confirm 21 millilitres",
               "confirm 12 or 21 millilitres"),
    IntentCase("cancel_timer", "timer", "Jarvis, cancel the 10 minute timer.", "CANCEL_TIMER",
               "confirm cancel 10 minute timer", "confirm cancel 5 minute timer",
               "confirm cancel 10 minute and 5 minute timer"),
    IntentCase("pause_timer", "timer", "Jarvis, pause the timer.", "PAUSE_TIMER",
               "confirm pause timer", "confirm resume timer", "confirm pause session"),
    IntentCase("start_session", "ready", "Jarvis, start the session.", "START_SESSION",
               "confirm start session", "confirm end session",
               "confirm start session and pause"),
    IntentCase("resume_session", "paused", "Jarvis, resume the session.", "RESUME_SESSION",
               "confirm resume session", "confirm pause session",
               "confirm resume session and pause"),
    IntentCase("observation", "active", "Jarvis, record observation the solution turned cloudy.",
               "RECORD_OBSERVATION", "confirm observation",
               "confirm observation the solution is clear", "confirm observation number 2"),
)  # fmt: skip


def reply_text(case: IntentCase, kind: str) -> str:
    return {
        "valid": f"Jarvis, {case.valid}.",
        "wrong": f"Jarvis, {case.wrong}.",
        "second": f"Jarvis, {case.second}.",
        "bare_yes": "Jarvis, yes.",
        "bare_confirm": "Jarvis, confirm.",
        "unaddressed": f"{case.valid.capitalize()}.",
        "echo": f"Say the wake word, then: {case.valid}.",
        "reject": "Jarvis, reject.",
    }[kind]


def expected_outcome(kind: str) -> str:
    return {"valid": "commit", "reject": "reject"}.get(kind, "no_commit")


QUESTIONS: tuple[tuple[str, str, int | None], ...] = (
    ("q-incubate", "Jarvis, how long should I incubate the synthetic sample?", 2),
    ("q-ethanol", "Jarvis, how much ethanol do I add?", 1),
    ("q-vessel", "Jarvis, what goes into the demonstration vessel?", 1),
    ("q-proceed", "Jarvis, when can I proceed?", 3),
    ("q-cap", "Jarvis, what has to be sealed before I proceed?", 3),
    ("q-centrifuge", "Jarvis, what speed should the centrifuge run at?", None),
    ("q-ph", "Jarvis, what is the pH of the buffer?", None),
    ("q-spin", "Jarvis, how long do I spin the tubes?", None),
    ("q-boiling", "Jarvis, what is the boiling point of ethanol?", None),
)

QUERIES: tuple[tuple[str, str, str, str], ...] = (
    # id, text, setup, expectation (intent or regex on the response)
    ("c-left", "Jarvis, how long is left?", "timer", r"left"),
    ("c-reagents", "Jarvis, what reagents do I need?", "active", r"ethanol"),
    ("c-where", "Jarvis, where am I?", "active", r"step 1 of 3"),
    ("c-last", "Jarvis, what did I just record?", "measured", r"volume 10 millilitres"),
    ("c-check-name", "Jarvis, check the ethanol.", "active", "requirement"),
    ("c-alarm-stop", "Jarvis, stop the alarm.", "elapsed", "ACKNOWLEDGE_ALARM"),
    ("c-alarm-done", "Jarvis, timer done.", "elapsed", "ACKNOWLEDGE_ALARM"),
    ("c-stop-listening", "Jarvis, stop listening.", "active", "STOP_LISTENING"),
    ("c-start-listening", "Jarvis, start listening.", "active", "START_LISTENING"),
    ("c-mute", "Jarvis, mute.", "active", "MUTE_SPEECH"),
    ("c-unmute", "Jarvis, unmute.", "active", "UNMUTE_SPEECH"),
    ("c-again", "Jarvis, say that again.", "active", "REPEAT_LAST_SPOKEN"),
    ("c-stop", "Jarvis, stop.", "active", "STOP_SPEAKING"),
)

# The scripted protocol run: (utterance, expectation name).
SCRIPT: tuple[tuple[str, str], ...] = (
    ("Jarvis, start the session.", "proposal:START_SESSION"),
    ("Jarvis, confirm start session.", "state:ACTIVE"),
    ("Jarvis, how long should I incubate the synthetic sample?", "say:Step 2 says"),
    ("Jarvis, record the volume as 10 millilitres.", "proposal:RECORD_MEASUREMENT"),
    ("Jarvis, confirm 10 millilitres.", "entry:measurement"),
    ("Jarvis, check the ethanol.", "scan:match"),
    ("Jarvis, confirm ethanol.", "reading:CONFIRMED"),
    ("Jarvis, check the ethanol.", "scan:mismatch"),
    ("Jarvis, confirm ethanol.", "reading:PENDING"),
    ("Jarvis, reject.", "reading:REJECTED"),
    ("Jarvis, set a timer for 3 seconds.", "proposal:SET_TIMER"),
    ("Jarvis, confirm 3 seconds.", "timer:RUNNING"),
    ("Jarvis, stop the alarm.", "alarm:off"),
    ("Jarvis, set a timer for 10 minutes.", "proposal:SET_TIMER"),
    ("Jarvis, confirm 10 minutes.", "timer:RUNNING"),
    ("Jarvis, how long is left?", "say:left"),
    ("Jarvis, pause the timer.", "proposal:PAUSE_TIMER"),
    ("Jarvis, confirm pause timer.", "timer:PAUSED"),
    ("Jarvis, cancel the 10 minute timer.", "proposal:CANCEL_TIMER"),
    ("Jarvis, confirm cancel 10 minute timer.", "timer:CANCELLED"),
    ("Jarvis, complete the step.", "proposal:COMPLETE_STEP"),
    ("Jarvis, confirm step 1.", "step:1:COMPLETED"),
    ("Jarvis, skip this step because the incubator is unavailable.", "proposal:SKIP_STEP_WITH_REASON"),
    ("Jarvis, confirm skip step 2.", "step:2:SKIPPED_WITH_REASON"),
    ("Jarvis, correct the last measurement to 12 millilitres.", "proposal:CORRECT_PREVIOUS_ENTRY"),
    ("Jarvis, confirm 12 millilitres.", "entry:correction"),
    ("Jarvis, end the session.", "proposal:END_SESSION"),
    ("Jarvis, confirm end session.", "state:ABORTED"),
)  # fmt: skip
#: What an operator says instead when a phrasing is not understood (a re-ask).
#: "end" is often heard as "and"; "finish" is the documented alternative.
REPHRASE = {
    "Jarvis, end the session.": "Jarvis, finish the session.",
    "Jarvis, confirm end session.": "Jarvis, confirm finish session.",
}


def all_texts() -> dict[str, str]:
    texts: dict[str, str] = {}
    for case in INTENT_CASES:
        texts[f"a-{case.name}-command"] = case.command
        for kind in ("valid", "wrong", "second", *COMMON_REPLIES):
            texts[f"a-{case.name}-{kind}"] = reply_text(case, kind)
    for question_id, text, _ in QUESTIONS:
        texts[question_id] = text
    for query_id, text, _, _ in QUERIES:
        texts[query_id] = text
    for index, (text, _) in enumerate(SCRIPT, start=1):
        texts[f"d-{index:02d}"] = text
    for index, text in enumerate(REPHRASE.values(), start=1):
        texts[f"d-rephrase-{index}"] = text
    return texts


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")[:60]


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


def clip_path(text: str, voice_index: int) -> Path:
    return MEDIA / f"{slug(text)}-v{voice_index}.wav"


def render_missing() -> None:
    MEDIA.mkdir(parents=True, exist_ok=True)
    unique = sorted(set(all_texts().values()))
    spec = [
        {"voice": voice, "text": text, "path": str(clip_path(text, index))}
        for text in unique
        for index, voice in enumerate(VOICES, start=1)
        if not clip_path(text, index).exists()
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


# ------------------------------------------------------------------ service


def make_service(settings: Settings, adapters: ModelAdapters) -> LabJarvisService:
    factory = create_session_factory("sqlite+pysqlite://", create_schema=True)
    ids = count(1)
    return LabJarvisService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(factory),
        document_extractor=DocumentExtractor(max_bytes=10_000_000, max_pages=50),
        speech=adapters.speech,
        verification_speech=adapters.verification_speech,
        text=adapters.text,
        labels=adapters.labels,
        inference_runner=DirectInferenceRunner(),
        clock=lambda: datetime.now(UTC),
        new_id=lambda: f"hf-{next(ids)}",
        command_confidence_threshold=settings.command_confidence_threshold,
        reagent_match_threshold=settings.reagent_match_threshold,
        reagent_uncertain_threshold=settings.reagent_uncertain_threshold,
        raw_media_store=DiscardRawMediaStore(),
        retain_transcripts=True,
        wake_phrase=settings.wake_phrase,
        voice_only_confirmation=True,
        live_label_scan=True,
        voice_observations=True,
    )


keys = count(1)


def open_session(service: LabJarvisService, setup: str) -> str:
    imported = service.import_protocol(SAMPLE.name, DOCX_MIME, SAMPLE.read_bytes())
    approved = service.approve_protocol(imported.version.id, expected_revision=0)
    session = service.create_session(approved.id)
    if setup == "ready":
        return session.id
    session = service.start_session(session.id, expected_version=0)

    def tool(name: str, parameters: dict[str, str], proposed: bool = False) -> None:
        current = service.get_session(session.id)
        result = service.invoke_tool(
            session.id, name, parameters, source=EntrySource.TYPED_INPUT,
            model_proposed=proposed, correlation_id="setup", expected_version=current.version,
            idempotency_key=f"setup-{next(keys)}",
        )  # fmt: skip
        if result.pending_action is not None:
            service.confirm_pending_action(result.pending_action.id, accepted=True, correlation_id="setup")

    if setup == "measured":
        tool("record_measurement", {"kind": "volume", "value": "10", "unit": "mL"}, proposed=True)
    elif setup == "timer":
        tool("set_timer", {"duration_seconds": "600"})
    elif setup == "elapsed":
        tool("set_timer", {"duration_seconds": "1"})
        time.sleep(1.3)
        service.list_timers(session.id)
    elif setup == "paused":
        tool("pause_session", {})
    return session.id


def hear(service: LabJarvisService, session_id: str, audio: bytes):  # type: ignore[no-untyped-def]
    session = service.get_session(session_id)
    return service.handle_audio_command(
        session_id, audio, content_type=AUDIO, idempotency_key=f"hf-{next(keys)}",
        expected_version=session.version, correlation_id="eval", require_wake_phrase=True,
    )  # fmt: skip


def answer(service: LabJarvisService, action_id: str, audio: bytes):  # type: ignore[no-untyped-def]
    return service.confirm_pending_action_by_voice(
        action_id, audio, content_type=AUDIO, idempotency_key=f"hf-{next(keys)}",
        correlation_id="eval", via_wake_phrase=True,
    )  # fmt: skip


def action_status(service: LabJarvisService, action_id: str) -> PendingActionStatus:
    with service._uow_factory() as uow:
        action = uow.pending_actions.get(action_id)
        assert action is not None
        return action.status


# ------------------------------------------------------------------- part A


def part_a(settings: Settings, adapters: ModelAdapters) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case in INTENT_CASES:
        for kind in ("valid", "wrong", "second", *COMMON_REPLIES):
            for voice in (1, 2):
                service = make_service(settings, adapters)
                session_id = open_session(service, case.setup)
                command = hear(service, session_id, clip_path(case.command, voice).read_bytes())
                row: dict[str, Any] = {
                    "intent": case.name, "reply_kind": kind, "voice": voice,
                    "command_heard": command.transcript, "command_intent": command.parsed_command.intent,
                    "expected": expected_outcome(kind), "evidence_class": "synthetic",
                }  # fmt: skip
                if command.pending_action is None or command.pending_action.intent != case.expected_intent:
                    row.update(result="command_not_understood", committed=False,
                               response=command.response_text)  # fmt: skip
                    rows.append(row)
                    print(f"A {case.name:15} {kind:12} v{voice} command_not_understood heard={command.transcript!r}")
                    continue
                action_id = command.pending_action.id
                try:
                    result = answer(service, action_id, clip_path(reply_text(case, kind), voice).read_bytes())
                    heard, response = result.heard, result.response_text
                except ApplicationError as exc:
                    heard, response = "", f"refused: {exc}"
                status = action_status(service, action_id)
                committed = status is PendingActionStatus.ACCEPTED
                rejected = status is PendingActionStatus.REJECTED
                row.update(heard=heard, response=response, committed=committed, rejected=rejected)
                row["result"] = classify(row["expected"], committed, rejected)
                rows.append(row)
                print(f"A {case.name:15} {kind:12} v{voice} {row['result']:18} heard={heard!r}")
    return rows


def part_a_self_recorded(settings: Settings, adapters: ModelAdapters) -> list[dict[str, Any]]:
    """Own recordings of replies (``hands-free.jsonl``), reported as self-recorded.

    The command is the synthetic clip (voice 1); the reply is the recording.
    """

    if not PRIVATE.exists():
        return []
    cases = {case.name: case for case in INTENT_CASES}
    rows: list[dict[str, Any]] = []
    for line in PRIVATE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        case = cases[item["intent"]]
        service = make_service(settings, adapters)
        session_id = open_session(service, case.setup)
        command = hear(service, session_id, clip_path(case.command, 1).read_bytes())
        row: dict[str, Any] = {"id": item["id"], "intent": case.name, "reply_kind": item["reply"],
                               "expected": item["expected"], "evidence_class": "self-recorded"}  # fmt: skip
        if command.pending_action is None:
            rows.append({**row, "result": "command_not_understood", "committed": False})
            continue
        action_id = command.pending_action.id
        try:
            answer(service, action_id, (ROOT / item["media_path"]).read_bytes())
        except ApplicationError:
            pass
        status = action_status(service, action_id)
        committed = status is PendingActionStatus.ACCEPTED
        rows.append({**row, "committed": committed,
                     "result": classify(item["expected"], committed,
                                        status is PendingActionStatus.REJECTED)})  # fmt: skip
    return rows


def classify(expected: str, committed: bool, rejected: bool) -> str:
    if committed:
        return "correct_commit" if expected == "commit" else "FALSE_COMMIT"
    if expected == "commit":
        return "false_refusal"
    if expected == "reject":
        return "correct_reject" if rejected else "reject_not_recorded"
    return "correct_refusal"


# ------------------------------------------------------------------- part B/C


def part_b(settings: Settings, adapters: ModelAdapters) -> list[dict[str, Any]]:
    rows = []
    for question_id, text, step in QUESTIONS:
        for voice in (1, 2):
            service = make_service(settings, adapters)
            session_id = open_session(service, "active")
            result = hear(service, session_id, clip_path(text, voice).read_bytes())
            response = result.response_text
            if step is None:
                correct = response == "I couldn't find that in the approved protocol."
            else:
                correct = response.startswith(f"Step {step} says:")
            rows.append({
                "id": question_id, "voice": voice, "answerable": step is not None,
                "expected_step": step, "heard": result.transcript, "intent": result.parsed_command.intent,
                "response": response, "correct": correct, "evidence_class": "synthetic",
            })  # fmt: skip
            print(f"B {question_id:14} v{voice} {'ok' if correct else 'WRONG':5} {response[:70]!r}")
    return rows


def part_c(settings: Settings, adapters: ModelAdapters) -> list[dict[str, Any]]:
    rows = []
    for query_id, text, setup, expectation in QUERIES:
        for voice in (1, 2):
            service = make_service(settings, adapters)
            session_id = open_session(service, setup)
            result = hear(service, session_id, clip_path(text, voice).read_bytes())
            intent = result.parsed_command.intent
            if expectation == "requirement":
                ok = "requirement_id" in result.parsed_command.parameters
            elif expectation.isupper():
                ok = intent == expectation
            else:
                ok = re.search(expectation, result.response_text, re.I) is not None
            alarm_off = None
            if setup == "elapsed":
                alarm_off = not any(view.alarm_active for view in service.list_timers(session_id))
                ok = ok and alarm_off
            record = service.get_session_review(session_id)
            rows.append({
                "id": query_id, "voice": voice, "heard": result.transcript, "intent": intent,
                "response": result.response_text, "ok": ok, "alarm_acknowledged": alarm_off,
                "pending_created": result.pending_action is not None,
                "evidence_class": "synthetic",
                "notebook_entries": len(record.entries),
            })  # fmt: skip
            print(f"C {query_id:18} v{voice} {'ok' if ok else 'FAIL':5} {intent:24} heard={result.transcript!r}")
    return rows


# ------------------------------------------------------------------- part D


@dataclass
class Run:
    voice: int
    commands: int = 0
    reasks: int = 0
    touches: int = 0
    failures: list[str] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)


def live_scan(service: LabJarvisService, session_id: str, requirement_id: str, image: Path):  # type: ignore[no-untyped-def]
    rule = ScanRule()
    readings: list[ProbeReading] = []
    started = time.perf_counter()
    for _ in range(rule.max_frames):
        probe = service.probe_reagent_label(
            session_id, image.read_bytes(), content_type="image/png", requirement_id=requirement_id
        )
        readings.append(ProbeReading(probe.outcome, probe.observed_normalised))
        state = evaluate_scan(
            readings, elapsed_ms=round((time.perf_counter() - started) * 1000), rule=rule
        )
        if state.status is ScanStatus.STABLE:
            session = service.get_session(session_id)
            frames = [image.read_bytes()] * rule.required_consecutive
            return service.verify_reagent(
                session_id, frames[0], content_type="image/png", correlation_id="eval",
                expected_version=session.version, idempotency_key=f"scan-{next(keys)}",
                requirement_id=requirement_id, additional_frames=frames[1:],
                capture_metadata={"mode": "live_scan", "scan_rule": "stable-consecutive-v1",
                                  "scan_frames_examined": str(state.frames_examined)},
            )  # fmt: skip
        if state.status is ScanStatus.TIMEOUT:
            return None
    return None


def part_d(settings: Settings, adapters: ModelAdapters, voice: int) -> Run:
    run = Run(voice=voice)
    service = make_service(settings, adapters)
    session_id = open_session(service, "ready")
    reading_id: str | None = None
    scan_images = iter([MATCH_LABEL, mismatch_label()])
    started = time.perf_counter()
    for index, (text, expectation) in enumerate(SCRIPT, start=1):
        if expectation == "alarm:off":
            time.sleep(3.5)  # let the 3 second timer elapse; the alarm is then sounding
            assert any(view.alarm_active for view in service.list_timers(session_id))
        attempts = 0
        satisfied = False
        response = ""
        heard = ""
        while attempts < 2 and not satisfied:
            # A re-ask: the same words in the other voice, or the documented
            # alternative phrasing when there is one.
            said = REPHRASE.get(text, text) if attempts else text
            clip_voice = voice if attempts == 0 or said != text else 3 - voice
            attempts += 1
            run.commands += 1
            if attempts == 2:
                run.reasks += 1
            audio = clip_path(said, clip_voice).read_bytes()
            snapshot = service.get_session_snapshot(session_id)
            try:
                if snapshot.pending_action is not None:
                    result = answer(service, snapshot.pending_action.id, audio)
                    heard, response = result.heard, result.response_text
                elif snapshot.pending_verification is not None:
                    decision = service.confirm_reagent_verification_by_voice(
                        snapshot.pending_verification.id, audio, content_type=AUDIO,
                        correlation_id="eval", expected_version=snapshot.session.version,
                        idempotency_key=f"hf-{next(keys)}",
                    )  # fmt: skip
                    response = decision.response_text
                    reading_id = decision.verification.id
                else:
                    command = hear(service, session_id, audio)
                    heard, response = command.transcript, command.response_text
                    if expectation.startswith("scan:"):
                        requirement_id = command.parsed_command.parameters.get("requirement_id")
                        if requirement_id is not None:
                            reading = live_scan(service, session_id, requirement_id, next(scan_images))
                            reading_id = reading.id if reading is not None else None
                            response += f" | scan: {reading.outcome.value if reading else 'none'}"
            except ApplicationError as exc:
                response = f"refused: {exc}"
            satisfied = check(service, session_id, expectation, response, reading_id)
        if not satisfied:
            run.failures.append(f"{index:02d} {text} -> {response}")
            run.touches += fallback(service, session_id, expectation)
        run.steps.append({"step": index, "text": text, "expectation": expectation,
                          "heard": heard, "response": response, "attempts": attempts,
                          "satisfied": satisfied})  # fmt: skip
        print(f"D v{voice} {index:02d} {'ok' if satisfied else 'FAIL':4} tries={attempts} {text}")
    run_seconds = time.perf_counter() - started
    run.steps.append({"end_to_end_seconds": round(run_seconds, 1)})
    return run


def check(service: LabJarvisService, session_id: str, expectation: str, response: str,
          reading_id: str | None) -> bool:  # fmt: skip
    kind, _, value = expectation.partition(":")
    snapshot = service.get_session_snapshot(session_id)
    if kind == "proposal":
        return snapshot.pending_action is not None and snapshot.pending_action.intent == value
    if kind == "state":
        return snapshot.session.state is SessionState(value) and snapshot.pending_action is None
    if kind == "say":
        return value in response
    if kind == "entry":
        return any(entry.entry_type == value for entry in snapshot.entries) and snapshot.pending_action is None
    if kind == "scan":
        wanted = "MATCH" if value == "match" else "MISMATCH"
        return snapshot.pending_verification is not None and snapshot.pending_verification.outcome.value == wanted
    if kind == "reading":
        if reading_id is None:
            return False
        with service._uow_factory() as uow:
            reading = uow.verifications.get(reading_id)
        return reading is not None and reading.decision is VerificationDecision(value)
    if kind == "timer":
        latest = snapshot.timers[-1].timer if snapshot.timers else None
        return latest is not None and latest.state is TimerState(value) and snapshot.pending_action is None
    if kind == "alarm":
        return not any(view.alarm_active for view in snapshot.timers)
    if kind == "step":
        number, state = value.split(":")
        step = next(s for s in snapshot.version.steps if s.number == int(number))
        progress = next(s for s in snapshot.session.steps if s.step_id == step.id)
        return progress.state is StepState(state)
    raise ValueError(expectation)


def fallback(service: LabJarvisService, session_id: str, expectation: str) -> int:
    """Do on screen what voice failed to do, counting it as a touch."""

    snapshot = service.get_session_snapshot(session_id)
    kind, _, _ = expectation.partition(":")
    if snapshot.pending_action is not None and kind != "proposal":
        service.confirm_pending_action(snapshot.pending_action.id, accepted=True, correlation_id="touch")
        return 1
    if snapshot.pending_verification is not None and kind == "reading":
        service.confirm_reagent_verification(
            snapshot.pending_verification.id, accepted=False, corrected_observed_label=None,
            correlation_id="touch", expected_version=snapshot.session.version,
            idempotency_key=f"touch-{next(keys)}",
        )  # fmt: skip
        return 1
    return 1


# ------------------------------------------------------------------- report


def summarise_a(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(row["result"] for row in rows)
    per_intent: dict[str, Any] = {}
    for case in INTENT_CASES:
        subset = [row for row in rows if row["intent"] == case.name]
        understood = [row for row in subset if row["result"] != "command_not_understood"]
        valid = [row for row in subset if row["reply_kind"] == "valid"]
        per_intent[case.name] = {
            "clips": len(subset),
            "command_understood": f"{len(understood)}/{len(subset)}",
            "valid_committed": f"{sum(r['result'] == 'correct_commit' for r in valid)}/{len(valid)}",
            "false_commits": sum(r["result"] == "FALSE_COMMIT" for r in subset),
        }
    return {
        "clips": len(rows),
        "results": dict(sorted(counts.items())),
        "false_commits": counts.get("FALSE_COMMIT", 0),
        "correct_commits": counts.get("correct_commit", 0),
        "false_refusals": counts.get("false_refusal", 0),
        "commands_not_understood": counts.get("command_not_understood", 0),
        "per_intent": per_intent,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--parts", default="abcd")
    args = parser.parse_args()
    render_missing()
    gpu = args.device == "cuda"
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        model_mode=ModelMode.LOCAL,
        speech_device=args.device,
        speech_compute_type="float16" if gpu else "int8",
        vision_gpu=gpu,
        hands_free_profile=True,
        voice_observations_enabled=True,
        model_cache_dir=ROOT / ".labjarvis" / "models",
        data_dir=ROOT / ".labjarvis",
    )
    adapters = build_model_adapters(settings)
    payload: dict[str, Any] = {
        "created_at": datetime.now(UTC).isoformat(),
        "evidence_class": (
            "synthetic Windows TTS speech (2 voices) through real local models: faster-whisper "
            "small.en + base.en cross-check, rule grammar v2 + qwen2.5:3b cascade, EasyOCR on "
            "rendered labels; production LabJarvisService with the hands-free profile"
        ),
        "models": {
            "speech": settings.speech_model_name,
            "speech_cross_check": settings.high_integrity_transcription_model_name,
            "text": f"{settings.effective_text_interpreter().value} + {settings.text_model_name}",
            "vision": f"{settings.vision_model_name} ({'gpu' if gpu else 'cpu'})",
            "device": args.device,
        },
        "self_recorded": "none found" if not PRIVATE.exists() else "see self_recorded rows",
    }
    try:
        if "a" in args.parts:
            rows = part_a(settings, adapters)
            payload["part_a_confirmation"] = {"summary": summarise_a(rows), "rows": rows}
            own = part_a_self_recorded(settings, adapters)
            if own:
                payload["part_a_self_recorded"] = {"summary": summarise_a(own), "rows": own}
        if "b" in args.parts:
            rows = part_b(settings, adapters)
            answerable = [row for row in rows if row["answerable"]]
            unanswerable = [row for row in rows if not row["answerable"]]
            payload["part_b_questions"] = {
                "summary": {
                    "answerable_correct": f"{sum(r['correct'] for r in answerable)}/{len(answerable)}",
                    "unanswerable_abstained": f"{sum(r['correct'] for r in unanswerable)}/{len(unanswerable)}",
                    "wrong_answers_given": sum(
                        not r["correct"] and r["response"].startswith(("Step", "The approved"))
                        for r in rows
                    ),
                },
                "rows": rows,
            }
        if "c" in args.parts:
            rows = part_c(settings, adapters)
            alarm = [row for row in rows if row["alarm_acknowledged"] is not None]
            payload["part_c_queries_and_controls"] = {
                "summary": {
                    "ok": f"{sum(r['ok'] for r in rows)}/{len(rows)}",
                    "alarm_acknowledged": f"{sum(bool(r['alarm_acknowledged']) for r in alarm)}/{len(alarm)}",
                    "proposals_created_by_read_only_or_control": sum(r["pending_created"] for r in rows),
                },
                "rows": rows,
            }
        if "d" in args.parts:
            runs = [part_d(settings, adapters, voice) for voice in (1, 2)]
            payload["part_d_full_protocol"] = [
                {
                    "voice": VOICES[run.voice - 1],
                    "commands": run.commands,
                    "reasks": run.reasks,
                    "touches_after_start": run.touches,
                    "failures": run.failures,
                    "end_to_end_seconds": run.steps[-1]["end_to_end_seconds"],
                    "steps": run.steps[:-1],
                }
                for run in runs
            ]
    finally:
        adapters.close()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "hands-free-evaluation.json").write_text(json.dumps(payload, indent=1), encoding="utf-8")
    brief = {key: value["summary"] if isinstance(value, dict) and "summary" in value else value
             for key, value in payload.items() if key.startswith("part_")}  # fmt: skip
    if "part_d_full_protocol" in brief:
        brief["part_d_full_protocol"] = [
            {k: v for k, v in run.items() if k != "steps"} for run in payload["part_d_full_protocol"]
        ]
    print(json.dumps(brief, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
