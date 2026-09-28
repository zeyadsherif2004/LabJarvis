"""Hands-free bench workflow (ADR 0018): every new voice path against the invariants.

Invariants under test, always asserted on authoritative state rather than on
wording:

* listeners and models only propose; nothing is recorded, changed or deleted
  without an explicit confirmation;
* a voice-only confirmation must start with the wake phrase and restate the
  content being confirmed; a bare acknowledgement, a wrong, second or
  conflicting value, unaddressed speech, an echo of LabJarvis' own prompt, and
  a stale or replaced proposal never commit;
* spoken prompts never contain the wake phrase;
* interface controls (stop listening, mute, stop the alarm, ...) never touch
  the experimental record.

Evidence class: fake/fixture (deterministic "FAKE:" speech, rule grammar).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from itertools import count
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from labjarvis.api.app import create_app
from labjarvis.application.errors import ApplicationError
from labjarvis.application.service import LabJarvisService
from labjarvis.domain.models import (
    EntrySource,
    PendingActionStatus,
    ProtocolRequirement,
    SessionState,
    StepState,
    TimerState,
)
from labjarvis.domain.voice_controls import ListenMode
from labjarvis.infrastructure.database import SqlAlchemyUnitOfWork, create_session_factory
from labjarvis.infrastructure.documents import DOCX_MIME, DocumentExtractor
from labjarvis.infrastructure.fake_models import (
    FakeLabelRecognition,
    FakeSpeechToText,
    FakeTextUnderstanding,
)
from labjarvis.infrastructure.hands_free import HandsFreeProfileError, hands_free_status, preflight
from labjarvis.infrastructure.inference import DirectInferenceRunner
from labjarvis.infrastructure.media_storage import DiscardRawMediaStore
from labjarvis.ports.models import AdapterReadiness
from labjarvis.settings import Settings

SAMPLE = (
    Path(__file__).parents[4]
    / "sample_data"
    / "protocols"
    / "development-only-ethanol-protocol.docx"
)
AUDIO = "audio/wav"
keys = count(1)


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 27, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += timedelta(seconds=seconds)


@dataclass
class Bench:
    service: LabJarvisService
    session_id: str
    clock: Clock

    # ----------------------------------------------------------------- speech

    def hear(self, words: str, mode: ListenMode = ListenMode.COMMAND):  # type: ignore[no-untyped-def]
        session = self.service.get_session(self.session_id)
        return self.service.handle_audio_command(
            self.session_id,
            f"FAKE:{words}".encode(),
            content_type=AUDIO,
            idempotency_key=f"cmd-{next(keys)}",
            expected_version=session.version,
            correlation_id="hear",
            require_wake_phrase=True,
            listen_mode=mode,
        )

    def answer(self, action_id: str, words: str, *, expected_version: int | None = None):  # type: ignore[no-untyped-def]
        return self.service.confirm_pending_action_by_voice(
            action_id,
            f"FAKE:{words}".encode(),
            content_type=AUDIO,
            idempotency_key=f"voice-{next(keys)}",
            correlation_id="answer",
            via_wake_phrase=True,
            expected_session_version=expected_version,
        )

    def propose(self, words: str) -> str:
        proposal = self.hear(words)
        assert proposal.pending_action is not None, proposal.response_text
        # A spoken prompt never contains the wake phrase itself.
        assert "jarvis" not in proposal.response_text.casefold()
        return proposal.pending_action.id

    # ------------------------------------------------------------------ state

    def status(self, action_id: str) -> PendingActionStatus:
        with self.service._uow_factory() as uow:
            action = uow.pending_actions.get(action_id)
            assert action is not None
            return action.status

    def entries(self, entry_type: str) -> list:  # type: ignore[type-arg]
        return [
            entry
            for entry in self.service.list_notebook(self.session_id)
            if entry.entry_type == entry_type
        ]

    def timers(self):  # type: ignore[no-untyped-def]
        return self.service.list_timers(self.session_id)


def build_bench(
    *,
    start: bool = True,
    voice_observations: bool = False,
    extra_requirements: tuple[ProtocolRequirement, ...] = (),
) -> Bench:
    clock = Clock()
    factory = create_session_factory("sqlite+pysqlite://", create_schema=True)
    ids = count(1)
    service = LabJarvisService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(factory),
        document_extractor=DocumentExtractor(max_bytes=1_000_000, max_pages=10),
        speech=FakeSpeechToText(),
        verification_speech=FakeSpeechToText(verification=True),
        text=FakeTextUnderstanding(),
        labels=FakeLabelRecognition(),
        inference_runner=DirectInferenceRunner(),
        clock=clock,
        new_id=lambda: f"id-{next(ids)}",
        command_confidence_threshold=0.75,
        reagent_match_threshold=92,
        reagent_uncertain_threshold=72,
        raw_media_store=DiscardRawMediaStore(),
        retain_transcripts=True,
        wake_phrase="Jarvis",
        voice_only_confirmation=True,
        voice_observations=voice_observations,
    )
    imported = service.import_protocol(SAMPLE.name, DOCX_MIME, SAMPLE.read_bytes())
    version = imported.version
    if extra_requirements:
        first = version.steps[0]
        steps = (
            replace(first, requirements=(*first.requirements, *extra_requirements)),
            *version.steps[1:],
        )
        version = service.update_protocol_draft(version.id, steps, expected_revision=0)
    approved = service.approve_protocol(version.id, expected_revision=version.revision)
    session = service.create_session(approved.id)
    if start:
        session = service.start_session(session.id, expected_version=session.version)
    return Bench(service=service, session_id=session.id, clock=clock)


def confirm_by_voice(bench: Bench, words: str, reply: str) -> None:
    action_id = bench.propose(words)
    result = bench.answer(action_id, reply)
    assert result.committed, result.response_text


# ============================================================ the safety matrix


@dataclass(frozen=True)
class Case:
    name: str
    setup: Callable[[], Bench]
    command: str
    valid: str
    wrong: str
    second: str
    committed: Callable[[Bench], bool]


def _skip_bench() -> Bench:
    return build_bench()


def _correction_bench() -> Bench:
    bench = build_bench()
    confirm_by_voice(bench, "Jarvis, record the volume as 10 mL", "Jarvis, confirm 10 millilitres")
    return bench


def _timer_bench() -> Bench:
    bench = build_bench()
    confirm_by_voice(bench, "Jarvis, set a timer for 10 minutes", "Jarvis, confirm 10 minutes")
    return bench


def _paused_bench() -> Bench:
    bench = build_bench()
    confirm_by_voice(bench, "Jarvis, pause the session", "Jarvis, confirm pause")
    return bench


def _state(bench: Bench) -> SessionState:
    return bench.service.get_session(bench.session_id).state


CASES = (
    Case(
        "skip step with reason",
        _skip_bench,
        "Jarvis, skip this step because the reagent is unavailable",
        "Jarvis, confirm skip step 1",
        "Jarvis, confirm skip step 2",
        "Jarvis, confirm skip step 1 and step 2",
        lambda b: b.service.get_session(b.session_id).steps[0].state
        is StepState.SKIPPED_WITH_REASON,
    ),
    Case(
        "correct the last measurement",
        _correction_bench,
        "Jarvis, correct the last measurement to 12 millilitres",
        "Jarvis, confirm 12 millilitres",
        "Jarvis, confirm the old 10 millilitres",
        "Jarvis, confirm 12 or 21 millilitres",
        lambda b: len(b.entries("correction")) == 1,
    ),
    Case(
        "cancel a timer",
        _timer_bench,
        "Jarvis, cancel the 10 minute timer",
        "Jarvis, confirm cancel 10 minute timer",
        "Jarvis, confirm cancel 5 minute timer",
        "Jarvis, confirm cancel 10 minute and 5 minute timer",
        lambda b: b.timers()[0].timer.state is TimerState.CANCELLED,
    ),
    Case(
        "pause a timer",
        _timer_bench,
        "Jarvis, pause the timer",
        "Jarvis, confirm pause timer",
        "Jarvis, confirm resume timer",
        "Jarvis, confirm pause timer 10",
        lambda b: b.timers()[0].timer.state is TimerState.PAUSED,
    ),
    Case(
        "start the session",
        lambda: build_bench(start=False),
        "Jarvis, start the session",
        "Jarvis, confirm start session",
        "Jarvis, confirm end session",
        "Jarvis, confirm start session and pause",
        lambda b: _state(b) is SessionState.ACTIVE,
    ),
    Case(
        "resume the session",
        _paused_bench,
        "Jarvis, resume the session",
        "Jarvis, confirm resume session",
        "Jarvis, confirm pause session",
        "Jarvis, confirm resume session 2",
        lambda b: _state(b) is SessionState.ACTIVE,
    ),
    Case(
        "record an observation (opt-in)",
        lambda: build_bench(voice_observations=True),
        "Jarvis, record observation the solution turned cloudy",
        "Jarvis, confirm observation",
        "Jarvis, confirm observation the solution is clear",
        "Jarvis, confirm observation 2",
        lambda b: len(b.entries("observation")) == 1,
    ),
)
IDS = [case.name for case in CASES]


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_a_valid_restating_confirmation_commits(case: Case) -> None:
    bench = case.setup()
    action_id = bench.propose(case.command)
    result = bench.answer(action_id, case.valid)
    assert result.committed, result.response_text
    assert bench.status(action_id) is PendingActionStatus.ACCEPTED
    assert case.committed(bench)
    assert "jarvis" not in result.response_text.casefold()


@pytest.mark.parametrize("case", CASES, ids=IDS)
@pytest.mark.parametrize(
    "reply", ["Jarvis, yes", "Jarvis, confirm", "Jarvis, OK", "Jarvis, correct"]
)
def test_a_bare_acknowledgement_never_commits(case: Case, reply: str) -> None:
    bench = case.setup()
    action_id = bench.propose(case.command)
    result = bench.answer(action_id, reply)
    assert result.committed is False
    assert bench.status(action_id) is PendingActionStatus.PENDING
    assert not case.committed(bench)


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_a_wrong_value_or_action_never_commits(case: Case) -> None:
    bench = case.setup()
    action_id = bench.propose(case.command)
    assert bench.answer(action_id, case.wrong).committed is False
    assert bench.status(action_id) is PendingActionStatus.PENDING
    assert not case.committed(bench)


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_a_second_or_conflicting_value_never_commits(case: Case) -> None:
    bench = case.setup()
    action_id = bench.propose(case.command)
    assert bench.answer(action_id, case.second).committed is False
    # The two transcriptions disagree about the reply: never a winner.
    assert bench.answer(action_id, f"{case.valid}||{case.wrong}").committed is False
    assert bench.status(action_id) is PendingActionStatus.PENDING
    assert not case.committed(bench)


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_unaddressed_speech_never_commits_and_leaves_no_trace(case: Case) -> None:
    bench = case.setup()
    action_id = bench.propose(case.command)
    before = len(bench.service.get_session_review(bench.session_id).utterances)
    result = bench.answer(action_id, case.valid.removeprefix("Jarvis, "))
    assert result.decision == "not_addressed"
    assert result.committed is False
    assert len(bench.service.get_session_review(bench.session_id).utterances) == before
    assert bench.status(action_id) is PendingActionStatus.PENDING


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_an_echo_of_the_prompt_never_commits(case: Case) -> None:
    bench = case.setup()
    proposal = bench.hear(case.command)
    assert proposal.pending_action is not None
    action_id = proposal.pending_action.id
    # The prompt itself, re-captured by the microphone: no wake phrase.
    assert bench.answer(action_id, proposal.response_text).committed is False
    # Even an echo that somehow follows the wake phrase still carries "say".
    phrase = proposal.response_text.split("then: ", 1)[1].split(".", 1)[0]
    echoed = f"Jarvis, say the wake word, then: {phrase}"
    assert bench.answer(action_id, echoed).committed is False
    assert bench.status(action_id) is PendingActionStatus.PENDING
    assert not case.committed(bench)


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_a_stale_or_replaced_proposal_never_commits(case: Case) -> None:
    bench = case.setup()
    first = bench.propose(case.command)
    assert bench.answer(first, "Jarvis, reject").committed is False
    assert bench.status(first) is PendingActionStatus.REJECTED
    second = bench.propose(case.command)
    with pytest.raises(ApplicationError):
        bench.answer(first, case.valid)
    # A reply prepared against an older view of the session is refused too.
    stale_version = bench.service.get_session(bench.session_id).version - 1
    with pytest.raises(ApplicationError, match="session changed"):
        bench.answer(second, case.valid, expected_version=stale_version)
    assert bench.status(second) is PendingActionStatus.PENDING
    assert not case.committed(bench)


# ======================================================== skip, correct, observe


def test_skipping_without_a_reason_asks_for_one() -> None:
    bench = build_bench()
    result = bench.hear("Jarvis, skip this step")
    assert result.pending_action is None
    assert "why" in result.response_text.casefold()
    assert bench.service.get_session(bench.session_id).steps[0].state is StepState.IN_PROGRESS


def test_a_correction_keeps_and_links_the_original_entry() -> None:
    bench = _correction_bench()
    original = bench.entries("measurement")[0]
    proposal = bench.hear("Jarvis, correct the volume to 12 mL")
    assert proposal.pending_action is not None
    assert "from 10 millilitres to 12 millilitres" in proposal.response_text
    assert bench.answer(proposal.pending_action.id, "Jarvis, confirm 12 millilitres").committed
    correction = bench.entries("correction")[0]
    assert correction.supersedes_entry_id == original.id
    assert correction.payload["value"] == "12" and correction.payload["old_value"] == "10"
    assert bench.entries("measurement")[0].payload["value"] == "10"  # unchanged
    # A second correction supersedes the first, forming a chain.
    confirm_by_voice(
        bench, "Jarvis, correct the last measurement to 13 mL", "Jarvis, confirm 13 millilitres"
    )
    latest = bench.entries("correction")[-1]
    assert latest.supersedes_entry_id == correction.id


def test_a_correction_needs_a_measurement_and_a_value() -> None:
    bench = build_bench()
    assert bench.hear("Jarvis, correct the last measurement to 12 mL").pending_action is None
    assert bench.hear("Jarvis, correct the previous entry").pending_action is None
    bench = _correction_bench()
    unchanged = bench.hear("Jarvis, correct the last measurement to 10 mL")
    assert unchanged.pending_action is None and "already" in unchanged.response_text
    named = bench.hear("Jarvis, correct the temperature to 37 degrees Celsius")
    assert named.pending_action is None


def test_a_correction_overtaken_on_screen_fails_closed() -> None:
    bench = _correction_bench()
    original = bench.entries("measurement")[0]
    action_id = bench.propose("Jarvis, correct the last measurement to 12 mL")
    bench.service.correct_notebook_entry(
        original.id, {"kind": "volume", "value": "11", "unit": "mL"}, correlation_id="screen"
    )
    with pytest.raises(ApplicationError, match="corrected again"):
        bench.answer(action_id, "Jarvis, confirm 12 millilitres")
    assert len(bench.entries("correction")) == 1  # only the on-screen one


def test_observations_are_voice_only_only_when_opted_in_and_both_readings_agree() -> None:
    bench = build_bench(voice_observations=False)
    action_id = bench.propose("Jarvis, record observation the solution turned cloudy")
    result = bench.answer(action_id, "Jarvis, confirm observation")
    assert result.committed is False
    assert "push-to-talk" in result.response_text or "screen" in result.response_text

    opted = build_bench(voice_observations=True)
    proposal = opted.hear("Jarvis, record observation the solution turned cloudy")
    assert proposal.pending_action is not None
    assert "I heard: the solution turned cloudy" in proposal.response_text
    # A dictation the two transcriptions disagree on never becomes a proposal.
    fresh = build_bench(voice_observations=True)
    disagreed = fresh.hear(
        "Jarvis, record observation the solution turned cloudy"
        "||Jarvis, record observation the solution turned clear"
    )
    assert disagreed.pending_action is None
    assert disagreed.parsed_command.intent == "TRANSCRIPT_CONFLICT"


def test_a_typed_observation_cannot_be_confirmed_voice_only() -> None:
    bench = build_bench(voice_observations=True)
    session = bench.service.get_session(bench.session_id)
    typed = bench.service.handle_text_command(
        bench.session_id,
        "record observation the solution turned cloudy",
        idempotency_key="typed-1",
        expected_version=session.version,
        correlation_id="typed",
    )
    assert typed.pending_action is not None
    assert bench.answer(typed.pending_action.id, "Jarvis, confirm observation").committed is False


# ===================================================================== timers


def test_timer_references_are_resolved_or_asked_about() -> None:
    bench = _timer_bench()
    confirm_by_voice(bench, "Jarvis, set a timer for 5 minutes", "Jarvis, confirm 5 minutes")
    ambiguous = bench.hear("Jarvis, cancel the timer")
    assert ambiguous.pending_action is None
    assert "Which timer" in ambiguous.response_text
    assert "10 minute" in ambiguous.response_text and "5 minute" in ambiguous.response_text
    unknown = bench.hear("Jarvis, cancel the 3 minute timer")
    assert unknown.pending_action is None
    resolved = bench.hear("Jarvis, cancel the 5 minute timer")
    assert resolved.pending_action is not None
    assert resolved.pending_action.payload["timer_duration_seconds"] == "300"
    assert "confirm cancel 5 minute timer" in resolved.response_text


def test_nothing_to_resume_is_said_plainly() -> None:
    bench = _timer_bench()
    result = bench.hear("Jarvis, resume the timer")
    assert result.pending_action is None
    assert "no paused timer" in result.response_text


def test_how_long_is_left_is_spoken_and_read_only() -> None:
    bench = _timer_bench()
    bench.clock.advance(90)
    result = bench.hear("Jarvis, how long is left")
    assert result.pending_action is None
    assert "8 minutes 30 seconds left" in result.response_text


def test_a_finished_timer_alarms_until_acknowledged_and_survives_a_reload() -> None:
    bench = build_bench()
    bench.service.invoke_tool(
        bench.session_id,
        "set_timer",
        {"duration_seconds": "60", "label": "incubation"},
        source=EntrySource.TYPED_INPUT,
        model_proposed=False,
        correlation_id="button",
    )
    assert bench.timers()[0].alarm_active is False
    bench.clock.advance(61)
    assert bench.timers()[0].alarm_active is True
    # A reload re-reads authority: the alarm is still sounding.
    snapshot = bench.service.get_session_snapshot(bench.session_id)
    assert snapshot.timers[0].alarm_active is True
    before = len(bench.service.list_notebook(bench.session_id))
    stopped = bench.hear("Jarvis, stop the alarm")
    assert stopped.parsed_command.intent == "ACKNOWLEDGE_ALARM"
    assert stopped.response_text == "Alarm stopped."
    assert bench.timers()[0].alarm_active is False
    assert bench.service.get_session_snapshot(bench.session_id).timers[0].alarm_active is False
    # Acknowledging is not a record.
    assert len(bench.service.list_notebook(bench.session_id)) == before
    assert bench.hear("Jarvis, timer done").response_text == "No alarm is sounding."


def test_an_alarm_can_be_silenced_while_a_proposal_waits() -> None:
    bench = build_bench()
    bench.service.invoke_tool(
        bench.session_id,
        "set_timer",
        {"duration_seconds": "60"},
        source=EntrySource.TYPED_INPUT,
        model_proposed=False,
        correlation_id="button",
    )
    bench.clock.advance(61)
    action_id = bench.propose("Jarvis, record the volume as 10 mL")
    result = bench.answer(action_id, "Jarvis, stop the alarm")
    assert result.decision == "control" and result.control == "ACKNOWLEDGE_ALARM"
    assert result.committed is False
    assert bench.status(action_id) is PendingActionStatus.PENDING
    assert bench.timers()[0].alarm_active is False
    # The on-screen control reaches the same state through the gateway.
    assert bench.service.invoke_tool(
        bench.session_id,
        "acknowledge_timer_alarm",
        {},
        source=EntrySource.TYPED_INPUT,
        model_proposed=False,
        correlation_id="button",
    ).message == "No alarm is sounding."


# ================================================================ questions


def test_a_protocol_question_is_answered_from_the_protocol_with_its_step() -> None:
    bench = build_bench()
    answer = bench.hear("Jarvis, how long should I incubate the synthetic sample?")
    assert answer.parsed_command.intent == "ANSWER_PROTOCOL_QUESTION"
    assert answer.response_text == "Step 2 says: Incubate the synthetic sample for 5 minutes."
    assert answer.pending_action is None


def test_an_unanswerable_question_is_never_guessed() -> None:
    bench = build_bench()
    answer = bench.hear("Jarvis, what speed should the centrifuge run at?")
    assert answer.response_text == "I couldn't find that in the approved protocol."


def test_read_only_voice_queries() -> None:
    bench = build_bench()
    reagents = bench.hear("Jarvis, what reagents do I need")
    assert "ethanol" in reagents.response_text
    where = bench.hear("Jarvis, where am I")
    assert where.response_text.startswith("You are on step 1 of 3.")
    empty = bench.hear("Jarvis, what did I just record")
    assert "Nothing has been recorded" in empty.response_text
    confirm_by_voice(bench, "Jarvis, record the volume as 10 mL", "Jarvis, confirm 10 millilitres")
    last = bench.hear("Jarvis, what did I just record")
    assert last.response_text == "The last entry was volume 10 millilitres in step 1."
    for result in (reagents, where, empty, last):
        assert result.pending_action is None


# ================================================================== reagents


def test_a_reagent_is_selected_by_name_alias_or_number() -> None:
    buffer = ProtocolRequirement(requirement_id="req-buffer", name="buffer A", aliases=("PBS",))
    bench = build_bench(extra_requirements=(buffer,))
    by_name = bench.hear("Jarvis, check the buffer")
    assert by_name.parsed_command.parameters["requirement_id"] == "req-buffer"
    by_alias = bench.hear("Jarvis, check the PBS")
    assert by_alias.parsed_command.parameters["requirement_id"] == "req-buffer"
    by_number = bench.hear("Jarvis, check reagent two")
    assert by_number.parsed_command.parameters["requirement_id"] == "req-buffer"
    ethanol = bench.hear("Jarvis, check the ethanol")
    assert ethanol.parsed_command.parameters["requirement_id"] == "step-1-req-1"
    assert "ethanol" in ethanol.response_text


def test_an_unknown_or_unspecified_reagent_is_never_defaulted() -> None:
    buffer = ProtocolRequirement(requirement_id="req-buffer", name="buffer A")
    bench = build_bench(extra_requirements=(buffer,))
    unknown = bench.hear("Jarvis, check the acetone")
    assert "requirement_id" not in unknown.parsed_command.parameters
    assert "ethanol" in unknown.response_text and "buffer A" in unknown.response_text
    unspecified = bench.hear("Jarvis, check reagent")
    assert "requirement_id" not in unspecified.parsed_command.parameters
    assert unspecified.response_text.startswith("Which reagent?")


# =================================================================== controls


@pytest.mark.parametrize(
    ("words", "intent"),
    [
        ("Jarvis, stop listening", "STOP_LISTENING"),
        ("Jarvis, start listening", "START_LISTENING"),
        ("Jarvis, mute", "MUTE_SPEECH"),
        ("Jarvis, unmute", "UNMUTE_SPEECH"),
        ("Jarvis, say that again", "REPEAT_LAST_SPOKEN"),
        ("Jarvis, stop", "STOP_SPEAKING"),
        ("Jarvis, quiet", "STOP_SPEAKING"),
    ],
)
def test_interface_controls_never_touch_the_record(words: str, intent: str) -> None:
    bench = build_bench()
    before = bench.service.get_session_review(bench.session_id)
    result = bench.hear(words)
    after = bench.service.get_session_review(bench.session_id)
    assert result.parsed_command.intent == intent
    assert result.pending_action is None
    assert "jarvis" not in result.response_text.casefold()
    assert after.entries == before.entries
    assert after.inferences == before.inferences
    assert after.session.version == before.session.version


def test_standby_accepts_only_start_listening() -> None:
    bench = build_bench()
    ignored = bench.hear("Jarvis, record the volume as 10 mL", ListenMode.STANDBY)
    assert ignored.parsed_command.intent == "LISTEN_MODE_IGNORED"
    assert ignored.pending_action is None
    assert bench.service.get_session_review(bench.session_id).inferences == ()
    assert bench.hear("Jarvis, mute", ListenMode.STANDBY).parsed_command.intent == (
        "LISTEN_MODE_IGNORED"
    )
    assert bench.hear("Jarvis, start listening", ListenMode.STANDBY).parsed_command.intent == (
        "START_LISTENING"
    )


def test_barge_in_accepts_only_stop_mute_and_the_alarm() -> None:
    bench = build_bench()
    for words in ("Jarvis, confirm 10 millilitres", "Jarvis, stop listening", "Jarvis, next step"):
        result = bench.hear(words, ListenMode.BARGE_IN)
        assert result.parsed_command.intent == "LISTEN_MODE_IGNORED"
    assert bench.hear("Jarvis, stop", ListenMode.BARGE_IN).parsed_command.intent == "STOP_SPEAKING"
    assert bench.hear("Jarvis, mute", ListenMode.BARGE_IN).parsed_command.intent == "MUTE_SPEECH"
    assert bench.hear("Jarvis, stop the alarm", ListenMode.BARGE_IN).parsed_command.intent == (
        "ACKNOWLEDGE_ALARM"
    )
    assert bench.service.list_notebook(bench.session_id) == ()


def test_a_ready_session_refuses_everything_but_starting_and_reading() -> None:
    bench = build_bench(start=False)
    refused = bench.hear("Jarvis, record the volume as 10 mL")
    assert refused.pending_action is None
    assert "ready" in refused.response_text
    assert bench.hear("Jarvis, what reagents do I need").pending_action is None


# ========================================================== profile and startup


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        model_mode="fake",
        data_dir=tmp_path,
        database_url=f"sqlite+pysqlite:///{(tmp_path / 'db.sqlite').as_posix()}",
        **overrides,  # type: ignore[arg-type]
    )


def test_the_profile_switches_everything_on_together(tmp_path: Path) -> None:
    settings = _settings(tmp_path, hands_free_profile=True)
    for flag in Settings.HANDS_FREE_PROFILE_FLAGS:
        assert getattr(settings, flag) is True, flag
    assert settings.high_integrity_transcription_model_name == "base.en"
    with pytest.raises(ValidationError, match="LIVE_LABEL_SCAN"):
        _settings(tmp_path, hands_free_profile=True, live_label_scan_enabled=False)


def test_hosted_vision_hands_free_is_ready_with_timed_capture(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        model_mode="local",
        data_dir=tmp_path,
        allow_hosted_models=True,
        openai_api_key=SecretStr("unit-test-placeholder-not-a-real-key"),
        vision_provider="openai",
        hosted_vision_model_name="gpt-5.4-nano",
        hands_free_profile=True,
        text_interpreter="rules",
    )
    adapters = SimpleNamespace(
        speech=_adapter(True, True, "small.en"),
        verification_speech=_adapter(True, True, "base.en"),
        labels=_adapter(True, True, "gpt-5.4-nano"),
        text=SimpleNamespace(),
    )

    status = hands_free_status(settings, adapters)
    assert status["ready"] is True
    assert status["missing"] == []
    assert settings.live_label_scan_enabled is False


def test_default_verifier_name_is_only_allowed_in_the_profile(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="explicit"):
        _settings(
            tmp_path,
            hands_free_listening_enabled=True,
            high_integrity_transcription_enabled=True,
        )


def test_dependent_options_are_refused_on_their_own(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="voice-only"):
        _settings(tmp_path, voice_observations_enabled=True)
    with pytest.raises(ValidationError, match="autostart"):
        _settings(tmp_path, hands_free_autostart=True)


def _adapter(ready: bool, cached: bool, name: str) -> SimpleNamespace:
    return SimpleNamespace(
        readiness=lambda: AdapterReadiness(
            ready=ready,
            adapter="test",
            model_name=name,
            detail="weights are not cached and downloads are disabled" if not cached else "ok",
            model_cached=cached,
        )
    )


def test_the_profile_refuses_to_start_without_a_local_model(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        model_mode="local",
        hands_free_profile=True,
        text_interpreter="rules",
        data_dir=tmp_path,
    )
    adapters = SimpleNamespace(
        speech=_adapter(True, True, "small.en"),
        verification_speech=_adapter(False, False, "base.en"),
        labels=_adapter(True, True, "english_g2"),
        text=SimpleNamespace(),
    )
    with pytest.raises(HandsFreeProfileError, match=r"base\.en"):
        preflight(settings, adapters)
    adapters.verification_speech = _adapter(True, True, "base.en")
    preflight(settings, adapters)


def test_readiness_reports_one_hands_free_line_and_the_api_carries_listen_modes(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path, hands_free_profile=True, app_env="test")
    with TestClient(create_app(settings, create_schema=True)) as client:
        payload = client.get("/health/ready").json()
        assert payload["hands_free"]["summary"] == "Hands-free: ready"
        assert payload["interaction"]["hands_free_autostart"] is True
        assert payload["interaction"]["timer_alarms"] is True
        assert payload["interaction"]["timer_warning_seconds"] == "60,10"
        response = client.post(
            "/api/v1/sessions/missing/commands/audio?require_wake_phrase=true&listen_mode=standby",
            headers={"Idempotency-Key": "k", "If-Match": "0"},
            files={"file": ("c.wav", b"FAKE:Jarvis, start listening", AUDIO)},
        )
        assert response.status_code == 404
        invalid = client.post(
            "/api/v1/sessions/missing/commands/audio?listen_mode=everything",
            headers={"Idempotency-Key": "k2", "If-Match": "0"},
            files={"file": ("c.wav", b"FAKE:Jarvis, stop", AUDIO)},
        )
        assert invalid.status_code == 422

    off = _settings(tmp_path / "off")
    assert off.hands_free_listening_enabled is False


def test_spelled_out_quantities_from_the_real_session_run_through_the_grammar() -> None:
    """Replays the phrasings of the 2026-09-28 real-world session.

    The transcriber wrote quantities as words. They used to reach the language
    model, which once returned a correction without its value; they must now
    be understood deterministically, grounded against the words actually said,
    and confirmable by a spelled-out read-back.
    """

    bench = build_bench()
    measured = bench.hear("Jarvis, record the volume as ten millilitres.")
    assert measured.pending_action is not None, measured.response_text
    assert measured.pending_action.payload["value"] == "10"
    assert bench.answer(measured.pending_action.id, "Jarvis, confirm ten millilitres.").committed

    corrected = bench.hear("Jarvis, correct the last measurement to twenty-two millilitres.")
    assert corrected.pending_action is not None, corrected.response_text
    assert corrected.pending_action.payload["value"] == "22"
    assert bench.answer(
        corrected.pending_action.id, "Jarvis, confirm twenty-two millilitres."
    ).committed
    assert bench.entries("correction")[0].payload["value"] == "22"

    timed = bench.hear("Jarvis, set a timer for one minute.")
    assert timed.pending_action is not None, timed.response_text
    assert timed.pending_action.payload["duration_seconds"] == "60"


def test_part_of_a_spoken_value_is_never_turned_into_a_different_number() -> None:
    bench = build_bench()
    for words in (
        "Jarvis, record the volume as one two millilitres",
        "Jarvis, record the temperature as thirty seven point five degrees Celsius",
    ):
        proposal = bench.hear(words)
        assert proposal.pending_action is None or proposal.pending_action.payload.get(
            "value"
        ) not in {"2", "5"}, words


class _VocabularySpy:
    """Wraps a speech adapter and records the vocabulary of every call."""

    def __init__(self, inner):  # type: ignore[no-untyped-def]
        self._inner = inner
        self.adapter_name = inner.adapter_name
        self.model_name = inner.model_name
        self.vocabularies: list[tuple[str, ...]] = []

    def transcribe(self, audio, *, content_type, vocabulary=()):  # type: ignore[no-untyped-def]
        self.vocabularies.append(tuple(vocabulary))
        return self._inner.transcribe(audio, content_type=content_type, vocabulary=vocabulary)

    def readiness(self):  # type: ignore[no-untyped-def]
        return self._inner.readiness()


def test_the_verification_reading_is_primed_with_commands_but_never_the_protocol() -> None:
    bench = build_bench()
    spy = _VocabularySpy(bench.service._verification_speech)
    bench.service._verification_speech = spy  # type: ignore[assignment]

    confirm_by_voice(bench, "Jarvis, record the volume as 10 mL", "Jarvis, confirm 10 millilitres")

    assert spy.vocabularies, "the verifier was never consulted"
    protocol_terms = {"ethanol"}  # the sample protocol's reagent
    for vocabulary in spy.vocabularies:
        assert "Jarvis" in vocabulary and "confirm" in vocabulary
        assert not protocol_terms & {term.casefold() for term in vocabulary}
        assert not any(character.isdigit() for term in vocabulary for character in term)
