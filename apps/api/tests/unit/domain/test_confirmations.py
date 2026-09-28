"""Reading a human confirmation without a model in the loop.

The bias of every case here is one-directional: when in doubt, do not confirm.
A wrongly rejected proposal costs one repetition; a wrongly confirmed one writes
a number the operator never agreed to into a laboratory record.
"""

import pytest

from labjarvis.domain.confirmations import (
    ConfirmationIntent,
    check_measurement_readback,
    read_confirmation,
    readback_prompt,
)


@pytest.mark.parametrize(
    "transcript",
    ["yes", "Yes.", "confirm", "correct", "yep", "affirmative", "confirm 10 millilitres"],
)
def test_plain_affirmations_are_confirmations(transcript: str) -> None:
    assert read_confirmation(transcript).intent is ConfirmationIntent.CONFIRM


@pytest.mark.parametrize(
    "transcript",
    ["no", "reject", "cancel that", "wrong", "nope", "incorrect", "abort"],
)
def test_plain_refusals_are_rejections(transcript: str) -> None:
    assert read_confirmation(transcript).intent is ConfirmationIntent.REJECT


@pytest.mark.parametrize(
    "transcript",
    [
        "",
        "   ",
        "maybe",
        "I think so",
        "yes no",
        "no wait yes",
        "that is not correct",
        "roughly ten millilitres",
        "what did you say",
    ],
)
def test_anything_unclear_is_ambiguous(transcript: str) -> None:
    assert read_confirmation(transcript).intent is ConfirmationIntent.AMBIGUOUS


def test_a_negated_affirmation_is_never_a_confirmation() -> None:
    """"That is not correct" contains the word "correct". It is not a yes."""

    assert read_confirmation("that is not correct").intent is not ConfirmationIntent.CONFIRM


def test_spoken_numbers_and_units_are_normalised() -> None:
    reading = read_confirmation("confirm ten millilitres")
    assert reading.restated_values == ("10",)
    assert reading.restated_units == ("ml",)


def test_spoken_decimals_survive_normalisation() -> None:
    reading = read_confirmation("confirm ten point five millilitres")
    assert reading.restated_values == ("10.5",)


# ------------------------------------------------------------------ readback


def test_a_matching_readback_is_accepted() -> None:
    reading = read_confirmation("confirm 10 mL")
    verdict = check_measurement_readback(reading, expected_value="10", expected_unit="mL")
    assert verdict.accepted


def test_trailing_zeros_do_not_break_a_correct_readback() -> None:
    reading = read_confirmation("confirm 10.0 mL")
    assert check_measurement_readback(
        reading, expected_value="10", expected_unit="mL"
    ).accepted


def test_a_bare_yes_fails_the_readback() -> None:
    verdict = check_measurement_readback(
        read_confirmation("yes"), expected_value="10", expected_unit="mL"
    )
    assert not verdict.accepted
    assert "repeat the value" in verdict.reasons[0]


def test_an_order_of_magnitude_error_fails_the_readback() -> None:
    """The case this whole design exists for: 10 mL confirmed back as 50 mL."""

    verdict = check_measurement_readback(
        read_confirmation("confirm 50 mL"), expected_value="10", expected_unit="mL"
    )
    assert not verdict.accepted


def test_a_wrong_unit_fails_the_readback() -> None:
    verdict = check_measurement_readback(
        read_confirmation("confirm 10 microlitres"),
        expected_value="10",
        expected_unit="mL",
    )
    assert not verdict.accepted


def test_an_equivalent_unit_spelling_passes() -> None:
    assert check_measurement_readback(
        read_confirmation("confirm 10 millilitres"),
        expected_value="10",
        expected_unit="mL",
    ).accepted


def test_a_non_numeric_proposal_cannot_be_read_back() -> None:
    verdict = check_measurement_readback(
        read_confirmation("confirm 10 mL"), expected_value="about ten", expected_unit="mL"
    )
    assert not verdict.accepted


def test_the_prompt_spells_out_what_to_say() -> None:
    prompt = readback_prompt("RECORD_MEASUREMENT", {"value": "10", "unit": "mL"})
    assert prompt == "Say: confirm 10 mL"
    assert readback_prompt("COMPLETE_STEP", {}) == "Say: confirm, or say: reject"
