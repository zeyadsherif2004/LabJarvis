"""Resolve spoken references ("the 10 minute timer", "reagent two") deterministically.

A hands-free operator names things instead of clicking them. These functions
turn such a name into candidates from authoritative state; they never choose a
default. The caller asks again whenever there is not exactly one candidate.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from labjarvis.domain.models import ProtocolRequirement, SessionTimer
from labjarvis.domain.transcripts import _fold, salient_tokens

_SECONDS = {"s": 1, "min": 60, "h": 3600}
_NOISE = frozenset(
    {"the", "timer", "timers", "for", "on", "of", "my", "number", "reagent", "bottle", "label"}
)


def _name_tokens(text: str) -> tuple[str, ...]:
    """Tokens of a reagent name. Unlike command folding, single letters are kept:
    "buffer A" and "buffer B" are different reagents."""

    folded = (_fold(word) for word in text.split())
    return tuple(token for token in folded if token and token not in _NOISE)


def _is_number(token: str) -> bool:
    return token.replace(".", "", 1).isdigit()


def parse_timer_reference(reference: str) -> tuple[int | None, int | None, tuple[str, ...]]:
    """(duration in seconds, step number, label words) named by a reference."""

    tokens = salient_tokens(reference)
    duration = 0
    step: int | None = None
    words: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        following = tokens[index + 1] if index + 1 < len(tokens) else ""
        if token == "step" and _is_number(following):
            step = int(float(following))
            index += 2
            continue
        if _is_number(token) and following in _SECONDS:
            duration += round(float(token) * _SECONDS[following])
            index += 2
            continue
        if token not in _NOISE and not _is_number(token) and token not in _SECONDS:
            words.append(token)
        index += 1
    return (duration or None), step, tuple(words)


def match_timers(
    timers: Sequence[SessionTimer],
    reference: str,
    step_numbers: Mapping[str, int],
) -> list[SessionTimer]:
    """Timers consistent with every part of the reference (all when it is empty)."""

    duration, step, words = parse_timer_reference(reference)
    matches: list[SessionTimer] = []
    for timer in timers:
        if duration is not None and timer.duration_seconds != duration:
            continue
        if step is not None and step_numbers.get(timer.step_id or "") != step:
            continue
        if words and not set(words) <= set(salient_tokens(timer.label)):
            continue
        matches.append(timer)
    return matches


def match_requirement(
    requirements: Sequence[ProtocolRequirement],
    reference: str,
) -> ProtocolRequirement | None:
    """The one requirement a spoken name, alias or position refers to, else ``None``."""

    tokens = _name_tokens(reference)
    if not tokens:
        return None
    if len(tokens) == 1 and _is_number(tokens[0]):
        position = int(float(tokens[0]))
        return requirements[position - 1] if 1 <= position <= len(requirements) else None
    spoken = set(tokens)
    exact: list[ProtocolRequirement] = []
    partial: list[ProtocolRequirement] = []
    for requirement in requirements:
        for name in (requirement.name, *requirement.aliases):
            words = set(_name_tokens(name))
            if not words:
                continue
            if words == spoken:
                exact.append(requirement)
                break
            if spoken <= words or words <= spoken:
                partial.append(requirement)
                break
    if len(exact) == 1:
        return exact[0]
    if not exact and len(partial) == 1:
        return partial[0]
    return None
