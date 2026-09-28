from hypothesis import given
from hypothesis import strategies as st

from labjarvis.domain.commands import validate_and_normalise_command
from labjarvis.domain.models import ParsedCommand


def command(intent: str, parameters: dict[str, str]) -> ParsedCommand:
    return ParsedCommand(intent=intent, parameters=parameters, confidence=0.95)


def test_measurement_value_and_unit_are_normalised_deterministically() -> None:
    parsed = validate_and_normalise_command(
        command(
            "RECORD_MEASUREMENT",
            {"kind": "Temperature", "value": "37.00", "unit": "degrees Celsius"},
        )
    )

    assert parsed.intent == "RECORD_MEASUREMENT"
    assert parsed.parameters == {"kind": "temperature", "value": "37", "unit": "degC"}


@given(st.sampled_from(["mL", "millilitres", "ML", "milliliters"]))
def test_measurement_unit_normalisation_is_idempotent(unit: str) -> None:
    once = validate_and_normalise_command(
        command("RECORD_MEASUREMENT", {"kind": "volume", "value": "1.5", "unit": unit})
    )
    twice = validate_and_normalise_command(once)

    assert once == twice
    assert once.parameters["unit"] == "mL"


def test_unknown_units_and_impossible_temperatures_are_rejected() -> None:
    unknown = validate_and_normalise_command(
        command("RECORD_MEASUREMENT", {"kind": "mass", "value": "2", "unit": "buckets"})
    )
    impossible = validate_and_normalise_command(
        command("RECORD_MEASUREMENT", {"kind": "temperature", "value": "-300", "unit": "degC"})
    )

    assert unknown.intent == "UNSUPPORTED"
    assert impossible.intent == "UNSUPPORTED"


def test_timer_is_bounded_to_seven_days() -> None:
    valid = validate_and_normalise_command(command("SET_TIMER", {"duration_seconds": "60"}))
    invalid = validate_and_normalise_command(
        command("SET_TIMER", {"duration_seconds": "604801"})
    )

    assert valid.parameters == {"duration_seconds": "60"}
    assert invalid.intent == "UNSUPPORTED"
