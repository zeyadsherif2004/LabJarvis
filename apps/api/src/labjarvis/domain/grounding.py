"""Deterministic retrieval over one approved protocol version.

Protocol questions are answered from the approved protocol or not at all. This
module does the retrieval part in ordinary code:

* it only ever searches the version it was given, so an answer cannot be drawn
  from a different protocol or from an unapproved draft;
* every passage it returns carries the page, section, and step it came from, so
  an answer can be checked against the document;
* when nothing scores above the floor it returns nothing, and the caller says so
  rather than filling the gap.

Ranking is lexical - term overlap with a rarity weight - not semantic. That is a
deliberate trade: a laboratory protocol is short, uses consistent vocabulary, and
benefits far more from being auditable and reproducible than from catching a
paraphrase. A reviewer can re-derive any score here by hand.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from labjarvis.domain.models import ProtocolStepDraft, ProtocolVersion

WORD_PATTERN = re.compile(r"[a-z0-9][a-z0-9.\-]*", flags=re.IGNORECASE)

#: Words that frame a question or connect a sentence without saying anything
#: about which passage answers it.
#:
#: Two kinds are listed. Articles, prepositions, and interrogatives are the
#: obvious ones. The second kind matters more and was the source of a real
#: retrieval defect: generic action verbs such as "use", "need", and "get".
#: They carry no protocol meaning, but in a corpus of a dozen short passages a
#: word appearing once gets a large rarity weight, so a question like "which
#: vessel do I use" could be answered by whichever line happened to contain the
#: word "use" - in the evaluation fixture, a sentence about ethanol storage -
#: while the passage that actually names a vessel ranked below it. Dropping
#: query-framing verbs makes the ranking depend on the domain terms, which is
#: what the operator meant by the question.
STOP_WORDS = frozenset(
    {
        # articles, conjunctions, prepositions, and copulas
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "than",
        "that",
        "the",
        "then",
        "this",
        "to",
        "with",
        # interrogatives and modals
        "can",
        "did",
        "do",
        "does",
        "how",
        "long",
        "many",
        "much",
        "should",
        "what",
        "when",
        "where",
        "which",
        "will",
        "would",
        # pronouns
        "i",
        "me",
        "my",
        "we",
        "you",
        "your",
        # generic action verbs that appear in questions rather than instructions
        "get",
        "have",
        "need",
        "needed",
        "use",
        "used",
        "using",
        # structural vocabulary shared by every protocol
        "step",
        "steps",
        "protocol",
    }
)

#: Minimum score before a passage counts as an answer. Below this the caller
#: abstains, which is always safer than a confident irrelevant excerpt.
RELEVANCE_FLOOR = 0.35


@dataclass(frozen=True, slots=True)
class GroundedPassage:
    """One retrievable passage plus everything needed to cite it."""

    chunk_id: str
    text: str
    page: int
    section: str | None
    step_id: str | None
    step_number: int | None
    score: float = 0.0

    @property
    def citation(self) -> str:
        parts = [f"page {self.page}"]
        if self.section:
            parts.append(self.section)
        if self.step_number is not None:
            parts.append(f"step {self.step_number}")
        return ", ".join(parts)


@dataclass(frozen=True, slots=True)
class GroundedRetrieval:
    """What retrieval found, and whether it is enough to answer from."""

    passages: tuple[GroundedPassage, ...]
    protocol_version_id: str

    @property
    def supported(self) -> bool:
        """Whether any passage cleared the relevance floor."""

        return bool(self.passages)


def tokenise(text: str) -> tuple[str, ...]:
    """Split text into comparable terms.

    Interior dots and hyphens are kept because they carry meaning in a protocol
    ("0.5", "pH-7"), but trailing ones are sentence punctuation and must be
    stripped: otherwise "flammable." in the document never matches "flammable"
    in the question, and the passage is invisible to retrieval.
    """

    tokens: list[str] = []
    for match in WORD_PATTERN.finditer(text):
        token = match.group(0).casefold().rstrip(".-")
        if token and token not in STOP_WORDS:
            tokens.append(token)
    return tuple(tokens)


def _step_passages(version: ProtocolVersion) -> list[GroundedPassage]:
    """One passage per approved step, carrying its own provenance."""

    passages: list[GroundedPassage] = []
    for step in version.steps:
        passages.append(
            GroundedPassage(
                chunk_id=f"{version.id}:{step.id}",
                text=_step_text(step),
                page=step.source.page,
                section=step.source.section,
                step_id=step.id,
                step_number=step.number,
            )
        )
    return passages


def _step_text(step: ProtocolStepDraft) -> str:
    parts = [step.instruction, *step.safety_notes]
    for requirement in step.requirements:
        quantity = (
            f"{requirement.quantity} {requirement.unit} "
            if requirement.quantity is not None and requirement.unit is not None
            else ""
        )
        parts.append(f"{quantity}{requirement.name}")
    if step.duration_seconds is not None:
        parts.append(f"{step.duration_seconds} seconds")
    return " ".join(part for part in parts if part)


def _source_passages(version: ProtocolVersion) -> list[GroundedPassage]:
    """Paragraph-sized passages of the original source text.

    Source passages exist because the structured steps are a *reduction* of the
    document. Material a reviewer left out of the steps - a preamble, a storage
    note, a hazard warning - is still part of the approved protocol and is still
    quotable, but only ever as an exact excerpt.
    """

    passages: list[GroundedPassage] = []
    for index, block in enumerate(version.source_text.split("\n"), start=1):
        cleaned = " ".join(block.split())
        if len(cleaned) < 12:
            continue
        passages.append(
            GroundedPassage(
                chunk_id=f"{version.id}:source:{index}",
                text=cleaned,
                # The source text is flattened, so provenance is limited to the
                # first page. Step passages carry exact pages, and they rank
                # first for anything the reviewer captured as a step.
                page=1,
                section=None,
                step_id=None,
                step_number=None,
            )
        )
    return passages


def retrieve(
    version: ProtocolVersion,
    question: str,
    *,
    limit: int = 3,
    floor: float = RELEVANCE_FLOOR,
) -> GroundedRetrieval:
    """Rank passages of one approved version against a question.

    The version is the only corpus. There is no index spanning protocols, so
    retrieving from an unrelated or unapproved protocol is not merely disallowed
    - it is not expressible.
    """

    query = set(tokenise(question))
    if not query:
        return GroundedRetrieval(passages=(), protocol_version_id=version.id)

    candidates = _step_passages(version) + _source_passages(version)
    if not candidates:
        return GroundedRetrieval(passages=(), protocol_version_id=version.id)

    # Rarity weight: a term appearing in every passage says nothing about which
    # passage to pick, so it should not drive the ranking.
    document_frequency: Counter[str] = Counter()
    tokenised: list[tuple[GroundedPassage, set[str]]] = []
    for passage in candidates:
        tokens = set(tokenise(passage.text))
        tokenised.append((passage, tokens))
        for token in tokens:
            document_frequency[token] += 1

    total = len(candidates)
    scored: list[GroundedPassage] = []
    for passage, tokens in tokenised:
        overlap = query & tokens
        if not overlap:
            continue
        weight = sum(
            math.log(1 + total / (1 + document_frequency[token])) for token in overlap
        )
        # Normalised by the query so scores stay comparable between questions.
        normaliser = sum(
            math.log(1 + total / (1 + document_frequency.get(token, 0))) for token in query
        )
        score = weight / normaliser if normaliser else 0.0
        if score >= floor:
            scored.append(
                GroundedPassage(
                    chunk_id=passage.chunk_id,
                    text=passage.text,
                    page=passage.page,
                    section=passage.section,
                    step_id=passage.step_id,
                    step_number=passage.step_number,
                    score=round(score, 4),
                )
            )

    # Structured approved steps win ties over raw source lines. They carry exact
    # page, section, and step provenance, and they are the reviewed form of the
    # instruction, so an answer drawn from one is both more precise and more
    # checkable than the same sentence quoted from the flattened source.
    scored.sort(key=lambda item: (-item.score, item.step_id is None, item.chunk_id))
    return GroundedRetrieval(
        passages=tuple(scored[:limit]),
        protocol_version_id=version.id,
    )


ABSTENTION = (
    "That is not specified in the approved protocol. "
    "Check the original document or ask your supervisor before proceeding."
)


def compose_answer(retrieval: GroundedRetrieval) -> str:
    """Turn retrieved passages into an answer, or abstain.

    The answer is assembled from exact excerpts and their citations. Nothing is
    paraphrased and nothing is added, so the sentence the operator hears can
    always be checked against the document it came from.
    """

    if not retrieval.supported:
        return ABSTENTION
    best = retrieval.passages[0]
    return f"The approved protocol says, at {best.citation}: {best.text}"


#: Spoken when nothing reaches the relevance floor. Never a guess.
SPOKEN_ABSTENTION = "I couldn't find that in the approved protocol."


def spoken_answer(retrieval: GroundedRetrieval, version: ProtocolVersion) -> str:
    """The eyes-free form of an answer: the step reference, then its exact words.

    "Step 3 says: Incubate at 37 degrees Celsius for 10 minutes." The spoken
    text is the approved step instruction verbatim (not the retrieval passage,
    which also lists requirements and durations in machine form), or the exact
    source excerpt when the best passage is not a structured step.
    """

    if not retrieval.supported:
        return SPOKEN_ABSTENTION
    best = retrieval.passages[0]
    if best.step_id is not None:
        step = next((item for item in version.steps if item.id == best.step_id), None)
        if step is not None:
            return f"Step {step.number} says: {step.instruction}"
    return f"The approved protocol says: {best.text}"


# --- Retrieval version 2 -----------------------------------------------------
#
# Version 1 failed on independently written protocols in three documented ways
# (September post-review; development split only): "At what temperature is the
# heat shock performed?" abstained although the step reads "Heat shock at 42 C",
# because nothing in the question matched the token "c"; "incubation" never met
# "incubate"; and "long" was a stop word, so "how long ..." lost its only cue.
# Version 2 keeps the same auditable, deterministic, exact-excerpt design and
# adds three things, developed and tuned on the development split only:
#
# * light suffix stemming, so morphological variants meet;
# * quantity concepts: a passage containing "42 C" also carries the concept
#   ``#temperature``, and a question containing "temperature", "how hot" or
#   "degrees" asks for it (likewise durations, volumes, speeds and masses);
# * BM25 term weighting, normalised per question to [0, 1).
#
# Answers are still exact excerpts with citations, and anything below the floor
# still abstains. v2 is NOT the production retriever (see ``retrieve_v2``).

RETRIEVAL_VERSION = "grounding-v2"
BM25_K1 = 1.2
BM25_B = 0.75
#: Chosen on the development split (research/grounding/evaluate.py); the
#: held-out test split is reported across all floors, never used to choose.
RELEVANCE_FLOOR_V2 = 0.15

_SUFFIXES = (
    "ational", "ations", "ation", "ating", "ated", "ates", "ate", "ings", "ing",
    "ied", "ies", "ed", "es", "ly", "s",
)  # fmt: skip
_NUMBER = r"(?:-?\d+(?:[.,]\d+)?(?:\s*[-\u2013]\s*\d+(?:[.,]\d+)?)?)"
_CONCEPT_PATTERNS: dict[str, re.Pattern[str]] = {
    "#temperature": re.compile(
        _NUMBER + r"\s*(?:°|˚|º)?\s*(?:c|f|k)\b|" + _NUMBER + r"\s*(?:degrees?|°|˚)|"
        r"\b(?:on ice|room temperature|freezer)\b",
        re.IGNORECASE,
    ),
    "#duration": re.compile(
        _NUMBER + r"\s*(?:s|sec|secs|seconds?|min|mins|minutes?|h|hr|hrs|hours?)\b|"
        r"\b(?:overnight|one hour|an hour|ten minutes)\b",
        re.IGNORECASE,
    ),
    "#volume": re.compile(
        _NUMBER + r"\s*(?:µl|μl|ul|ml|l|liters?|litres?|microliters?|milliliters?)\b|"
        r"\bvolumes?\b",
        re.IGNORECASE,
    ),
    "#speed": re.compile(_NUMBER + r"\s*rpm\b|\bfull speed\b", re.IGNORECASE),
    "#mass": re.compile(_NUMBER + r"\s*(?:g|mg|kg|µg|ug|grams?)\b", re.IGNORECASE),
}
_QUESTION_CONCEPTS: dict[str, tuple[str, ...]] = {
    "#temperature": ("temperature", "temperatures", "hot", "cold", "warm", "degrees", "celsius"),
    "#duration": ("long", "time", "duration", "minutes", "hours", "seconds"),
    "#volume": ("volume", "ml", "microlitres", "microliters", "millilitres", "milliliters"),
    "#speed": ("speed", "fast", "rpm"),
    "#mass": ("mass", "weight", "grams", "weigh"),
}


def stem(token: str) -> str:
    """Strip one common English suffix (and a final 'e'); numbers are untouched."""

    if not token or token[0].isdigit() or token[0] == "-":
        return token
    for suffix in _SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            token = token[: -len(suffix)]
            break
    if token.endswith("e") and len(token) > 4:
        token = token[:-1]
    return token


def passage_terms(text: str) -> list[str]:
    """Stemmed passage terms plus the quantity concepts the passage states."""

    terms = [stem(token) for token in tokenise(text)]
    terms.extend(concept for concept, pattern in _CONCEPT_PATTERNS.items() if pattern.search(text))
    return terms


def question_terms(question: str) -> list[str]:
    """Stemmed question terms plus the quantity concepts the question asks for."""

    words = {match.group(0).casefold() for match in WORD_PATTERN.finditer(question)}
    terms = [stem(token) for token in tokenise(question)]
    terms.extend(
        concept for concept, cues in _QUESTION_CONCEPTS.items() if words & set(cues)
    )
    return terms


def retrieve_v2(
    version: ProtocolVersion,
    question: str,
    *,
    limit: int = 3,
    floor: float = RELEVANCE_FLOOR_V2,
) -> GroundedRetrieval:
    """Research comparator only (grounding-v2); production uses ``retrieve``.

    Evaluated 2026-09-26: on four unseen protocols v2 raised answer recall from
    0.60 to 0.80 but answered 6/12 unsupported questions (v1: 4/12), and it
    failed the existing grounding safety gate. It is therefore not shipped.

    The version is still the only corpus, so retrieving from an unrelated or
    unapproved protocol is not expressible.
    """

    query = question_terms(question)
    if not query:
        return GroundedRetrieval(passages=(), protocol_version_id=version.id)
    candidates = _step_passages(version) + _source_passages(version)
    if not candidates:
        return GroundedRetrieval(passages=(), protocol_version_id=version.id)

    documents = [(passage, Counter(passage_terms(passage.text))) for passage in candidates]
    total = len(documents)
    average_length = sum(sum(counts.values()) for _, counts in documents) / total
    document_frequency: Counter[str] = Counter()
    for _, counts in documents:
        document_frequency.update(set(counts))

    def idf(term: str) -> float:
        frequency = document_frequency.get(term, 0)
        return math.log(1 + (total - frequency + 0.5) / (frequency + 0.5))

    unique_query = sorted(set(query))
    normaliser = sum(idf(term) * (BM25_K1 + 1) for term in unique_query)
    scored: list[GroundedPassage] = []
    for passage, counts in documents:
        length = sum(counts.values()) or 1
        weight = 0.0
        for term in unique_query:
            frequency = counts.get(term, 0)
            if not frequency:
                continue
            saturation = frequency * (BM25_K1 + 1) / (
                frequency + BM25_K1 * (1 - BM25_B + BM25_B * length / average_length)
            )
            weight += idf(term) * saturation
        score = weight / normaliser if normaliser else 0.0
        if score >= floor:
            scored.append(
                GroundedPassage(
                    chunk_id=passage.chunk_id,
                    text=passage.text,
                    page=passage.page,
                    section=passage.section,
                    step_id=passage.step_id,
                    step_number=passage.step_number,
                    score=round(score, 4),
                )
            )
    scored.sort(key=lambda item: (-item.score, item.step_id is None, item.chunk_id))
    return GroundedRetrieval(passages=tuple(scored[:limit]), protocol_version_id=version.id)


#: Name kept for the comparison harness.
retrieve_v1 = retrieve
