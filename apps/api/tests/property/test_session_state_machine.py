"""Stateful property-based verification of the session safety invariants.

A Hypothesis ``RuleBasedStateMachine`` drives the real ``LabJarvisService``
(real SQLAlchemy repositories on in-memory SQLite, deterministic fake model
adapters) through random interleavings of start, pause, resume, spoken/typed
proposals, confirm, reject, skip, complete, end, and idempotent replay. After
every step it checks the invariants the safety case depends on:

* at most one active step, and ``active_step_id`` agrees with the step states;
* at most one unresolved pending action per session;
* the session version never decreases, increases on every session/step state
  change, and does not change when nothing changed;
* COMPLETED and ABORTED are absorbing;
* the notebook gains a measurement/observation only through an accepted
  confirmation, one entry per accepted confirmation;
* an idempotent replay returns the original outcome and writes nothing new;
* a skipped step is never recorded as completed/performed;
* any refused operation leaves authoritative state unchanged.

The hands-free extension (ADR 0018) adds wake-phrase commands in all three
listening modes, voice-only answers drawn from valid read-backs, bare
acknowledgements, wrong and second values, echoes and unaddressed speech,
timer control, skip/correct by voice, alarms and clock advances, with:

* a voice-only answer commits only when it is exactly the wake phrase plus the
  read-back phrase for the waiting proposal;
* standby and barge-in segments never propose, record or change state;
* an acknowledged alarm never sounds again.

``LABJARVIS_PROPERTY_EXAMPLES`` raises the example count for the evidence run
(the default keeps ordinary CI fast); the result of the 1,000-sequence run is
recorded in the final evidence package.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from itertools import count
from pathlib import Path

from hypothesis import HealthCheck, settings
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    precondition,
    rule,
)

from labjarvis.application.errors import ApplicationError
from labjarvis.application.service import LabJarvisService
from labjarvis.domain.models import (
    EntrySource,
    PendingActionStatus,
    SessionState,
    StepState,
)
from labjarvis.domain.voice_confirmation import expected_readback
from labjarvis.domain.voice_controls import ListenMode
from labjarvis.infrastructure.database import SqlAlchemyUnitOfWork, create_session_factory
from labjarvis.infrastructure.documents import DOCX_MIME, DocumentExtractor
from labjarvis.infrastructure.fake_models import (
    FakeLabelRecognition,
    FakeSpeechToText,
    FakeTextUnderstanding,
)
from labjarvis.infrastructure.inference import DirectInferenceRunner
from labjarvis.infrastructure.media_storage import DiscardRawMediaStore

SAMPLE = (
    Path(__file__).parents[4]
    / "sample_data"
    / "protocols"
    / "development-only-ethanol-protocol.docx"
)
EXAMPLES = int(os.environ.get("LABJARVIS_PROPERTY_EXAMPLES", "150"))
TERMINAL = {SessionState.COMPLETED, SessionState.ABORTED}
RECORDED = {"measurement", "observation", "correction"}

COMMANDS = st.sampled_from(
    [
        "record the temperature as 37 degrees Celsius",
        "record the volume as 5 mL",
        "record the ph as 7.4 ph",
        "record an observation as the solution is clear",
        "set a timer for 5 minutes",
        "pause",
        "resume",
        "complete the step",
        "end session",
        "next step",
        "record the temperature as -300 degrees Celsius",
        "pause and end session",
        "tell me a joke",
        # hands-free phrasings (grammar v2)
        "skip this step because the reagent is unavailable",
        "skip this step",
        "correct the last measurement to 12 mL",
        "correct the volume to 6 mL",
        "pause the timer",
        "resume the timer",
        "cancel the 5 minute timer",
        "cancel the timer",
        "how long is left",
        "what reagents do I need",
        "where am I",
        "what did I just record",
        "how long do I incubate the synthetic sample",
        "check the ethanol",
        "check the acetone",
        "start the session",
        "resume the session",
    ]
)
VOICE_COMMANDS = st.sampled_from(
    [
        "Jarvis, record the volume as 5 mL",
        "Jarvis, set a timer for 5 minutes",
        "Jarvis, skip this step because the reagent is unavailable",
        "Jarvis, correct the last measurement to 12 mL",
        "Jarvis, cancel the 5 minute timer",
        "Jarvis, pause the timer",
        "Jarvis, start the session",
        "Jarvis, record observation the solution turned cloudy",
        "Jarvis, stop the alarm",
        "Jarvis, stop listening",
        "Jarvis, start listening",
        "Jarvis, stop",
        "Jarvis, mute",
        "Jarvis, how long is left",
        "record the volume as 5 mL",
    ]
)
REPLY_KINDS = st.sampled_from(
    ["valid", "bare", "confirm", "unaddressed", "echo", "wrong", "second", "reject", "alarm"]
)


class _Clock:
    def __init__(self) -> None:
        self.moment = datetime(2026, 9, 26, 9, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        self.moment += timedelta(seconds=1)
        return self.moment

    def advance(self, seconds: int) -> None:
        self.moment += timedelta(seconds=seconds)


class SessionMachine(RuleBasedStateMachine):
    @initialize()
    def open_session(self) -> None:
        factory = create_session_factory("sqlite+pysqlite://", create_schema=True)
        ids = count(1)
        self.clock = _Clock()
        self.service = LabJarvisService(
            uow_factory=lambda: SqlAlchemyUnitOfWork(factory),
            document_extractor=DocumentExtractor(max_bytes=1_000_000, max_pages=10),
            speech=FakeSpeechToText(),
            verification_speech=FakeSpeechToText(verification=True),
            text=FakeTextUnderstanding(),
            labels=FakeLabelRecognition(),
            inference_runner=DirectInferenceRunner(),
            clock=self.clock,
            voice_only_confirmation=True,
            voice_observations=True,
            new_id=lambda: f"id-{next(ids)}",
            command_confidence_threshold=0.75,
            reagent_match_threshold=92,
            reagent_uncertain_threshold=72,
            raw_media_store=DiscardRawMediaStore(),
            retain_transcripts=True,
        )
        imported = self.service.import_protocol(SAMPLE.name, DOCX_MIME, SAMPLE.read_bytes())
        approved = self.service.approve_protocol(imported.version.id, expected_revision=0)
        self.session_id = self.service.create_session(approved.id).id
        self.keys = count(1)
        self.accepted_records = 0
        self.skipped_steps: set[str] = set()
        self.last_command: tuple[str, str, int] | None = None
        self.previous = self._signature()
        self.terminal_reached: SessionState | None = None
        self.acknowledged: set[str] = set()
        self.audio_counter = count(1)

    # --- helpers ------------------------------------------------------------

    def _session(self):  # type: ignore[no-untyped-def]
        return self.service.get_session(self.session_id)

    def _signature(self) -> tuple[object, ...]:
        session = self._session()
        return (
            session.version,
            session.state,
            session.active_step_id,
            tuple((step.step_id, step.state) for step in session.steps),
        )

    def _pending(self) -> tuple[object, ...]:
        with self.service._uow_factory() as uow:
            return uow.pending_actions.list_pending_for_session(self.session_id)

    def _entries(self) -> tuple[object, ...]:
        return self.service.list_notebook(self.session_id)

    def _refused(self, before: tuple[object, ...], entries_before: int) -> None:
        assert self._signature() == before, "a refused operation changed session state"
        assert len(self._entries()) >= entries_before

    def _tool(self, name: str, parameters: dict[str, str] | None = None) -> None:
        before = self._signature()
        session = self._session()
        try:
            self.service.invoke_tool(
                self.session_id,
                name,
                parameters or {},
                source=EntrySource.TYPED_INPUT,
                model_proposed=False,
                correlation_id=f"corr-{next(self.keys)}",
                expected_version=session.version,
                idempotency_key=f"tool-{next(self.keys)}",
            )
        except ApplicationError:
            self._refused(before, 0)

    # --- rules --------------------------------------------------------------

    @rule()
    def start(self) -> None:
        session = self._session()
        before = self._signature()
        try:
            self.service.start_session(self.session_id, expected_version=session.version)
        except ApplicationError:
            self._refused(before, 0)

    @rule()
    def pause(self) -> None:
        self._tool("pause_session")

    @rule()
    def resume(self) -> None:
        self._tool("resume_session")

    @rule()
    def complete(self) -> None:
        self._tool("complete_step")

    @rule(reason=st.sampled_from(["reagent unavailable", "not applicable", " "]))
    def skip(self, reason: str) -> None:
        session = self._session()
        active = session.active_step_id
        before = self._signature()
        try:
            self.service.invoke_tool(
                self.session_id,
                "skip_step_with_reason",
                {"reason": reason},
                source=EntrySource.TYPED_INPUT,
                model_proposed=False,
                correlation_id=f"corr-{next(self.keys)}",
                expected_version=session.version,
                idempotency_key=f"skip-{next(self.keys)}",
            )
        except ApplicationError:
            self._refused(before, 0)
            return
        if active is not None and self._signature() != before:
            self.skipped_steps.add(active)

    @rule()
    def end(self) -> None:
        self._tool("end_session")

    @rule(text=COMMANDS)
    def propose(self, text: str) -> None:
        session = self._session()
        before = self._signature()
        entries_before = len(self._entries())
        key = f"cmd-{next(self.keys)}"
        try:
            self.service.handle_text_command(
                self.session_id,
                text,
                idempotency_key=key,
                expected_version=session.version,
                correlation_id=f"corr-{key}",
            )
        except ApplicationError:
            self._refused(before, entries_before)
            return
        self.last_command = (text, key, session.version)
        # A proposal alone never writes a recorded value or advances state.
        assert self._signature() == before
        recorded = [e for e in self._entries() if e.entry_type in RECORDED]
        assert len(recorded) == self.accepted_records

    @precondition(lambda self: self.last_command is not None)
    @rule()
    def replay(self) -> None:
        assert self.last_command is not None
        text, key, version = self.last_command
        entries_before = self._entries()
        signature = self._signature()
        try:
            result = self.service.handle_text_command(
                self.session_id,
                text,
                idempotency_key=key,
                expected_version=version,
                correlation_id=f"corr-{key}",
            )
        except ApplicationError:
            self._refused(signature, len(entries_before))
            return
        assert result.replayed is True
        assert self._entries() == entries_before, "a replay wrote to the notebook"
        assert self._signature() == signature, "a replay changed state"

    @rule(accept=st.booleans())
    def decide(self, accept: bool) -> None:
        pending = self._pending()
        if not pending:
            return
        action = pending[0]
        before = self._signature()
        try:
            result = self.service.confirm_pending_action(
                action.id, accepted=accept, correlation_id=f"corr-{next(self.keys)}"
            )
        except ApplicationError:
            self._refused(before, 0)
            return
        if accept and result.entry is not None and result.entry.entry_type in RECORDED:
            self.accepted_records += 1

    # --- hands-free rules -----------------------------------------------------

    @rule(text=VOICE_COMMANDS, mode=st.sampled_from(list(ListenMode)))
    def voice(self, text: str, mode: ListenMode) -> None:
        session = self._session()
        before = self._signature()
        entries_before = self._entries()
        pending_before = self._pending()
        try:
            result = self.service.handle_audio_command(
                self.session_id,
                f"FAKE:{text}".encode(),
                content_type="audio/wav",
                idempotency_key=f"voice-{next(self.keys)}",
                expected_version=session.version,
                correlation_id=f"corr-{next(self.keys)}",
                require_wake_phrase=True,
                listen_mode=mode,
            )
        except ApplicationError:
            self._refused(before, len(entries_before))
            return
        # A command never commits by itself: state and records are unchanged.
        assert self._signature() == before
        recorded = [e for e in self._entries() if e.entry_type in RECORDED]
        assert len(recorded) == self.accepted_records
        if mode is not ListenMode.COMMAND:
            assert result.pending_action is None
            assert self._entries() == entries_before
            assert self._pending() == pending_before
        if result.parsed_command.intent == "ACKNOWLEDGE_ALARM":
            self.acknowledged.update(
                view.timer.id for view in self.service.list_timers(self.session_id)
                if view.timer.state.value == "ELAPSED"
            )
        assert "jarvis" not in result.response_text.casefold()

    @rule(kind=REPLY_KINDS)
    def voice_answer(self, kind: str) -> None:
        pending = self._pending()
        if not pending:
            return
        action = pending[0]
        session = self._session()
        with self.service._uow_factory() as uow:
            step_number = self.service._active_step_number(uow, session)
        expected = expected_readback(
            action.intent, dict(action.payload), step_number=step_number, voice_observations=True
        )
        phrase = expected.phrase() if expected is not None else "confirm"
        reply = {
            "valid": f"Jarvis, {phrase}",
            "bare": "Jarvis, yes",
            "confirm": "Jarvis, confirm",
            "unaddressed": phrase,
            "echo": f"Jarvis, say the wake word, then: {phrase}",
            "wrong": "Jarvis, confirm 99 millilitres",
            "second": f"Jarvis, {phrase} 77",
            "reject": "Jarvis, reject",
            "alarm": "Jarvis, stop the alarm",
        }[kind]
        before = self._signature()
        entries_before = len(self._entries())
        try:
            result = self.service.confirm_pending_action_by_voice(
                action.id,
                # A unique byte suffix: each reply is a new recording.
                f"FAKE:{reply}".encode() + b" " * next(self.audio_counter),
                content_type="audio/wav",
                idempotency_key=f"answer-{next(self.keys)}",
                correlation_id=f"corr-{next(self.keys)}",
                via_wake_phrase=True,
            )
        except ApplicationError:
            self._refused(before, entries_before)
            return
        if result.committed:
            assert kind == "valid" and expected is not None, (kind, reply, action.intent)
            if result.entry is not None and result.entry.entry_type in RECORDED:
                self.accepted_records += 1
        else:
            assert result.pending_action.status in {
                PendingActionStatus.PENDING,
                PendingActionStatus.REJECTED,
            }
            if result.pending_action.status is PendingActionStatus.REJECTED:
                assert kind == "reject"
            if result.pending_action.status is PendingActionStatus.PENDING:
                assert self._signature() == before

    @rule(seconds=st.sampled_from([30, 200, 400]))
    def advance_clock(self, seconds: int) -> None:
        self.clock.advance(seconds)

    @rule()
    def acknowledge_on_screen(self) -> None:
        elapsed = {
            view.timer.id
            for view in self.service.list_timers(self.session_id)
            if view.timer.state.value == "ELAPSED"
        }
        self._tool("acknowledge_timer_alarm")
        if self._session().state in {SessionState.READY, SessionState.ACTIVE, SessionState.PAUSED}:
            self.acknowledged.update(elapsed)

    @invariant()
    def acknowledged_alarms_stay_silent(self) -> None:
        for view in self.service.list_timers(self.session_id):
            if view.timer.id in self.acknowledged:
                assert view.alarm_active is False
            if view.timer.state.value != "ELAPSED":
                assert view.alarm_active is False

    # --- invariants ---------------------------------------------------------

    @invariant()
    def single_active_step(self) -> None:
        session = self._session()
        in_progress = [step for step in session.steps if step.state is StepState.IN_PROGRESS]
        assert len(in_progress) <= 1
        if session.active_step_id is None:
            assert not in_progress
        else:
            assert [step.step_id for step in in_progress] == [session.active_step_id]

    @invariant()
    def single_pending_action(self) -> None:
        assert len(self._pending()) <= 1

    @invariant()
    def version_tracks_state_changes(self) -> None:
        current = self._signature()
        previous = self.previous
        assert current[0] >= previous[0], "session version decreased"
        if current[1:] != previous[1:]:
            assert current[0] > previous[0], "state changed without a version increment"
        self.previous = current

    @invariant()
    def terminal_states_are_absorbing(self) -> None:
        state = self._session().state
        if self.terminal_reached is not None:
            assert state is self.terminal_reached
        elif state in TERMINAL:
            self.terminal_reached = state

    @invariant()
    def recorded_values_require_accepted_confirmation(self) -> None:
        recorded = [e for e in self._entries() if e.entry_type in RECORDED]
        assert len(recorded) == self.accepted_records

    @invariant()
    def skipped_steps_are_never_performed(self) -> None:
        session = self._session()
        for step in session.steps:
            if step.step_id in self.skipped_steps:
                assert step.state is StepState.SKIPPED_WITH_REASON
                assert step.completed_at is None
                assert step.skipped_reason


SessionMachine.TestCase.settings = settings(
    max_examples=EXAMPLES,
    stateful_step_count=25,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
TestSessionStateMachine = SessionMachine.TestCase
