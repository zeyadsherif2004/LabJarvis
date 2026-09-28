import pytest
from hypothesis import given
from hypothesis import strategies as st

from labjarvis.domain.models import VerificationOutcome
from labjarvis.domain.reagent import classify_reagent, normalise_reagent


@pytest.mark.parametrize(
    ("observed", "expected", "outcome"),
    [
        ("ETHANOL", "ethanol", VerificationOutcome.MATCH),
        ("  Ethanol (EtOH)  ", "ethanol etoh", VerificationOutcome.MATCH),
        ("methanol", "ethanol", VerificationOutcome.MISMATCH),
        ("sodium chlorite", "sodium chloride", VerificationOutcome.MISMATCH),
        ("ethano1", "ethanol", VerificationOutcome.UNCERTAIN),
        ("", "ethanol", VerificationOutcome.UNCERTAIN),
    ],
)
def test_conservative_reagent_classification(
    observed: str,
    expected: str,
    outcome: VerificationOutcome,
) -> None:
    result = classify_reagent(
        observed,
        expected,
        match_threshold=92,
        uncertain_threshold=72,
    )

    assert result.outcome is outcome
    assert 0 <= result.score <= 100
    assert result.expected_normalised == normalise_reagent(expected)
    assert result.observed_normalised == normalise_reagent(observed)


def test_exact_protocol_alias_is_a_match() -> None:
    result = classify_reagent(
        "EtOH",
        "ethanol",
        aliases=("ethyl alcohol", "EtOH"),
        match_threshold=92,
        uncertain_threshold=72,
    )

    assert result.outcome is VerificationOutcome.MATCH
    assert result.matched_alias == "etoh"


@given(st.text())
def test_reagent_normalisation_is_idempotent(value: str) -> None:
    assert normalise_reagent(normalise_reagent(value)) == normalise_reagent(value)
