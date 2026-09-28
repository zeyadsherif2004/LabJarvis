"""Deterministic rules for value-bearing, wake-phrase confirmations."""

import pytest

from labjarvis.domain.confirmations import check_measurement_readback, read_confirmation
from labjarvis.domain.voice_confirmation import (
    VoiceDecision,
    expected_readback,
    judge_voice_only_confirmation,
    reagent_readback,
)
from labjarvis.domain.wake_phrase import strip_wake_phrase

MEASUREMENT = expected_readback("RECORD_MEASUREMENT", {"value": "37", "unit": "degC"})
TIMER = expected_readback("SET_TIMER", {"duration_seconds": "5400"})


def judge(words: str, expected=MEASUREMENT):  # type: ignore[no-untyped-def]
    return judge_voice_only_confirmation(words, wake_phrase="Jarvis", expected=expected).decision


@pytest.mark.parametrize(
    ("words", "decision"),
    [
        ("Jarvis, confirm 37 degrees Celsius", VoiceDecision.CONFIRM),
        ("hey Jarvis confirm thirty 7 degrees celsius", VoiceDecision.AMBIGUOUS),
        ("Jarvis, confirm 37 degrees", VoiceDecision.AMBIGUOUS),  # scale not named
        ("Jarvis, confirm 37 kelvin", VoiceDecision.AMBIGUOUS),
        ("Jarvis, that is not correct", VoiceDecision.AMBIGUOUS),  # mixed: never commits
        ("Jarvis, no", VoiceDecision.REJECT),
        ("Jarvis, confirm not 37 celsius", VoiceDecision.AMBIGUOUS),
        ("Jarvis", VoiceDecision.NOT_ADDRESSED),
        ("Travis, confirm 37 celsius", VoiceDecision.NOT_ADDRESSED),
    ],
)
def test_measurement_rules(words: str, decision: VoiceDecision) -> None:
    assert judge(words) is decision


@pytest.mark.parametrize(
    ("words", "decision"),
    [
        ("Jarvis, confirm 1 hour 30 minutes", VoiceDecision.CONFIRM),
        ("Jarvis, confirm 90 minutes", VoiceDecision.CONFIRM),
        ("Jarvis, confirm 5400 seconds", VoiceDecision.CONFIRM),
        ("Jarvis, confirm 1 hour", VoiceDecision.AMBIGUOUS),
        ("Jarvis, confirm minutes", VoiceDecision.AMBIGUOUS),
        ("Jarvis, confirm 90", VoiceDecision.AMBIGUOUS),
    ],
)
def test_duration_rules(words: str, decision: VoiceDecision) -> None:
    assert judge(words, TIMER) is decision


def test_prompts_spell_out_the_phrase_without_the_wake_phrase() -> None:
    assert MEASUREMENT is not None and TIMER is not None
    assert MEASUREMENT.phrase() == "confirm 37 degrees Celsius"
    assert TIMER.phrase() == "confirm 1 hour 30 minutes"
    assert "jarvis" not in MEASUREMENT.phrase().casefold()


def test_reagent_aliases_are_accepted_but_other_names_are_not() -> None:
    expected = reagent_readback("Ethanol absolute", ("EtOH",))
    assert judge("Jarvis, confirm ethanol absolute", expected) is VoiceDecision.CONFIRM
    assert judge("Jarvis, confirm EtOH", expected) is VoiceDecision.CONFIRM
    assert judge("Jarvis, confirm ethanol", expected) is VoiceDecision.AMBIGUOUS


def test_unsupported_intents_have_no_voice_phrase() -> None:
    assert expected_readback("RECORD_OBSERVATION", {"observation": "x"}) is None
    assert expected_readback("COMPLETE_STEP", {}) is None  # step number unknown
    verdict = judge_voice_only_confirmation("Jarvis, confirm", wake_phrase="Jarvis", expected=None)
    assert verdict.decision is VoiceDecision.NOT_SUPPORTED


@pytest.mark.parametrize(
    ("transcript", "body"),
    [
        ("Jarvis, confirm 10 mL", "confirm 10 ml"),
        ("OK Jarvis confirm", "confirm"),
        ("tell Jarvis to confirm", None),
        ("Jarvis", None),
    ],
)
def test_strip_wake_phrase(transcript: str, body: str | None) -> None:
    assert strip_wake_phrase(transcript, "Jarvis") == body


def test_push_to_talk_readback_accepts_celsius_and_refuses_two_values() -> None:
    ok = check_measurement_readback(
        read_confirmation("confirm 37 degrees celsius"), expected_value="37", expected_unit="degC"
    )
    assert ok.accepted
    two = check_measurement_readback(
        read_confirmation("confirm 37 or 38 celsius"), expected_value="37", expected_unit="degC"
    )
    assert not two.accepted


TWENTY_ONE = expected_readback("RECORD_MEASUREMENT", {"value": "21", "unit": "degC"})


@pytest.mark.parametrize(
    ("words", "decision"),
    [
        # Transcribed exactly this way during a real bench run: the read-back
        # was refused every time because "twenty-one" was not read as a number.
        ("Jarvis, confirm twenty-one degrees Celsius.", VoiceDecision.CONFIRM),
        ("Jarvis, confirm twenty one degrees Celsius", VoiceDecision.CONFIRM),
        ("Jarvis, confirm 21 degrees Celsius.", VoiceDecision.CONFIRM),
        ("Jarvis, confirm twenty-two degrees Celsius.", VoiceDecision.AMBIGUOUS),
        ("Jarvis, confirm twenty degrees Celsius", VoiceDecision.AMBIGUOUS),
        ("Jarvis, confirm two one degrees Celsius", VoiceDecision.AMBIGUOUS),
    ],
)
def test_spoken_compound_numbers_confirm_only_the_same_value(
    words: str, decision: VoiceDecision
) -> None:
    assert judge(words, TWENTY_ONE) is decision
