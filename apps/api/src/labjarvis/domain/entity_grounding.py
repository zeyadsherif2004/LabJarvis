"""Deterministic checks that a model-proposed command is grounded in the utterance.

A language model's self-reported confidence is not evidence: small local models
emitted ``confidence: 100`` for schema-invalid output. What *can* be observed is
whether the consequential entities of a proposal - the number, the unit, the
timer duration, the observation text - actually appear in what the operator
said. A proposal whose value is not in the utterance was invented or converted
by the model, and must never become a pending action on the model's word alone.

Everything here is pure and deterministic. It never mutates state and never
widens what the command validator accepts; it can only withhold confidence.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

_UNITS_WORDS = {
    "zero": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}
_TENS_WORDS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}
_SCALE_WORDS = {"hundred": 100, "thousand": 1000}
_ARTICLE_ONE = {"a", "an"}

#: Surface forms that can justify each canonical unit. Deliberately excludes a
#: bare "degrees": without a scale the utterance is ambiguous and the frozen
#: benchmark (text-063) requires abstention.
UNIT_EVIDENCE: Mapping[str, tuple[str, ...]] = {
    "uL": ("ul", "µl", "μl", "microliter", "microliters", "microlitre", "microlitres", "mic"),
    "mL": ("ml", "mls", "milliliter", "milliliters", "millilitre", "millilitres", "mil", "mils"),
    "L": ("l", "liter", "liters", "litre", "litres"),
    "mg": ("mg", "milligram", "milligrams", "milligramme", "milligrammes"),
    "g": ("g", "gram", "grams", "gramme", "grammes"),
    "kg": ("kg", "kilogram", "kilograms", "kilo", "kilos"),
    "degC": ("°c", "degc", "celsius", "centigrade", "c", "ºc"),
    "K": ("k", "kelvin", "kelvins"),
    "s": ("s", "sec", "secs", "second", "seconds"),
    "min": ("min", "mins", "minute", "minutes"),
    "h": ("h", "hr", "hrs", "hour", "hours"),
    "rpm": ("rpm", "revolutions per minute"),
    "pH": ("ph",),
    "%": ("%", "percent", "per cent", "percentage"),
}

#: Measurement kinds that a unit alone makes unambiguous, so "it reads 37
#: degrees Celsius" may be recorded as a temperature without the word appearing.
_KIND_FROM_UNIT: Mapping[str, frozenset[str]] = {
    "degC": frozenset({"temperature"}),
    "K": frozenset({"temperature"}),
    "uL": frozenset({"volume"}),
    "mL": frozenset({"volume"}),
    "L": frozenset({"volume"}),
    "mg": frozenset({"mass", "weight"}),
    "g": frozenset({"mass", "weight"}),
    "kg": frozenset({"mass", "weight"}),
    "pH": frozenset({"ph"}),
    "rpm": frozenset({"speed"}),
}

_DURATION_FACTORS = {"s": 1, "min": 60, "h": 3600}

_TOKEN = re.compile(r"-?\d+(?:\.\d+)?|[a-zµμº°%]+", re.IGNORECASE)
_STOP = frozenset(
    {
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "that",
        "it",
        "its",
        "of",
        "to",
        "and",
        "as",
        "at",
        "in",
        "on",
        "with",
        "has",
        "have",
        "been",
        "be",
    }
)

# Ordinal evidence codes, not probabilities. They are compared against the
# application's command threshold (0.75) and recorded as the basis of acceptance.
DETERMINISTIC_MATCH = 0.99
GROUNDED_MODEL_PROPOSAL = 0.8
NO_EVIDENCE = 0.0


@dataclass(frozen=True, slots=True)
class GroundingResult:
    """Whether every consequential entity in a proposal appears in the utterance."""

    grounded: bool
    reason: str


def _normalise(text: str) -> str:
    lowered = text.casefold().replace("μ", "µ").replace("º", "°")
    lowered = re.sub(r"(\d),(\d{3})\b", r"\1\2", lowered)
    # Split glued numbers and units ("5ml", "37°c") without splitting decimals.
    lowered = re.sub(r"(\d)(?=[a-zµ°%])", r"\1 ", lowered)
    lowered = re.sub(r"\bdeg(?:rees?)?\s+c\b", "degc", lowered)
    # "twenty-one" and "five-minute" are word joins; "-20" keeps its sign.
    return re.sub(r"(?<=[a-z])-(?=[a-z0-9])", " ", lowered)


def tokens(text: str) -> list[str]:
    """Lower-case word, number, and unit-symbol tokens of an utterance."""

    return _TOKEN.findall(_normalise(text))


def _word_numbers(words: list[str]) -> list[Decimal]:
    """Decode spelled numbers such as 'thirty seven' or 'twenty one point five'."""

    found: list[Decimal] = []
    index = 0
    while index < len(words):
        word = words[index]
        if word not in _UNITS_WORDS and word not in _TENS_WORDS:
            index += 1
            continue
        total = 0
        current = 0
        consumed = index
        while consumed < len(words):
            token = words[consumed]
            if token in _UNITS_WORDS:
                current += _UNITS_WORDS[token]
            elif token in _TENS_WORDS:
                current += _TENS_WORDS[token]
            elif token in _SCALE_WORDS and current:
                current *= _SCALE_WORDS[token]
                if _SCALE_WORDS[token] >= 1000:
                    total += current
                    current = 0
            elif token == "and" and (current or total):
                pass
            else:
                break
            consumed += 1
        value = Decimal(total + current)
        if (
            consumed + 1 < len(words)
            and words[consumed] == "point"
            and words[consumed + 1] in _UNITS_WORDS
        ):
            digits = ""
            cursor = consumed + 1
            while cursor < len(words) and words[cursor] in _UNITS_WORDS:
                digits += str(_UNITS_WORDS[words[cursor]] % 10)
                cursor += 1
            value = Decimal(f"{total + current}.{digits}")
            consumed = cursor
        found.append(value)
        index = max(consumed, index + 1)
    return found


def utterance_numbers(text: str) -> set[Decimal]:
    """Every number an operator said, whether in digits or in words."""

    found: set[Decimal] = set()
    words = tokens(text)
    negative_next = False
    for token in words:
        if token in {"minus", "negative"}:
            negative_next = True
            continue
        try:
            value = Decimal(token)
        except InvalidOperation:
            negative_next = False
            continue
        found.add(-abs(value) if negative_next else value)
        negative_next = False
    for index, value in enumerate(_word_numbers(words)):
        del index
        found.add(value)
    joined = " ".join(words)
    for match in re.finditer(r"\b(?:minus|negative)\s+([a-z ]+)", joined):
        for value in _word_numbers(match.group(1).split()):
            found.add(-value)
            break
    if "half" in words:
        found.add(Decimal("0.5"))
    if any(word in _ARTICLE_ONE for word in words):
        found.add(Decimal(1))
    return found


def _has_phrase(text_tokens: list[str], phrase: str) -> bool:
    parts = phrase.split()
    width = len(parts)
    return any(text_tokens[i : i + width] == parts for i in range(len(text_tokens) - width + 1))


def unit_is_grounded(transcript: str, unit: str) -> bool:
    """Whether the utterance names the canonical unit through an accepted form."""

    evidence = UNIT_EVIDENCE.get(unit, ())
    text_tokens = tokens(transcript)
    normalised = _normalise(transcript)
    for form in evidence:
        if form in {"%", "°c", "ºc"}:
            if form in normalised:
                return True
            continue
        if _has_phrase(text_tokens, form):
            return True
    return False


def _content_words(text: str) -> list[str]:
    return [token for token in tokens(text) if token not in _STOP and not token.isdigit()]


def _value_is_grounded(transcript: str, raw_value: str) -> bool:
    try:
        value = Decimal(raw_value)
    except InvalidOperation:
        return False
    return value in utterance_numbers(transcript)


def _duration_is_grounded(transcript: str, raw_seconds: str) -> bool:
    try:
        seconds = Decimal(raw_seconds)
    except InvalidOperation:
        return False
    numbers = utterance_numbers(transcript)
    factors = [
        factor for unit, factor in _DURATION_FACTORS.items() if unit_is_grounded(transcript, unit)
    ]
    if not factors:
        return False
    singles = {number * factor for number in numbers for factor in factors}
    if seconds in singles:
        return True
    # "one hour and thirty minutes": any two stated components may sum.
    return any(a + b == seconds for a in singles for b in singles)


def ground_command(
    transcript: str,
    intent: str,
    parameters: Mapping[str, str],
) -> GroundingResult:
    """Check each consequential entity of a proposal against the utterance.

    Parameterless intents carry no entity to check and are grounded by
    definition; the application's state machine and confirmation still apply.
    """

    if intent == "RECORD_MEASUREMENT":
        value = parameters.get("value", "")
        unit = parameters.get("unit", "")
        kind = " ".join(parameters.get("kind", "").split()).casefold()
        if not _value_is_grounded(transcript, value):
            return GroundingResult(False, "the proposed value was not said")
        if not unit_is_grounded(transcript, unit):
            return GroundingResult(False, "the proposed unit was not said")
        kind_words = [word for word in kind.split() if word]
        spoken = set(tokens(transcript))
        if not kind_words or not (
            all(word in spoken for word in kind_words) or kind in _KIND_FROM_UNIT.get(unit, ())
        ):
            return GroundingResult(False, "the proposed quantity was not said")
        return GroundingResult(True, "value, unit and quantity appear in the utterance")
    if intent == "SET_TIMER":
        if not _duration_is_grounded(transcript, parameters.get("duration_seconds", "")):
            return GroundingResult(False, "the proposed duration was not said")
        return GroundingResult(True, "duration appears in the utterance")
    if intent == "RECORD_OBSERVATION":
        observed = _content_words(parameters.get("observation", ""))
        spoken = set(tokens(transcript))
        if not observed:
            return GroundingResult(False, "the observation is empty")
        overlap = sum(word in spoken for word in observed) / len(observed)
        if overlap < 0.6:
            return GroundingResult(False, "the observation text was not said")
        # A dictation has an instruction ("note that ...", "write down ...")
        # around the observed words. When the model files the whole utterance
        # as the observation there is no evidence it was a dictation at all.
        remainder = set(_content_words(transcript)) - set(observed)
        if not remainder:
            return GroundingResult(False, "no recording instruction accompanies the observation")
        return GroundingResult(True, "observation wording appears in the utterance")
    if intent == "CORRECT_PREVIOUS_ENTRY" and parameters:
        if not _value_is_grounded(transcript, parameters.get("value", "")):
            return GroundingResult(False, "the corrected value was not said")
        if not unit_is_grounded(transcript, parameters.get("unit", "")):
            return GroundingResult(False, "the corrected unit was not said")
        if not _words_were_said(transcript, parameters.get("target", ""), required=0.99):
            return GroundingResult(False, "the corrected quantity was not said")
        return GroundingResult(True, "corrected value, unit and target appear in the utterance")
    for field, intents in _TEXT_REFERENCES.items():
        if intent in intents and field in parameters:
            if not _words_were_said(transcript, parameters[field], required=0.6):
                return GroundingResult(False, f"the {field} was not said")
            return GroundingResult(True, f"the {field} appears in the utterance")
    return GroundingResult(True, "no consequential entity to check")


#: Free-text references a hands-free intent may carry, keyed by field. Each
#: must be (mostly) the operator's own words.
_TEXT_REFERENCES: Mapping[str, frozenset[str]] = {
    "reason": frozenset({"SKIP_STEP_WITH_REASON"}),
    "question": frozenset({"ANSWER_PROTOCOL_QUESTION"}),
    "reagent": frozenset({"VERIFY_REAGENT"}),
    "timer": frozenset({"PAUSE_TIMER", "RESUME_TIMER", "CANCEL_TIMER"}),
}


def _words_were_said(transcript: str, text: str, *, required: float) -> bool:
    """Whether at least ``required`` of the content words of ``text`` were said."""

    words = _content_words(text)
    if not words:
        return not text.strip()
    spoken = set(tokens(transcript))
    return sum(word in spoken for word in words) / len(words) >= required
