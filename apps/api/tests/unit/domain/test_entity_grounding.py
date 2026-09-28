"""Deterministic grounding of model-proposed entities in the utterance."""

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from labjarvis.domain.entity_grounding import (
    ground_command,
    unit_is_grounded,
    utterance_numbers,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("record the temperature as -20 degrees celsius", {Decimal(-20)}),
        ("it is thirty-seven degrees C", {Decimal(37)}),
        ("twenty one point five mL", {Decimal("21.5")}),
        ("1,500 rpm", {Decimal(1500)}),
        ("minus four degrees celsius", {Decimal(-4), Decimal(4)}),
        ("pH 7.4", {Decimal("7.4")}),
    ],
)
def test_numbers_are_read_in_digits_and_words(text: str, expected: set[Decimal]) -> None:
    assert expected <= utterance_numbers(text)


def test_bare_degrees_does_not_ground_celsius() -> None:
    assert not unit_is_grounded("record the temperature as 37 degrees", "degC")
    assert unit_is_grounded("record the temperature as 37 degrees celsius", "degC")
    assert unit_is_grounded("37°C", "degC")


@pytest.mark.parametrize(
    ("transcript", "parameters", "grounded"),
    [
        (
            "log the temperature at 37 celsius",
            {"kind": "temperature", "value": "37", "unit": "degC"},
            True,
        ),
        (
            "it reads 37 degrees celsius",
            {"kind": "temperature", "value": "37", "unit": "degC"},
            True,
        ),
        ("record the volume as 5 mL", {"kind": "volume", "value": "50", "unit": "mL"}, False),
        ("record the volume as 50 gallons", {"kind": "volume", "value": "50", "unit": "L"}, False),
        ("record the mass as 5 grams", {"kind": "volume", "value": "5", "unit": "g"}, False),
        ("record 98.6 fahrenheit", {"kind": "temperature", "value": "37", "unit": "degC"}, False),
    ],
)
def test_measurements(transcript: str, parameters: dict[str, str], grounded: bool) -> None:
    assert ground_command(transcript, "RECORD_MEASUREMENT", parameters).grounded is grounded


@pytest.mark.parametrize(
    ("transcript", "seconds", "grounded"),
    [
        ("start a five minute countdown", "300", True),
        ("timer for one hour and thirty minutes", "5400", True),
        ("set a timer for half an hour", "1800", True),
        ("set a timer for 5 minutes", "3000", False),
        ("set a timer", "60", False),
    ],
)
def test_timer_durations(transcript: str, seconds: str, grounded: bool) -> None:
    result = ground_command(transcript, "SET_TIMER", {"duration_seconds": seconds})

    assert result.grounded is grounded


def test_observation_needs_its_words_and_a_dictation_instruction() -> None:
    assert ground_command(
        "note that the solution is cloudy",
        "RECORD_OBSERVATION",
        {"observation": "the solution is cloudy"},
    ).grounded
    assert not ground_command(
        "tell me a joke", "RECORD_OBSERVATION", {"observation": "tell me a joke"}
    ).grounded
    assert not ground_command(
        "note that it is fine", "RECORD_OBSERVATION", {"observation": "a blue precipitate"}
    ).grounded


def test_parameterless_intents_are_grounded_by_definition() -> None:
    assert ground_command("hold on", "PAUSE_SESSION", {}).grounded


@given(st.integers(min_value=-500, max_value=10_000), st.sampled_from(["mL", "uL", "g"]))
def test_a_value_is_grounded_only_when_that_value_was_said(value: int, unit: str) -> None:
    spoken = {"mL": "millilitres", "uL": "microlitres", "g": "grams"}[unit]
    transcript = f"record the amount as {value} {spoken}"

    assert ground_command(
        transcript, "RECORD_MEASUREMENT", {"kind": "amount", "value": str(value), "unit": unit}
    ).grounded
    assert not ground_command(
        transcript,
        "RECORD_MEASUREMENT",
        {"kind": "amount", "value": str(value * 10 + 7), "unit": unit},
    ).grounded
