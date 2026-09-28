"""Small transparent metrics used by the multimodal evaluation runner."""

from __future__ import annotations

from collections.abc import Mapping, Sequence


def _edit_distance(reference: Sequence[str], hypothesis: Sequence[str]) -> int:
    previous = list(range(len(hypothesis) + 1))
    for reference_index, reference_item in enumerate(reference, start=1):
        current = [reference_index]
        for hypothesis_index, hypothesis_item in enumerate(hypothesis, start=1):
            substitution = previous[hypothesis_index - 1] + (
                reference_item != hypothesis_item
            )
            insertion = current[hypothesis_index - 1] + 1
            deletion = previous[hypothesis_index] + 1
            current.append(min(substitution, insertion, deletion))
        previous = current
    return previous[-1]


def _words(value: str) -> tuple[str, ...]:
    return tuple(value.casefold().split())


def _characters(value: str) -> tuple[str, ...]:
    return tuple("".join(value.casefold().split()))


def word_error_rate(reference: str, hypothesis: str) -> float:
    reference_words = _words(reference)
    hypothesis_words = _words(hypothesis)
    return _edit_distance(reference_words, hypothesis_words) / max(1, len(reference_words))


def character_error_rate(reference: str, hypothesis: str) -> float:
    reference_characters = _characters(reference)
    hypothesis_characters = _characters(hypothesis)
    return _edit_distance(reference_characters, hypothesis_characters) / max(
        1, len(reference_characters)
    )


def exact_slot_match(expected: Mapping[str, str], observed: Mapping[str, str]) -> int:
    return int(dict(expected) == dict(observed))


def slot_counts(
    expected: Mapping[str, str],
    observed: Mapping[str, str],
) -> tuple[int, int, int]:
    expected_pairs = set(expected.items())
    observed_pairs = set(observed.items())
    true_positive = len(expected_pairs & observed_pairs)
    return (
        true_positive,
        len(observed_pairs - expected_pairs),
        len(expected_pairs - observed_pairs),
    )


def precision_recall_f1(
    true_positive: int,
    false_positive: int,
    false_negative: int,
) -> tuple[float, float, float]:
    precision = true_positive / max(1, true_positive + false_positive)
    recall = true_positive / max(1, true_positive + false_negative)
    f1 = 2 * precision * recall / max(1e-12, precision + recall)
    return precision, recall, f1
