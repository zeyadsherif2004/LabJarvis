"""Transparent, conservative visible-label comparison."""

import re
import unicodedata
from difflib import SequenceMatcher

from labjarvis.domain.models import ReagentComparison, VerificationOutcome

NON_WORD = re.compile(r"[^\w]+", flags=re.UNICODE)
WHITESPACE = re.compile(r"\s+")


def normalise_reagent(value: str) -> str:
    """Normalize visible label text without making risky OCR substitutions."""

    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = NON_WORD.sub(" ", normalized)
    return WHITESPACE.sub(" ", normalized).strip()


def _score(observed: str, expected: str) -> float:
    if not observed or not expected:
        return 0.0
    return round(SequenceMatcher(None, observed, expected).ratio() * 100, 2)


def _is_alpha_label(value: str) -> bool:
    return bool(value) and all(character.isalpha() or character.isspace() for character in value)


def classify_reagent(
    observed: str,
    expected: str,
    *,
    aliases: tuple[str, ...] = (),
    match_threshold: float,
    uncertain_threshold: float,
) -> ReagentComparison:
    """Classify OCR text with a bias against false acceptance."""

    observed_normalised = normalise_reagent(observed)
    expected_normalised = normalise_reagent(expected)
    aliases_normalised = tuple(normalise_reagent(alias) for alias in aliases)
    score = _score(observed_normalised, expected_normalised)

    if not observed_normalised:
        outcome = VerificationOutcome.UNCERTAIN
        matched_alias = None
    elif observed_normalised == expected_normalised:
        outcome = VerificationOutcome.MATCH
        score = 100.0
        matched_alias = None
    elif observed_normalised in aliases_normalised:
        outcome = VerificationOutcome.MATCH
        score = 100.0
        matched_alias = observed_normalised
    elif (
        score >= uncertain_threshold
        and _is_alpha_label(observed_normalised)
        and _is_alpha_label(expected_normalised)
    ):
        outcome = VerificationOutcome.MISMATCH
        matched_alias = None
    elif score >= uncertain_threshold:
        outcome = VerificationOutcome.UNCERTAIN
        matched_alias = None
    else:
        outcome = VerificationOutcome.MISMATCH
        matched_alias = None

    if score >= match_threshold and outcome is not VerificationOutcome.MATCH:
        outcome = VerificationOutcome.MISMATCH if _is_alpha_label(observed_normalised) else outcome

    return ReagentComparison(
        outcome=outcome,
        score=score,
        expected_normalised=expected_normalised,
        observed_normalised=observed_normalised,
        matched_alias=matched_alias,
    )
