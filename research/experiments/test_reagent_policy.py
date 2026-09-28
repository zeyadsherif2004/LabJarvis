"""Branch-coverage tests for the published reagent decision function.

Each test maps to a branch of the reagent decision rule and pins the
behaviour of the production ``classify_reagent`` at the shipped thresholds
(uncertain=72, match=92). Run:

    .venv/Scripts/python -m pytest research/experiments/test_reagent_policy.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "api" / "src"))

from labjarvis.domain.reagent import classify_reagent

TAU_U = 72.0
TAU_M = 92.0


def outcome(observed: str, expected: str, *, aliases: tuple[str, ...] = ()) -> str:
    return classify_reagent(
        observed,
        expected,
        aliases=aliases,
        match_threshold=TAU_M,
        uncertain_threshold=TAU_U,
    ).outcome.value


# Branch 1: exact normalised equality -> MATCH.
def test_exact_equality_matches() -> None:
    assert outcome("ethanol", "ethanol") == "MATCH"


def test_equality_is_case_and_punctuation_insensitive() -> None:
    assert outcome("  Ethanol. ", "ethanol") == "MATCH"


# Branch 1b: curated alias -> MATCH.
def test_alias_matches() -> None:
    assert outcome("etoh", "ethanol", aliases=("etoh",)) == "MATCH"


# Branch 2: empty OCR -> UNCERTAIN (abstain).
def test_empty_ocr_abstains() -> None:
    assert outcome("", "ethanol") == "UNCERTAIN"


# Branch 3: similar alphabetic label above tau_u -> MISMATCH (plausible wrong reagent).
def test_similar_chemical_name_is_mismatch() -> None:
    assert outcome("methanol", "ethanol") == "MISMATCH"


def test_one_letter_chemical_confusion_is_mismatch() -> None:
    assert outcome("sodium chlorate", "sodium chloride") == "MISMATCH"


# Branch 4: near-miss containing a digit/symbol above tau_u -> UNCERTAIN (abstain).
def test_digit_defacement_below_equality_but_nonalpha_abstains() -> None:
    # "ethano1" (digit one) is not purely alphabetic and is not exactly equal.
    assert outcome("ethano1", "ethanol") == "UNCERTAIN"


# Branch 5: low similarity -> MISMATCH.
def test_dissimilar_label_is_mismatch() -> None:
    assert outcome("acetone", "ethanol") == "MISMATCH"


# July evaluation failure: OCR normalises a defacement to the correct spelling,
# producing exact equality the comparator cannot distinguish
# from a genuine match. This is an upstream (OCR) error, not a policy branch.
def test_ocr_normalised_defacement_is_indistinguishable_match() -> None:
    # If OCR returns the correctly spelled name, the comparator sees equality.
    assert outcome("methanol", "methanol") == "MATCH"


# tau_m tightening branch: only reachable when the expected name is not purely
# alphabetic. On the all-alphabetic benchmark this branch is inert; this test
# documents that it exists and fires for a non-alphabetic expected string.
def test_tau_m_branch_fires_only_for_nonalpha_expected() -> None:
    # Expected contains a digit, so the alpha-and-alpha branch does not apply;
    # a high-scoring alphabetic observation is tightened to MISMATCH by tau_m.
    high = classify_reagent(
        "sodium chloride",
        "sodium chloride5",
        aliases=(),
        match_threshold=TAU_M,
        uncertain_threshold=TAU_U,
    )
    assert high.score >= TAU_M
    assert high.outcome.value == "MISMATCH"


@pytest.mark.parametrize(
    "observed,expected,expected_outcome",
    [
        ("ethanol", "ethanol", "MATCH"),
        ("methanol", "ethanol", "MISMATCH"),
        ("sodium chloride", "sodium chloride", "MATCH"),
        ("sodium chlorate", "sodium chloride", "MISMATCH"),
        ("", "acetone", "UNCERTAIN"),
        ("ethano1", "ethanol", "UNCERTAIN"),
    ],
)
def test_representative_edge_cases(observed: str, expected: str, expected_outcome: str) -> None:
    assert outcome(observed, expected) == expected_outcome


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
