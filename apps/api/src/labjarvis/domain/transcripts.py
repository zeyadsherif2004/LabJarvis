"""Deterministic comparison of two transcripts of the same utterance.

LabJarvis can hear one utterance twice: once through the low-latency realtime
path, and once through a higher-accuracy transcription model. Where those two
readings differ, something has to decide what the experimental record says.

That decision is made here, in ordinary code, and it is deliberately blunt:

* differences that *cannot* change what gets recorded - casing, spacing,
  punctuation, filler words, "ten" versus "10", "millilitres" versus "mL" -
  are treated as equivalent, because asking the operator to repeat themselves
  over those would make hands-free operation unusable;
* any difference in a number, a unit, a negation, or the substantive words is a
  conflict, and a conflict has no winner. The caller must ask the operator
  rather than choose.

No model, and no heuristic about which transcript "seems better", participates.
A wrong guess here writes a wrong number into a laboratory notebook.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

NUMBER_PATTERN = re.compile(r"^\d+(?:\.\d+)?$")
_DIGIT_HYPHEN = re.compile(r"(\d)-(?=[^\W\d_])")
NON_WORD = re.compile(r"[^\w.\-]+", flags=re.UNICODE)

#: Words that never change the meaning of a laboratory command.
FILLER_WORDS = frozenset(
    {
        "a",
        "ah",
        "an",
        "and",
        "eh",
        "er",
        "erm",
        "hmm",
        "just",
        "kindly",
        "of",
        "ok",
        "okay",
        "please",
        "so",
        "the",
        "then",
        "uh",
        "um",
        "well",
    }
)

#: Words that invert an instruction. Never treated as filler.
NEGATION_WORDS = frozenset({"no", "not", "dont", "doesnt", "cannot", "cant", "never", "without"})

SPOKEN_NUMBERS = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
    "eleven": "11",
    "twelve": "12",
    "thirteen": "13",
    "fourteen": "14",
    "fifteen": "15",
    "sixteen": "16",
    "seventeen": "17",
    "eighteen": "18",
    "nineteen": "19",
    "twenty": "20",
    "thirty": "30",
    "forty": "40",
    "fifty": "50",
    "sixty": "60",
    "seventy": "70",
    "eighty": "80",
    "ninety": "90",
    "hundred": "100",
    "thousand": "1000",
}

#: Unit synonyms collapse to one spelling. Only synonyms of the *same* unit
#: appear here; millilitres and microlitres stay distinct, which is the whole
#: point of comparing them.
UNIT_SYNONYMS = {
    "millilitre": "ml",
    "millilitres": "ml",
    "milliliter": "ml",
    "milliliters": "ml",
    "mls": "ml",
    "microlitre": "ul",
    "microlitres": "ul",
    "microliter": "ul",
    "microliters": "ul",
    "µl": "ul",
    "μl": "ul",
    "litre": "l",
    "litres": "l",
    "liter": "l",
    "liters": "l",
    "gram": "g",
    "grams": "g",
    "milligram": "mg",
    "milligrams": "mg",
    "kilogram": "kg",
    "kilograms": "kg",
    "second": "s",
    "seconds": "s",
    "secs": "s",
    "sec": "s",
    "minute": "min",
    "minutes": "min",
    "mins": "min",
    "hour": "h",
    "hours": "h",
    "hrs": "h",
    "hr": "h",
    "degree": "deg",
    "degrees": "deg",
    "celsius": "c",
    "centigrade": "c",
}

#: Tokens recognised as units after synonym folding.
UNIT_TOKENS = frozenset(
    {"ml", "ul", "l", "g", "mg", "kg", "s", "min", "h", "deg", "c", "k", "ph", "rpm", "%"}
)


class TranscriptAgreement(StrEnum):
    """How two readings of one utterance relate."""

    #: Byte-identical.
    IDENTICAL = "identical"
    #: Different wording, identical meaning for anything that could be recorded.
    EQUIVALENT = "equivalent"
    #: They differ in a way that could change the record. There is no winner.
    CONFLICT = "conflict"
    #: Only one reading exists, so nothing has been cross-checked.
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class TranscriptComparison:
    """The verdict, plus why, plus the wording to store when there is one."""

    agreement: TranscriptAgreement
    #: The transcript to record. ``None`` whenever the caller must ask instead.
    canonical: str | None
    reasons: tuple[str, ...]

    @property
    def requires_operator_decision(self) -> bool:
        return self.agreement in {TranscriptAgreement.CONFLICT, TranscriptAgreement.UNAVAILABLE}


def _fold(token: str) -> str:
    normalised = unicodedata.normalize("NFKC", token).casefold()
    normalised = NON_WORD.sub("", normalised).strip(".")
    normalised = SPOKEN_NUMBERS.get(normalised, normalised)
    return UNIT_SYNONYMS.get(normalised, normalised)


_UNIT_WORDS = {word: int(value) for word, value in SPOKEN_NUMBERS.items() if int(value) < 10}
_TEEN_WORDS = {word: int(value) for word, value in SPOKEN_NUMBERS.items() if 10 <= int(value) < 20}
_TENS_WORDS = {
    word: int(value) for word, value in SPOKEN_NUMBERS.items() if 20 <= int(value) < 100
}
_NUMBER_WORD_HYPHEN = re.compile(
    r"\b("
    + "|".join(_TENS_WORDS)
    + r")-("
    + "|".join(w for w in _UNIT_WORDS if w != "zero")
    + r")\b",
    flags=re.IGNORECASE,
)


def _below_hundred(words: list[str], index: int) -> tuple[int, int] | None:
    """Parse "twenty one", "fifteen", "seven" starting at ``index``."""

    if index >= len(words):
        return None
    word = words[index]
    if word in _TENS_WORDS:
        value = _TENS_WORDS[word]
        following = words[index + 1] if index + 1 < len(words) else None
        if following in _UNIT_WORDS and following != "zero":
            return value + _UNIT_WORDS[following], index + 2
        return value, index + 1
    if word in _TEEN_WORDS:
        return _TEEN_WORDS[word], index + 1
    if word in _UNIT_WORDS:
        return _UNIT_WORDS[word], index + 1
    return None


def _below_thousand(words: list[str], index: int) -> tuple[int, int] | None:
    """Parse "three hundred [and] twenty one" or anything below a hundred."""

    if index < len(words) and words[index] == "hundred":
        value, index = 100, index + 1
    elif (
        index + 1 < len(words)
        and words[index] in _UNIT_WORDS
        and words[index] != "zero"
        and words[index + 1] == "hundred"
    ):
        value, index = _UNIT_WORDS[words[index]] * 100, index + 2
    else:
        return _below_hundred(words, index)
    # "and" is only part of a number directly after "hundred".
    rest_start = index + 1 if index < len(words) and words[index] == "and" else index
    rest = _below_hundred(words, rest_start)
    if rest is not None:
        return value + rest[0], rest[1]
    return value, index


def _spoken_number(words: list[str], index: int) -> tuple[int, int] | None:
    """Parse one spoken English number up to the thousands, strictly.

    Only well-formed English numbers are joined, so a list of separate values
    ("one two", "five ten") is never merged into a different number - which
    would put a wrong value into the record.
    """

    if index < len(words) and words[index] == "thousand":
        value, index = 1000, index + 1
    else:
        head = _below_thousand(words, index)
        if head is None:
            return None
        value, index = head
        if index < len(words) and words[index] == "thousand":
            value, index = value * 1000, index + 1
        else:
            return value, index
    rest_start = index + 1 if index < len(words) and words[index] == "and" else index
    rest = _below_thousand(words, rest_start)
    if rest is not None:
        return value + rest[0], rest[1]
    return value, index


def _compose_spoken_numbers(transcript: str) -> str:
    """Rewrite spoken numbers as digits: "twenty-one" and "twenty one" become "21".

    Transcription models disagree about this - one writes "twenty-one" where
    another writes "21" - so without it the same spoken value compared as a
    conflict, and a hyphenated compound was not read as a number at all.
    """

    transcript = _NUMBER_WORD_HYPHEN.sub(r"\1 \2", transcript)
    raw = transcript.split()
    words = [NON_WORD.sub("", unicodedata.normalize("NFKC", item).casefold()) for item in raw]
    output: list[str] = []
    index = 0
    while index < len(raw):
        parsed = _spoken_number(words, index)
        # A single word already folds on its own; only rewrite real compounds,
        # and keep punctuation that ended the number ("twenty-one." -> "21.").
        if parsed is not None and parsed[1] - index > 1:
            value, end = parsed
            trailing = re.sub(r"^[\w\-]+", "", raw[end - 1])
            output.append(f"{value}{trailing}")
            index = end
            continue
        output.append(raw[index])
        index += 1
    return " ".join(output)


def spoken_quantities_to_digits(transcript: str) -> str:
    """Write a spoken number as digits where a unit follows it.

    "record the temperature as twenty-one degrees" becomes "... as 21 degrees"
    and "set a timer for one minute" becomes "... for 1 minute", so the fixed
    grammar can read quantities however the transcriber chose to write them.
    Only a number directly followed by a unit is touched: "step one" or
    "the one on the left" are left alone, and parsing is as strict as in
    :func:`_spoken_number`, so "one two" never becomes twelve.
    """

    raw = _NUMBER_WORD_HYPHEN.sub(r"\1 \2", transcript).split()
    words = [NON_WORD.sub("", unicodedata.normalize("NFKC", item).casefold()) for item in raw]
    output: list[str] = []
    index = 0
    while index < len(raw):
        parsed = _spoken_number(words, index)
        previous = words[index - 1] if index else ""
        # The tail of a longer spoken value ("thirty seven point five", "one
        # two", "minus five") must not be rewritten on its own: half a number
        # in digits is a different, wrong number.
        continues_a_value = (
            previous in _NUMBER_JOINERS or _spoken_number(words, index - 1) is not None
        )
        if parsed is not None and not (index and continues_a_value):
            value, end = parsed
            if end < len(raw) and _fold(raw[end]) in UNIT_TOKENS:
                output.append(str(value))
                index = end
                continue
        output.append(raw[index])
        index += 1
    return " ".join(output)


_NUMBER_JOINERS = frozenset({"point", "dot", "minus", "negative", "plus"})


def salient_tokens(transcript: str) -> tuple[str, ...]:
    """Reduce a transcript to the tokens that could change what is recorded.

    Filler is dropped; numbers, units, negations, and substantive words survive.
    Two transcripts with the same salient tokens cannot produce different
    records, which is exactly the property the comparison needs.
    """

    tokens: list[str] = []
    # "10-minute" and "10 minute" say the same thing; speech recognisers
    # hyphenate compound durations inconsistently (found in the hands-free
    # evaluation: small.en wrote "10-minute" where base.en wrote "10 minute").
    transcript = _DIGIT_HYPHEN.sub(r"\1 ", transcript)
    transcript = _compose_spoken_numbers(transcript)
    for raw in transcript.split():
        folded = _fold(raw)
        if not folded:
            continue
        if folded in FILLER_WORDS and folded not in NEGATION_WORDS:
            continue
        tokens.append(folded)
    return tuple(tokens)


def _classify(tokens: tuple[str, ...]) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    numbers = tuple(token for token in tokens if NUMBER_PATTERN.match(token))
    units = tuple(token for token in tokens if token in UNIT_TOKENS)
    negations = tuple(token for token in tokens if token in NEGATION_WORDS)
    return numbers, units, negations


def compare_transcripts(realtime: str, high_accuracy: str) -> TranscriptComparison:
    """Compare two readings of one utterance without guessing between them.

    ``high_accuracy`` becomes the canonical wording whenever the two agree,
    because it comes from the path chosen for exactness. When they disagree the
    result carries no canonical value at all: the caller has to ask the operator.
    """

    if not realtime.strip() or not high_accuracy.strip():
        return TranscriptComparison(
            agreement=TranscriptAgreement.UNAVAILABLE,
            canonical=None,
            reasons=("only one transcript is available, so nothing was cross-checked",),
        )
    if realtime == high_accuracy:
        return TranscriptComparison(
            agreement=TranscriptAgreement.IDENTICAL,
            canonical=high_accuracy,
            reasons=(),
        )

    left = salient_tokens(realtime)
    right = salient_tokens(high_accuracy)
    if left == right:
        return TranscriptComparison(
            agreement=TranscriptAgreement.EQUIVALENT,
            canonical=high_accuracy,
            reasons=(),
        )

    left_numbers, left_units, left_negations = _classify(left)
    right_numbers, right_units, right_negations = _classify(right)
    reasons: list[str] = []
    if left_numbers != right_numbers:
        reasons.append(
            "the value differs: "
            f"heard {' '.join(left_numbers) or 'no number'} "
            f"and {' '.join(right_numbers) or 'no number'}"
        )
    if left_units != right_units:
        reasons.append(
            "the unit differs: "
            f"heard {' '.join(left_units) or 'no unit'} "
            f"and {' '.join(right_units) or 'no unit'}"
        )
    if left_negations != right_negations:
        reasons.append("a negation differs, which reverses the instruction")
    if not reasons:
        reasons.append("the wording differs in a way that could change the meaning")

    return TranscriptComparison(
        agreement=TranscriptAgreement.CONFLICT,
        canonical=None,
        reasons=tuple(reasons),
    )
