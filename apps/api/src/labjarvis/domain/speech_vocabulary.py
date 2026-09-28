"""Protocol-conditioned speech vocabulary.

The approved protocol version already lists the reagents an operator will name,
their aliases, and the units the steps use. Passing those words to the speech
model as inference-time context adapts recognition to the reviewed protocol
without any training, and because the vocabulary is derived deterministically
from an immutable version it is reproducible and hashed into provenance.

The vocabulary only *biases* recognition. It never changes what is accepted:
the transcript still goes through the interpreter, validation and confirmation.
"""

from __future__ import annotations

import hashlib
import re

from labjarvis.domain.models import ProtocolVersion

#: Units and scientific words commonly misrecognised by general ASR models.
BASE_VOCABULARY: tuple[str, ...] = (
    "microlitres",
    "millilitres",
    "degrees Celsius",
    "pH",
    "rpm",
    "milligrams",
)
MAX_TERMS = 40
_UNIT_WORDS = {
    "uL": "microlitres",
    "µL": "microlitres",
    "mL": "millilitres",
    "L": "litres",
    "mg": "milligrams",
    "g": "grams",
    "kg": "kilograms",
    "degC": "degrees Celsius",
    "°C": "degrees Celsius",
    "rpm": "rpm",
    "pH": "pH",
    "%": "percent",
}
_CLEAN = re.compile(r"\s+")


def protocol_vocabulary(version: ProtocolVersion | None) -> tuple[str, ...]:
    """Reagent names, aliases and units from an approved version, deduplicated."""

    terms: list[str] = []
    seen: set[str] = set()

    def add(term: str) -> None:
        cleaned = _CLEAN.sub(" ", term).strip()
        key = cleaned.casefold()
        if cleaned and len(cleaned) <= 60 and key not in seen:
            seen.add(key)
            terms.append(cleaned)

    if version is not None:
        for step in version.steps:
            for requirement in step.requirements:
                add(requirement.name)
                for alias in requirement.aliases:
                    add(alias)
                if requirement.unit:
                    add(_UNIT_WORDS.get(requirement.unit, requirement.unit))
    for term in BASE_VOCABULARY:
        add(term)
    return tuple(terms[:MAX_TERMS])


#: Words LabJarvis commands and replies are built from. Unlike the protocol
#: vocabulary this names no reagent and no value, so priming the *verification*
#: transcriber with it cannot pull its reading towards what the protocol
#: expects - the cross-check stays independent where it matters.
COMMAND_VOCABULARY: tuple[str, ...] = (
    "confirm",
    "reject",
    "complete step",
    "record",
    "correct",
    "skip",
    "timer",
    "pause",
    "resume",
    "session",
    "reagent",
    "observation",
    "temperature",
    "volume",
    "minutes",
    "seconds",
    "degrees Celsius",
    "millilitres",
    "microlitres",
    "grams",
)


def command_vocabulary(wake_phrase: str) -> tuple[str, ...]:
    """The wake phrase plus the fixed command words, for the verification reading.

    Added after the 2026-09-28 real-world session, where the verifier spelled
    what it heard in an accent ("komplet step", "rekord", "Žarvis", the wake
    phrase in Arabic script) while the primary transcriber was right, so
    correct commands were refused as conflicts.
    """

    return (wake_phrase.strip(), *COMMAND_VOCABULARY) if wake_phrase.strip() else COMMAND_VOCABULARY


def vocabulary_sha256(vocabulary: tuple[str, ...]) -> str:
    return hashlib.sha256("\n".join(vocabulary).encode("utf-8")).hexdigest()
