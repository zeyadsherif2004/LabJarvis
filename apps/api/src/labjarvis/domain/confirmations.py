"""Deciding, in ordinary code, whether a person confirmed something.

A spoken confirmation is the last thing standing between a model's proposal and
a laboratory record. It therefore cannot be decided by a model - not by the
realtime model that produced the proposal, and not by any other model either.
This module reads a transcript of one human utterance and returns one of three
answers:

* **confirm** - an unambiguous affirmation;
* **reject** - an unambiguous refusal, or a negation;
* **ambiguous** - anything else: silence, hedging, an affirmation and a refusal
  in the same breath, or an affirmation carrying a negation.

Ambiguity is never resolved in favour of proceeding, and every uncertain case is
pushed towards *reject* rather than *confirm*, because a wrongly rejected
proposal costs one repetition while a wrongly confirmed one puts a number the
operator never agreed to into a laboratory record.

The readback rule carries the most weight. "Yes" confirms that the operator
heard *something*; "confirm ten millilitres" confirms that they heard the same
number the system is about to write down. For measurements only the second is
accepted, because the failure this design exists to prevent is 5 mL being
recorded as 50 mL after a confident-sounding readback.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from labjarvis.domain.transcripts import (
    NEGATION_WORDS,
    UNIT_SYNONYMS,
    UNIT_TOKENS,
    salient_tokens,
)

#: Words that affirm. Deliberately short and unambiguous; anything conditional,
#: hedged, or interrogative is left out so it lands in ``AMBIGUOUS`` instead.
AFFIRMATIONS = frozenset(
    {
        "affirmative",
        "confirm",
        "confirmed",
        "correct",
        "right",
        "yeah",
        "yep",
        "yes",
        "yup",
    }
)

#: Words that refuse outright.
REFUSALS = frozenset(
    {
        "abort",
        "cancel",
        "discard",
        "incorrect",
        "negative",
        "no",
        "nope",
        "reject",
        "wrong",
    }
)

#: Words that make an utterance non-committal whatever else it contains.
HEDGES = frozenset(
    {
        "actually",
        "approximately",
        "guess",
        "hold",
        "maybe",
        "perhaps",
        "probably",
        "roughly",
        "sorry",
        "think",
        "unsure",
        "wait",
    }
)

#: Canonical unit spellings. ``salient_tokens`` has already folded synonyms, so
#: a readback is matched against the folded form on both sides.
CANONICAL_UNITS = frozenset(UNIT_SYNONYMS.values()) | UNIT_TOKENS

_NUMBER = re.compile(r"^\d+(?:\.\d+)?$")

#: Spoken tokens (after transcript folding) that restate each canonical unit.
#: "degrees Celsius" folds to ("deg", "c"); "deg" alone does not name a scale.
UNIT_READBACK_TOKENS: dict[str, frozenset[str]] = {
    "uL": frozenset({"ul"}),
    "mL": frozenset({"ml"}),
    "L": frozenset({"l"}),
    "mg": frozenset({"mg"}),
    "g": frozenset({"g"}),
    "kg": frozenset({"kg"}),
    "degC": frozenset({"c"}),
    "K": frozenset({"k", "kelvin"}),
    "s": frozenset({"s"}),
    "min": frozenset({"min"}),
    "h": frozenset({"h"}),
    "rpm": frozenset({"rpm"}),
    "pH": frozenset({"ph"}),
    "%": frozenset({"%", "percent"}),
}

#: Spoken decimals: "ten point five" must fold to 10.5 before comparison, or a
#: correct readback of a fractional value would be rejected as a mismatch.
_DECIMAL_WORDS = frozenset({"point", "decimal"})


class ConfirmationIntent(StrEnum):
    CONFIRM = "confirm"
    REJECT = "reject"
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class ConfirmationReading:
    """What one confirmation utterance was understood to mean, and why."""

    intent: ConfirmationIntent
    #: Values the operator restated, normalised. Empty when they restated none.
    restated_values: tuple[str, ...]
    #: Units the operator restated, folded to canonical spellings.
    restated_units: tuple[str, ...]
    reasons: tuple[str, ...]

    @property
    def is_confirmation(self) -> bool:
        return self.intent is ConfirmationIntent.CONFIRM


def normalise_number(token: str) -> str | None:
    """Fold a numeric token so 10, 10.0, and 10.00 compare equal."""

    try:
        value = Decimal(token)
    except (InvalidOperation, ValueError):
        return None
    return format(value.normalize(), "f")


def _join_spoken_decimals(tokens: tuple[str, ...]) -> tuple[str, ...]:
    """Fold ``10 point 5`` into a single ``10.5`` token."""

    joined: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if (
            _NUMBER.match(token)
            and index + 2 < len(tokens)
            and tokens[index + 1] in _DECIMAL_WORDS
            and _NUMBER.match(tokens[index + 2])
        ):
            joined.append(f"{token}.{tokens[index + 2]}")
            index += 3
            continue
        joined.append(token)
        index += 1
    return tuple(joined)


def read_confirmation(transcript: str) -> ConfirmationReading:
    """Classify one human utterance as confirming, refusing, or neither."""

    tokens = _join_spoken_decimals(salient_tokens(transcript))
    values = tuple(
        normalised
        for token in tokens
        if _NUMBER.match(token)
        for normalised in (normalise_number(token),)
        if normalised is not None
    )
    units = tuple(token for token in tokens if token in CANONICAL_UNITS)

    def reading(intent: ConfirmationIntent, *reasons: str) -> ConfirmationReading:
        return ConfirmationReading(
            intent=intent,
            restated_values=values,
            restated_units=units,
            reasons=tuple(reasons),
        )

    if not tokens:
        return reading(ConfirmationIntent.AMBIGUOUS, "nothing was heard")

    affirmed = [token for token in tokens if token in AFFIRMATIONS]
    refused = [token for token in tokens if token in REFUSALS]
    negated = [token for token in tokens if token in NEGATION_WORDS]
    hedged = [token for token in tokens if token in HEDGES]

    if hedged:
        return reading(
            ConfirmationIntent.AMBIGUOUS,
            f"the reply was not committal: it contained {hedged[0]!r}",
        )
    if affirmed and (refused or negated):
        # "that is not correct" contains an affirmation word. Reading it as a
        # confirmation would be the worst possible mistake this module can make.
        return reading(
            ConfirmationIntent.AMBIGUOUS,
            "the reply mixed an affirmation with a refusal or a negation",
        )
    if refused or negated:
        return reading(ConfirmationIntent.REJECT)
    if affirmed:
        return reading(ConfirmationIntent.CONFIRM)
    return reading(
        ConfirmationIntent.AMBIGUOUS,
        "the reply was neither a clear yes nor a clear no",
    )


@dataclass(frozen=True, slots=True)
class ReadbackVerdict:
    """Whether a confirmation restated the value that is about to be recorded."""

    accepted: bool
    reasons: tuple[str, ...]


def check_measurement_readback(
    reading: ConfirmationReading,
    *,
    expected_value: str,
    expected_unit: str,
) -> ReadbackVerdict:
    """Require a measurement confirmation to restate the number being written.

    A bare "yes" is refused here on purpose. It proves the operator replied; it
    does not prove they heard the same number the system is holding. Making them
    say the value back is the only available check that distinguishes those two,
    and it is the one that catches a misheard order of magnitude.
    """

    expected = normalise_number(expected_value.strip())
    if expected is None:
        return ReadbackVerdict(
            accepted=False,
            reasons=("the proposed value is not a number, so it cannot be read back",),
        )
    if not reading.restated_values:
        return ReadbackVerdict(
            accepted=False,
            reasons=(
                "the reply did not repeat the value; say the number and unit back to "
                "confirm it",
            ),
        )
    if len(set(reading.restated_values)) > 1:
        return ReadbackVerdict(
            accepted=False,
            reasons=(
                f"the reply contained more than one value ({', '.join(reading.restated_values)}); "
                "say only the value being recorded",
            ),
        )
    if expected not in reading.restated_values:
        return ReadbackVerdict(
            accepted=False,
            reasons=(
                f"the value repeated back ({', '.join(reading.restated_values)}) is not "
                f"the value being recorded ({expected})",
            ),
        )

    spoken_unit = expected_unit.strip().casefold()
    folded_unit = UNIT_SYNONYMS.get(spoken_unit, spoken_unit)
    wanted_units = UNIT_READBACK_TOKENS.get(expected_unit.strip(), frozenset({folded_unit}))
    restated = set(reading.restated_units) - {"deg"}
    if restated and folded_unit and restated - wanted_units:
        return ReadbackVerdict(
            accepted=False,
            reasons=(
                f"the unit repeated back ({', '.join(reading.restated_units)}) is not "
                f"the unit being recorded ({expected_unit.strip() or folded_unit})",
            ),
        )
    return ReadbackVerdict(accepted=True, reasons=())


#: Intents whose spoken confirmation must restate the value, not merely affirm.
READBACK_REQUIRED_INTENTS = frozenset({"RECORD_MEASUREMENT", "CORRECT_PREVIOUS_ENTRY"})


def readback_prompt(intent: str, payload: dict[str, str]) -> str:
    """The exact phrase the operator should say, spelled out for them."""

    if intent in READBACK_REQUIRED_INTENTS:
        value = payload.get("value", "").strip()
        unit = payload.get("unit", "").strip()
        return f"Say: confirm {value} {unit}".strip()
    return "Say: confirm, or say: reject"
