import pytest

from labjarvis.evaluation.metrics import (
    character_error_rate,
    exact_slot_match,
    precision_recall_f1,
    slot_counts,
    word_error_rate,
)


def test_word_error_rate_counts_substitutions_insertions_and_deletions() -> None:
    assert word_error_rate("add ten millilitres ethanol", "add ten millilitres ethanol") == 0
    assert word_error_rate("add ten millilitres ethanol", "add five millilitres ethanol") == 0.25
    assert word_error_rate("", "unexpected") == 1


def test_character_error_rate_normalises_case_and_space() -> None:
    assert character_error_rate("ETHANOL", "ethanol") == 0
    assert character_error_rate("ethanol", "ethano1") == pytest.approx(1 / 7)


def test_exact_slot_match_requires_the_complete_mapping() -> None:
    expected = {"kind": "temperature", "value": "37", "unit": "degrees Celsius"}

    assert exact_slot_match(expected, dict(expected)) == 1
    assert exact_slot_match(expected, {"kind": "temperature", "value": "37"}) == 0


def test_slot_precision_recall_and_f1_count_exact_key_value_pairs() -> None:
    counts = slot_counts(
        {"kind": "temperature", "value": "37", "unit": "degC"},
        {"kind": "temperature", "value": "38", "unit": "degC"},
    )

    assert counts == (2, 1, 1)
    assert precision_recall_f1(*counts) == pytest.approx((2 / 3, 2 / 3, 2 / 3))
