"""Two transcripts of one utterance must never be silently reconciled.

When a realtime transcript and a high-accuracy transcript disagree about
something that changes the experimental record - a number, a unit, a negation -
the correct behaviour is to stop and ask, not to pick a winner. These tests pin
where that line falls, because drawing it too strictly makes the assistant
useless and too loosely makes it dangerous.
"""

import pytest

from labjarvis.domain.transcripts import (
    TranscriptAgreement,
    compare_transcripts,
    salient_tokens,
    spoken_quantities_to_digits,
)


def _agreement(first: str, second: str) -> TranscriptAgreement:
    return compare_transcripts(first, second).agreement


# --------------------------------------------------------------- equivalence


def test_identical_transcripts_agree() -> None:
    assert _agreement("record 10 mL of ethanol", "record 10 mL of ethanol") is (
        TranscriptAgreement.IDENTICAL
    )


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("Record 10 mL", "record 10 ml"),
        ("record  10   mL ", " record 10 mL"),
        ("record 10 mL.", "record 10 mL"),
        ("record ten mL", "record 10 mL"),
        ("record 10 millilitres", "record 10 mL"),
        ("set a timer for five minutes", "set a timer for 5 minutes"),
    ],
)
def test_wording_differences_that_cannot_change_the_record_are_equivalent(
    first: str, second: str
) -> None:
    # Casing, spacing, punctuation, spoken numerals, and unit synonyms all
    # normalise to the same salient content, so there is nothing to ask about.
    assert _agreement(first, second) is TranscriptAgreement.EQUIVALENT


def test_filler_words_do_not_count_as_disagreement() -> None:
    assert _agreement("um, record 10 mL please", "record 10 mL") is (
        TranscriptAgreement.EQUIVALENT
    )


# ------------------------------------------------------------------ conflict


@pytest.mark.parametrize(
    ("first", "second", "reason"),
    [
        ("record 5 mL", "record 50 mL", "value"),
        ("record 10 mL", "record 10 uL", "unit"),
        ("record 1.5 mL", "record 15 mL", "value"),
        ("record 0.5 mL", "record 5 mL", "value"),
        ("set a timer for 5 minutes", "set a timer for 5 hours", "unit"),
        ("record pH 7", "record pH 11", "value"),
    ],
)
def test_a_number_or_unit_difference_is_always_a_conflict(
    first: str, second: str, reason: str
) -> None:
    result = compare_transcripts(first, second)

    assert result.agreement is TranscriptAgreement.CONFLICT
    assert reason in " ".join(result.reasons)
    assert result.canonical is None, "a conflict must not resolve to a winner"


def test_a_negation_difference_is_a_conflict() -> None:
    # "do not complete" versus "complete" inverts the instruction entirely.
    result = compare_transcripts("do not complete the step", "complete the step")

    assert result.agreement is TranscriptAgreement.CONFLICT
    assert "negation" in " ".join(result.reasons)


def test_a_missing_number_is_a_conflict_rather_than_an_assumed_value() -> None:
    result = compare_transcripts("record the volume", "record 10 mL")

    assert result.agreement is TranscriptAgreement.CONFLICT


def test_a_conflict_reports_both_readings_so_the_operator_can_choose() -> None:
    result = compare_transcripts("record 5 mL", "record 50 mL")

    joined = " ".join(result.reasons)
    assert "5" in joined
    assert "50" in joined


# ----------------------------------------------------------- non-numeric drift


def test_different_words_without_numbers_are_flagged_but_not_a_number_conflict() -> None:
    result = compare_transcripts("add the ethanol slowly", "add the methanol slowly")

    assert result.agreement is TranscriptAgreement.CONFLICT
    assert "wording" in " ".join(result.reasons)


def test_a_blank_secondary_transcript_leaves_the_primary_unconfirmed() -> None:
    result = compare_transcripts("record 10 mL", "")

    assert result.agreement is TranscriptAgreement.UNAVAILABLE
    assert result.canonical is None


# --------------------------------------------------------------- canonicalisation


def test_agreement_selects_the_high_accuracy_transcript_as_canonical() -> None:
    # Both readings mean the same thing; the more accurate path supplies the
    # wording that gets stored, so the record has one unambiguous source.
    result = compare_transcripts("record ten ml", "record 10 mL")

    assert result.agreement is TranscriptAgreement.EQUIVALENT
    assert result.canonical == "record 10 mL"


def test_salient_tokens_ignore_filler_but_keep_numbers_units_and_negations() -> None:
    tokens = salient_tokens("um, please do not record 10 mL")

    assert "10" in tokens
    assert "ml" in tokens
    assert "not" in tokens
    assert "um" not in tokens
    assert "please" not in tokens


def test_spelled_and_digit_numbers_from_two_transcribers_agree() -> None:
    comparison = compare_transcripts(
        "Jarvis, confirm twenty-one degrees Celsius.",
        "Jarvis, confirm 21 degrees Celsius.",
    )

    assert comparison.agreement is TranscriptAgreement.EQUIVALENT
    assert comparison.canonical == "Jarvis, confirm 21 degrees Celsius."


@pytest.mark.parametrize(
    ("spoken", "tokens"),
    [
        ("add one hundred and five millilitres", ("add", "105", "ml")),
        ("two thousand five hundred rpm", ("2500", "rpm")),
        ("seventy-five minutes", ("75", "min")),
        # Separate values are never merged into a different number.
        ("record one two", ("record", "1", "2")),
        ("five and ten", ("5", "10")),
        ("thirty 7 celsius", ("30", "7", "c")),
    ],
)
def test_spoken_numbers_are_composed_strictly(spoken: str, tokens: tuple[str, ...]) -> None:
    assert salient_tokens(spoken) == tokens


def test_different_spelled_values_still_conflict() -> None:
    comparison = compare_transcripts("confirm twenty-one celsius", "confirm 31 celsius")

    assert comparison.agreement is TranscriptAgreement.CONFLICT


@pytest.mark.parametrize(
    ("spoken", "written"),
    [
        (
            "record the temperature as twenty-one degrees Celsius",
            "record the temperature as 21 degrees Celsius",
        ),
        ("set a timer for one minute", "set a timer for 1 minute"),
        ("add five millilitres", "add 5 millilitres"),
        # no unit after the number: left alone
        ("confirm step one", "confirm step one"),
        ("the one on the left", "the one on the left"),
        # part of a longer value: never rewritten on its own
        ("thirty seven point five degrees", "thirty seven point five degrees"),
        ("one two millilitres", "one two millilitres"),
        ("minus five degrees", "minus five degrees"),
    ],
)
def test_spoken_quantities_become_digits_only_before_a_unit(spoken: str, written: str) -> None:
    assert spoken_quantities_to_digits(spoken) == written
