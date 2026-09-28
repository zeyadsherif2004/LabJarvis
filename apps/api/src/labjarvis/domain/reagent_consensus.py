"""Deterministic multi-frame consensus for visible-label verification.

A single OCR reading of a hand-held bottle is unstable: glare, motion and
curvature change what is read from one frame to the next. Capturing several
frames and requiring them to agree turns that instability into an explicit
abstention instead of a lucky or unlucky single reading.

Algorithm ``unanimous-v1`` (the production rule):

1. Each frame is compared on its own with the unchanged single-frame
   comparator (``classify_reagent``).
2. If any frame is ``UNCERTAIN`` (including an empty reading), the result is
   ``UNCERTAIN``: an unreadable frame is not evidence either way.
3. If the frames' outcomes are not all identical, the result is ``UNCERTAIN``
   ("frames disagree") - never a vote.
4. Otherwise the shared outcome is returned. For ``MATCH`` every frame matched
   the expected name or an alias; for ``MISMATCH`` every frame contradicted it.

The consensus can only be *more* conservative than any single frame: it never
produces ``MATCH`` unless every frame did. ``majority-v1`` exists solely as a
research ablation and is not used by the application.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from labjarvis.domain.models import ReagentComparison, VerificationOutcome

CONSENSUS_ALGORITHM = "unanimous-v1"
MAX_FRAMES = 5


@dataclass(frozen=True, slots=True)
class FrameReading:
    visible_text: str
    comparison: ReagentComparison


@dataclass(frozen=True, slots=True)
class ConsensusResult:
    outcome: VerificationOutcome
    observed_text: str
    observed_normalised: str
    score: float
    reason: str
    algorithm: str
    frame_outcomes: tuple[VerificationOutcome, ...]


def _summary_text(frames: Sequence[FrameReading]) -> str:
    readings: list[str] = []
    for frame in frames:
        text = frame.visible_text.strip() or "(no text)"
        if text not in readings:
            readings.append(text)
    return " | ".join(readings)[:250]


def unanimous_consensus(frames: Sequence[FrameReading]) -> ConsensusResult:
    """Apply ``unanimous-v1``. Requires at least one frame."""

    if not frames:
        raise ValueError("consensus requires at least one frame")
    outcomes = tuple(frame.comparison.outcome for frame in frames)
    lowest_score = min(frame.comparison.score for frame in frames)
    if len(frames) == 1:
        only = frames[0]
        return ConsensusResult(
            outcome=only.comparison.outcome,
            observed_text=only.visible_text,
            observed_normalised=only.comparison.observed_normalised,
            score=only.comparison.score,
            reason="single frame",
            algorithm="single-frame",
            frame_outcomes=outcomes,
        )
    if VerificationOutcome.UNCERTAIN in outcomes:
        return ConsensusResult(
            outcome=VerificationOutcome.UNCERTAIN,
            observed_text=_summary_text(frames),
            observed_normalised="",
            score=lowest_score,
            reason="at least one frame was unreadable or uncertain",
            algorithm=CONSENSUS_ALGORITHM,
            frame_outcomes=outcomes,
        )
    if len(set(outcomes)) != 1:
        return ConsensusResult(
            outcome=VerificationOutcome.UNCERTAIN,
            observed_text=_summary_text(frames),
            observed_normalised="",
            score=lowest_score,
            reason="frames disagree; hold the label still and recapture",
            algorithm=CONSENSUS_ALGORITHM,
            frame_outcomes=outcomes,
        )
    commonest = Counter(frame.comparison.observed_normalised for frame in frames).most_common(1)
    representative = next(
        frame for frame in frames if frame.comparison.observed_normalised == commonest[0][0]
    )
    return ConsensusResult(
        outcome=outcomes[0],
        observed_text=representative.visible_text,
        observed_normalised=representative.comparison.observed_normalised,
        score=lowest_score,
        reason=f"all {len(frames)} frames agree",
        algorithm=CONSENSUS_ALGORITHM,
        frame_outcomes=outcomes,
    )


def majority_consensus(frames: Sequence[FrameReading]) -> VerificationOutcome:
    """Research ablation only: strict-majority vote with ties -> UNCERTAIN."""

    counts = Counter(frame.comparison.outcome for frame in frames)
    outcome, votes = counts.most_common(1)[0]
    if votes * 2 <= len(frames):
        return VerificationOutcome.UNCERTAIN
    return outcome
