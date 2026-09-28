"""Deterministic hands-free domain rules: controls, references, read-back, grounding."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from labjarvis.domain.entity_grounding import ground_command
from labjarvis.domain.grounding import SPOKEN_ABSTENTION
from labjarvis.domain.models import ProtocolRequirement, SessionTimer, TimerState
from labjarvis.domain.references import match_requirement, match_timers, parse_timer_reference
from labjarvis.domain.voice_confirmation import (
    DUAL_AGREED,
    VoiceDecision,
    expected_readback,
    judge_voice_only_confirmation,
)
from labjarvis.domain.voice_controls import (
    CONTROL_RESPONSES,
    ListenMode,
    VoiceControl,
    control_allowed,
    voice_control,
)

# ---------------------------------------------------------------- controls


@pytest.mark.parametrize(
    ("body", "control"),
    [
        ("stop listening", VoiceControl.STOP_LISTENING),
        ("start listening", VoiceControl.START_LISTENING),
        ("please mute", VoiceControl.MUTE_SPEECH),
        ("unmute", VoiceControl.UNMUTE_SPEECH),
        ("say that again", VoiceControl.REPEAT_LAST_SPOKEN),
        ("stop", VoiceControl.STOP_SPEAKING),
        ("quiet!", VoiceControl.STOP_SPEAKING),
        ("stop the alarm", VoiceControl.ACKNOWLEDGE_ALARM),
        ("timer done", VoiceControl.ACKNOWLEDGE_ALARM),
    ],
)
def test_controls_are_exact_phrases(body: str, control: VoiceControl) -> None:
    assert voice_control(body) is control


@pytest.mark.parametrize(
    "body", ["stop the centrifuge", "stop recording the volume", "do not stop", "mute the stirrer"]
)
def test_a_control_word_inside_a_sentence_is_not_a_control(body: str) -> None:
    assert voice_control(body) is None


def test_listening_modes_restrict_controls() -> None:
    assert control_allowed(VoiceControl.START_LISTENING, ListenMode.STANDBY)
    assert not control_allowed(VoiceControl.MUTE_SPEECH, ListenMode.STANDBY)
    assert control_allowed(VoiceControl.STOP_SPEAKING, ListenMode.BARGE_IN)
    assert control_allowed(VoiceControl.ACKNOWLEDGE_ALARM, ListenMode.BARGE_IN)
    assert not control_allowed(VoiceControl.STOP_LISTENING, ListenMode.BARGE_IN)
    assert all(control_allowed(control, ListenMode.COMMAND) for control in VoiceControl)


def test_control_responses_never_contain_a_wake_phrase() -> None:
    for text in CONTROL_RESPONSES.values():
        assert "jarvis" not in text.casefold()


# -------------------------------------------------------------- references


def _timer(
    timer_id: str, seconds: int, label: str = "timer", step: str | None = None
) -> SessionTimer:
    now = datetime(2026, 9, 27, tzinfo=UTC)
    return SessionTimer(
        id=timer_id,
        session_id="s",
        step_id=step,
        label=label,
        duration_seconds=seconds,
        state=TimerState.RUNNING,
        created_at=now,
        started_at=now,
        deadline_at=now,
        remaining_seconds_at_pause=None,
        paused_at=None,
        resolved_at=None,
        correlation_id="c",
        version=0,
    )


def test_timer_references() -> None:
    assert parse_timer_reference("ten minute") == (600, None, ())
    assert parse_timer_reference("1 hour 30 minute") == (5400, None, ())
    assert parse_timer_reference("for step 2") == (None, 2, ())
    timers = [
        _timer("a", 600, "incubation", "s1"),
        _timer("b", 300, "timer", "s2"),
        _timer("c", 600, "wash", "s2"),
    ]
    steps = {"s1": 1, "s2": 2}
    assert [t.id for t in match_timers(timers, "", steps)] == ["a", "b", "c"]
    assert [t.id for t in match_timers(timers, "10 minute", steps)] == ["a", "c"]
    assert [t.id for t in match_timers(timers, "10 minute step 2", steps)] == ["c"]
    assert [t.id for t in match_timers(timers, "incubation", steps)] == ["a"]
    assert match_timers(timers, "3 minute", steps) == []


def test_requirement_references() -> None:
    ethanol = ProtocolRequirement("r1", "ethanol", aliases=("EtOH",))
    buffer = ProtocolRequirement("r2", "buffer A", aliases=("PBS",))
    buffer_b = ProtocolRequirement("r3", "buffer B")
    requirements = (ethanol, buffer, buffer_b)
    assert match_requirement(requirements, "ethanol") is ethanol
    assert match_requirement(requirements, "EtOH") is ethanol
    assert match_requirement(requirements, "two") is buffer
    assert match_requirement(requirements, "3") is buffer_b
    assert match_requirement(requirements, "buffer a") is buffer
    assert match_requirement(requirements, "buffer") is None  # two candidates: ask
    assert match_requirement(requirements, "acetone") is None
    assert match_requirement(requirements, "9") is None


# -------------------------------------------------------------- read-back


def _judge(reply: str, intent: str, payload: dict[str, str], **kwargs: object) -> VoiceDecision:
    expected = expected_readback(intent, payload, **kwargs)  # type: ignore[arg-type]
    return judge_voice_only_confirmation(reply, wake_phrase="Jarvis", expected=expected).decision


@pytest.mark.parametrize(
    ("intent", "payload", "kwargs", "phrase", "wrong"),
    [
        ("SKIP_STEP_WITH_REASON", {"reason": "x"}, {"step_number": 3}, "confirm skip step 3",
         "confirm step 3"),
        ("CORRECT_PREVIOUS_ENTRY", {"value": "12", "unit": "mL"}, {}, "confirm 12 millilitres",
         "confirm 12 microlitres"),
        ("CANCEL_TIMER", {"timer_duration_seconds": "600"}, {}, "confirm cancel 10 minute timer",
         "confirm cancel 5 minute timer"),
        ("PAUSE_TIMER", {}, {}, "confirm pause timer", "confirm pause"),
        ("RESUME_TIMER", {}, {}, "confirm resume timer", "confirm resume session"),
        ("START_SESSION", {}, {}, "confirm start session", "confirm start timer"),
        ("RESUME_SESSION", {}, {}, "confirm resume session", "confirm resume timer"),
        ("RECORD_OBSERVATION", {"heard_agreement": DUAL_AGREED}, {"voice_observations": True},
         "confirm observation", "confirm observation it was blue"),
    ],
)  # fmt: skip
def test_new_readbacks(
    intent: str, payload: dict[str, str], kwargs: dict[str, object], phrase: str, wrong: str
) -> None:
    expected = expected_readback(intent, payload, **kwargs)  # type: ignore[arg-type]
    assert expected is not None and expected.phrase() == phrase
    assert "jarvis" not in phrase
    assert _judge(f"Jarvis, {phrase}", intent, payload, **kwargs) is VoiceDecision.CONFIRM
    assert _judge(f"Jarvis, {wrong}", intent, payload, **kwargs) is not VoiceDecision.CONFIRM
    assert _judge(phrase, intent, payload, **kwargs) is VoiceDecision.NOT_ADDRESSED
    for bare in ("Jarvis, yes", "Jarvis, confirm", "Jarvis, OK"):
        assert _judge(bare, intent, payload, **kwargs) is not VoiceDecision.CONFIRM
    assert _judge(f"Jarvis, say {phrase}", intent, payload, **kwargs) is VoiceDecision.AMBIGUOUS


def test_cancel_alone_still_rejects_a_timer_cancellation() -> None:
    payload = {"timer_duration_seconds": "600"}
    assert _judge("Jarvis, cancel", "CANCEL_TIMER", payload) is VoiceDecision.REJECT
    assert _judge("Jarvis, reject", "CANCEL_TIMER", payload) is VoiceDecision.REJECT


def test_observation_readback_needs_the_opt_in_and_dual_agreement() -> None:
    assert expected_readback("RECORD_OBSERVATION", {"heard_agreement": DUAL_AGREED}) is None
    assert expected_readback("RECORD_OBSERVATION", {}, voice_observations=True) is None


def test_a_cancel_without_a_known_duration_is_not_voice_only() -> None:
    assert expected_readback("CANCEL_TIMER", {}) is None


# ---------------------------------------------------------------- grounding


def _grounded(utterance: str, intent: str, **parameters: str) -> bool:
    return ground_command(utterance, intent, parameters).grounded


def test_new_fields_must_be_said() -> None:
    skip = "Jarvis, skip this step because the reagent is unavailable"
    assert _grounded(skip, "SKIP_STEP_WITH_REASON", reason="reagent unavailable")
    assert not _grounded(skip, "SKIP_STEP_WITH_REASON", reason="the lab is on fire")
    question = "how long do I incubate"
    assert _grounded(question, "ANSWER_PROTOCOL_QUESTION", question=question)
    assert not _grounded(question, "ANSWER_PROTOCOL_QUESTION", question="what is the boiling point")
    fix = "correct the volume to 12 millilitres"
    assert _grounded(fix, "CORRECT_PREVIOUS_ENTRY", target="volume", value="12", unit="mL")
    assert not _grounded(fix, "CORRECT_PREVIOUS_ENTRY", target="volume", value="21", unit="mL")
    assert not _grounded(fix, "CORRECT_PREVIOUS_ENTRY", target="mass", value="12", unit="mL")
    assert not _grounded("cancel the timer", "CANCEL_TIMER", timer="incubation")


def test_the_spoken_abstention_is_plain() -> None:
    assert SPOKEN_ABSTENTION == "I couldn't find that in the approved protocol."


def test_hyphenated_durations_are_equivalent_readings() -> None:
    from labjarvis.domain.transcripts import TranscriptAgreement, compare_transcripts

    comparison = compare_transcripts(
        "Jarvis, cancel the 10-minute timer.", "Jarvis, cancel the 10 minute timer."
    )
    assert comparison.agreement is TranscriptAgreement.EQUIVALENT
    assert parse_timer_reference("10-minute") == (600, None, ())
    # Only digit-letter joins are split: a different number still conflicts.
    conflict = compare_transcripts("cancel the 10-minute timer", "cancel the 5 minute timer")
    assert conflict.agreement is TranscriptAgreement.CONFLICT


def test_finish_is_an_accepted_alternative_for_ending() -> None:
    assert _judge("Jarvis, confirm finish session", "END_SESSION", {}) is VoiceDecision.CONFIRM
    paused = _judge("Jarvis, confirm finish session", "PAUSE_SESSION", {})
    assert paused is not VoiceDecision.CONFIRM
    assert _judge("Jarvis, confirm finish timer", "END_SESSION", {}) is not VoiceDecision.CONFIRM
