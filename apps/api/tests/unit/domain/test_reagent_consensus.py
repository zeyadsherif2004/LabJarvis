"""Deterministic multi-frame reagent consensus."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from labjarvis.domain.models import VerificationOutcome
from labjarvis.domain.reagent import classify_reagent
from labjarvis.domain.reagent_consensus import (
    FrameReading,
    majority_consensus,
    unanimous_consensus,
)


def frame(text: str, expected: str = "ethanol") -> FrameReading:
    return FrameReading(
        visible_text=text,
        comparison=classify_reagent(text, expected, match_threshold=92, uncertain_threshold=72),
    )


def test_agreeing_matches_give_match() -> None:
    result = unanimous_consensus([frame("ETHANOL"), frame("Ethanol"), frame("ethanol")])

    assert result.outcome is VerificationOutcome.MATCH
    assert result.algorithm == "unanimous-v1"


def test_agreeing_mismatches_give_mismatch() -> None:
    result = unanimous_consensus([frame("METHANOL")] * 3)

    assert result.outcome is VerificationOutcome.MISMATCH


def test_disagreeing_frames_are_uncertain_never_a_vote() -> None:
    result = unanimous_consensus([frame("ETHANOL"), frame("ETHANOL"), frame("METHANOL")])

    assert result.outcome is VerificationOutcome.UNCERTAIN
    assert "disagree" in result.reason
    assert "ETHANOL" in result.observed_text and "METHANOL" in result.observed_text


def test_an_unreadable_frame_makes_the_result_uncertain() -> None:
    result = unanimous_consensus([frame("ETHANOL"), frame(""), frame("ETHANOL")])

    assert result.outcome is VerificationOutcome.UNCERTAIN


def test_single_frame_behaves_exactly_like_the_single_frame_comparator() -> None:
    for text in ("ETHANOL", "METHANOL", "", "ETHAN0L"):
        single = frame(text)
        assert unanimous_consensus([single]).outcome is single.comparison.outcome


def test_empty_input_is_rejected() -> None:
    with pytest.raises(ValueError):
        unanimous_consensus([])


def test_majority_ablation_breaks_ties_conservatively() -> None:
    assert majority_consensus([frame("ETHANOL"), frame("METHANOL")]) is (
        VerificationOutcome.UNCERTAIN
    )
    assert majority_consensus([frame("ETHANOL")] * 2 + [frame("METHANOL")]) is (
        VerificationOutcome.MATCH
    )


LABELS = st.sampled_from(
    ["ETHANOL", "Ethanol", "METHANOL", "ETHAN0L", "", "ACETONE", "ethanol 70%"]
)


@given(st.lists(LABELS, min_size=1, max_size=5))
def test_consensus_never_matches_unless_every_frame_matched(texts: list[str]) -> None:
    frames = [frame(text) for text in texts]
    result = unanimous_consensus(frames)

    if result.outcome is VerificationOutcome.MATCH:
        assert all(f.comparison.outcome is VerificationOutcome.MATCH for f in frames)
    if any(f.comparison.outcome is not VerificationOutcome.MATCH for f in frames):
        assert result.outcome is not VerificationOutcome.MATCH
