"""Deterministic structuring of extracted protocol text into reviewable draft steps.

The rule this module follows is *extract and structure - never silently invent,
alter or discard*:

* every source line ends up in exactly one place: a step's instruction, a step's
  safety notes, a section heading, or the list of unassigned source text that
  the reviewer is shown;
* step text is kept as written - list markers of sub-steps stay, wrapped lines
  are rejoined, nothing is paraphrased;
* where the text does not settle a value (a range, an approximate amount, more
  than one duration, a repetition, a condition) no value is chosen - the step
  carries an extraction flag instead, and approval waits for a reviewer.

It is intentionally rule-based. The output is a proposal, and the review screen
plus :func:`labjarvis.domain.protocol_validation.validate_protocol_for_approval`
decide what becomes an approved protocol.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from labjarvis.domain.models import (
    ExtractionFlag,
    ProtocolRequirement,
    ProtocolStepDraft,
    SourceReference,
    UnassignedSourceText,
)
from labjarvis.domain.protocol_validation import (
    MAX_DURATION_SECONDS,
    MAX_FLAGS_PER_STEP,
    MAX_REQUIREMENTS_PER_STEP,
    MAX_SAFETY_NOTES_PER_STEP,
    MAX_STEPS,
)
from labjarvis.infrastructure.documents import (
    DocumentError,
    ExtractedDocument,
    SourceLine,
)

MAX_INSTRUCTION_CHARACTERS = 5000
MAX_EXCERPT_CHARACTERS = 1000
MAX_SAFETY_NOTE_CHARACTERS = 1000
MAX_SECTION_CHARACTERS = 250
MAX_TITLE_CHARACTERS = 80

# Unassigned-text reasons, shown to the reviewer beside the preserved text.
NON_PROCEDURE_SECTION = "NON_PROCEDURE_SECTION"
OUTSIDE_STEPS = "OUTSIDE_STEPS"
PAGE_HEADER_FOOTER = "PAGE_HEADER_FOOTER"
WARNING_WITHOUT_STEP = "WARNING_WITHOUT_STEP"
STEP_TEXT_OVERFLOW = "STEP_TEXT_OVERFLOW"


@dataclass(frozen=True, slots=True)
class ParsedProtocol:
    steps: tuple[ProtocolStepDraft, ...]
    unassigned: tuple[UnassignedSourceText, ...]


# --------------------------------------------------------------------------- lines

_HEADING_MARKDOWN = re.compile(r"^#{1,6}\s+(?P<body>.+?)\s*#*$")
_STEP_WORD = re.compile(
    r"^step\s*(?P<number>\d{1,3})\s*(?:[.:)\-\u2013\u2014]\s*|\s+)(?P<body>.*)$",
    re.I,
)
_NUMBERED = re.compile(r"^\(?(?P<number>\d{1,3})(?:\.\)|[.)]|[:\-\u2013\u2014])\s+(?P<body>\S.*)$")
_HIERARCHICAL = re.compile(r"^(?P<number>\d{1,3}(?:\.\d{1,3})+)\.?\)?\s+(?P<body>\S.*)$")
_LETTERED = re.compile(r"^\(?(?P<marker>[a-z]|i{1,3}|iv|vi{0,3}|ix|x)[.)]\s+(?P<body>\S.*)$")
_UPPER_LETTERED = re.compile(r"^\(?(?P<marker>[A-Z]|I{1,3}|IV|VI{0,3}|IX|X)[.)]\s+(?P<body>\S.*)$")
_BULLET = re.compile(
    "^(?P<marker>[\u2022\u25e6\u25aa\u25ab\u2023\u2219\u00b7\u25cf\u25cb\u25a0\u25a1"
    "\u25ba\u25b6\u27a2\u2713\u2714\\-*+\u2013\u2014\uf0b7\uf0a7\uf0d8\uf076\uf0fc])"
    r"\s+(?P<body>\S.*)$"
)
_MARKER_ONLY = re.compile(
    "^(?:step\\s*\\d{1,3}\\s*[.:)]?|\\(?\\d{1,3}[.)]|\\(?[a-z][.)]|[\u2022\u25e6\u25aa\u25cf\u25cb\u25a0\uf0b7\uf0a7])$",
    re.I,
)
_WARNING = re.compile(
    r"^[\s*_>]*(?:\u26a0\ufe0f?\s*)?(?P<label>warning|caution|danger|hazard|critical|"
    r"important|attention|safety note)\b[\s*_]*(?:[:!\-\u2013\u2014]|$)[\s*_]*(?P<body>.*)$",
    re.I,
)
_WARNING_CAPS = re.compile(
    r"^[\s*_>]*(?P<label>WARNING|CAUTION|DANGER|HAZARD|CRITICAL|IMPORTANT)\s+(?P<body>\S.*)$"
)
_WARNING_SYMBOL = re.compile("^[\\s*_>]*\u26a0\ufe0f?\\s*(?P<body>.*)$")
_NOTE = re.compile(
    r"^[\s*_>]*(?P<label>note|notes|tip|hint|pause point|remark|comment|optional)"
    r"\s*\d*[\s*_]*(?:[:\-\u2013\u2014]|$)[\s*_]*(?P<body>.*)$",
    re.I,
)
_PART_HEADING = re.compile(
    r"^(?:part|stage|phase|day|section|session|round|module)\s+"
    r"(?:\d{1,2}|[a-z]|[ivx]{1,4}|one|two|three|four|five|six)\b[^.;]{0,60}$",
    re.I,
)
_PAGE_NUMBER = re.compile(r"^(?:page\s*)?\d{1,4}(?:\s*(?:of|/)\s*\d{1,4})?$", re.I)
_MARKDOWN_TABLE_RULE = re.compile(r"^\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?$")
# Only a sentence end makes the next line a new line; a line ending in ";" or
# ":" is still mid-sentence and a following plain line continues it.
_TERMINAL = re.compile(r"[.!?]$")

_SECTION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "procedure": (
        "procedure",
        "procedures",
        "method",
        "methods",
        "methodology",
        "protocol",
        "protocol steps",
        "steps",
        "instructions",
        "experimental procedure",
        "experimental procedures",
        "workflow",
        "directions",
        "preparation",
        "materials and methods",
        "procedure steps",
        "experimental protocol",
    ),
    "materials": (
        "materials",
        "material",
        "reagents",
        "reagent",
        "equipment",
        "supplies",
        "consumables",
        "apparatus",
        "solutions",
        "buffers",
        "recipes",
        "stock solutions",
        "reagent setup",
        "equipment setup",
        "materials and reagents",
        "reagents and materials",
        "reagents and equipment",
        "materials and equipment",
        "materials required",
        "reagents required",
        "equipment required",
        "you will need",
        "what you need",
        "ingredients",
        "kit contents",
        "media",
        "chemicals",
    ),
    "references": (
        "references",
        "reference",
        "bibliography",
        "citations",
        "literature",
        "sources",
        "further reading",
        "acknowledgements",
        "acknowledgments",
    ),
    "notes": (
        "notes",
        "troubleshooting",
        "tips",
        "comments",
        "remarks",
        "notes and troubleshooting",
        "timing",
    ),
    "safety": (
        "safety",
        "safety precautions",
        "precautions",
        "hazards",
        "safety information",
        "health and safety",
        "risk assessment",
        "safety considerations",
    ),
    "background": (
        "introduction",
        "background",
        "abstract",
        "overview",
        "summary",
        "purpose",
        "aim",
        "aims",
        "objective",
        "objectives",
        "principle",
        "expected results",
        "anticipated results",
        "results",
        "discussion",
        "data analysis",
        "analysis",
        "applications",
        "limitations",
        "description",
        "scope",
    ),
}
_PROCEDURE_SUFFIXES = (" protocol", " procedure", " method")


@dataclass(slots=True)
class _Line:
    page: int
    raw: str
    kind: str  # heading step hier substep bullet warning note text furniture
    body: str
    number: int | None = None
    depth: int = 1
    level: int = 0
    heading_kind: str | None = None
    table_row: bool = False
    table_start: bool = False
    indent: int | None = None
    ocr: bool = False
    blank_before: bool = False
    paragraph: bool = False  # a separate paragraph in a format without wrapping


def _clean_heading(text: str) -> str:
    cleaned = text.strip().strip("*_").strip()
    cleaned = re.sub(r"^#{1,6}\s+", "", cleaned)
    cleaned = cleaned.strip("*_ ").rstrip(":").strip("*_ ")
    return cleaned


def _section_kind(text: str) -> str | None:
    normalized = _clean_heading(text).casefold().replace("&", "and")
    # "2. Materials", "II. Procedure", "A) Reagents", "Section 3: Methods"
    normalized = re.sub(
        r"^(?:section\s+)?(?:\d{1,3}(?:\.\d{1,3})*|[ivx]{1,4}|[a-z])[.):]?\s+",
        "",
        normalized,
    )
    normalized = " ".join(normalized.split()).rstrip(":")
    if not normalized or len(normalized.split()) > 8:
        return None
    for kind, keywords in _SECTION_KEYWORDS.items():
        for keyword in keywords:
            if (
                normalized == keyword
                or normalized.startswith(f"{keyword} ")
                or (normalized.startswith(f"{keyword}:"))
            ):
                return kind
    if normalized.endswith(_PROCEDURE_SUFFIXES):
        return "procedure"
    return None


def _looks_like_heading(text: str) -> bool:
    """A short standalone line that presents itself as a heading."""

    stripped = text.strip()
    bare = stripped.strip("*_").strip()
    words = bare.split()
    if not bare or len(words) > 8 or len(bare) > 70 or re.search(r"[.;,]$", bare):
        return False
    if not (bare[0].isupper() or bare[0].isdigit()):
        return False
    letters = [character for character in bare if character.isalpha()]
    if stripped.startswith(("**", "__")) and stripped.endswith(("**", "__")):
        return True
    if bare.endswith(":"):
        return True
    if len(letters) >= 3 and all(character.isupper() for character in letters):
        return True
    significant = [word for word in words if len(word) > 3 and word[0].isalpha()]
    return bool(significant) and all(word[0].isupper() for word in significant)


def _indent_level(text: str) -> int:
    expanded = text.replace("\t", "    ")
    return (len(expanded) - len(expanded.lstrip(" "))) // 2


def _classify_text(text: str, *, page: int, ocr: bool) -> _Line:
    line = _classify_marked_text(text, page=page, ocr=ocr)
    # Markdown bold is formatting, not content. ("__" is left alone: in a
    # protocol it can be a blank to fill in.)
    line.body = line.body.replace("**", "").strip() or line.body
    return line


def _classify_marked_text(text: str, *, page: int, ocr: bool) -> _Line:
    level = _indent_level(text)
    raw = " ".join(text.split())
    line = _Line(page=page, raw=raw, kind="text", body=raw, level=level, ocr=ocr)

    heading = _HEADING_MARKDOWN.match(raw)
    if heading is not None:
        body = _clean_heading(heading.group("body"))
        line.kind, line.body, line.heading_kind = "heading", body, _section_kind(body)
        return line
    if raw.startswith("|") and raw.count("|") >= 2:
        if _MARKDOWN_TABLE_RULE.match(raw):
            line.kind = "rule"  # "|---|---|" draws the table and carries no text
            return line
        cells = tuple(cell.strip() for cell in raw.strip("|").split("|") if cell.strip())
        return _classify_cells(cells, page=page, ocr=ocr, raw=raw)

    for pattern, kind in ((_STEP_WORD, "step"), (_NUMBERED, "step")):
        match = pattern.match(raw)
        if match is not None:
            body = match.group("body").strip()
            section = _section_kind(body)
            if section is not None and len(body.split()) <= 6:
                line.kind, line.body, line.heading_kind = "heading", _clean_heading(body), section
                return line
            line.kind, line.body, line.number = kind, body, int(match.group("number"))
            return line
    match = _HIERARCHICAL.match(raw)
    if match is not None:
        body = match.group("body").strip()
        if _section_kind(body) is not None and len(body.split()) <= 6:
            line.kind, line.body = "heading", _clean_heading(body)
            line.heading_kind = _section_kind(body)
            return line
        parts = match.group("number").split(".")
        line.kind, line.body, line.depth = "hier", body, len(parts)
        line.number = int(parts[-1])
        return line
    match = _LETTERED.match(raw)
    if match is not None:
        line.kind, line.body = "substep", match.group("body").strip()
        return line
    match = _UPPER_LETTERED.match(raw)
    if match is not None:
        body = match.group("body").strip()
        if _looks_like_heading(body) or _section_kind(body) is not None:
            line.kind, line.body, line.heading_kind = (
                "heading",
                _clean_heading(body),
                (_section_kind(body)),
            )
        else:
            line.kind, line.body = "substep", body
        return line
    match = _BULLET.match(raw)
    if match is not None:
        line.kind, line.body = "bullet", match.group("body").strip()
        return line

    warning = _WARNING.match(raw) or _WARNING_CAPS.match(raw) or _WARNING_SYMBOL.match(raw)
    if warning is not None:
        line.kind, line.body = "warning", raw.strip("*_ ")
        return line
    note = _NOTE.match(raw)
    if note is not None:
        if note.group("label").casefold() == "notes" and not note.group("body").strip():
            line.kind, line.body, line.heading_kind = "heading", _clean_heading(raw), "notes"
        else:
            line.kind, line.body = "note", raw
        return line

    section = _section_kind(raw)
    if section is not None and len(raw.split()) <= 6 and not re.search(r"[.;,]$", raw):
        line.kind, line.body, line.heading_kind = "heading", _clean_heading(raw), section
        return line
    if _PART_HEADING.match(raw.strip("*_ ")):
        line.kind, line.body = "heading", _clean_heading(raw)
        return line
    return line


def _classify_cells(cells: tuple[str, ...], *, page: int, ocr: bool, raw: str) -> _Line:
    line = _Line(page=page, raw=raw, kind="text", body=raw, table_row=True, ocr=ocr)
    if len(cells) >= 2:
        first = re.fullmatch(r"(?:step\s*)?\(?(\d{1,3})[.):]?", cells[0], re.I)
        if first is not None:
            line.kind, line.number = "step", int(first.group(1))
            line.body = " | ".join(cells[1:])
    return line


def _classify_source_line(source: SourceLine, *, page: int, ocr: bool) -> _Line:
    """Use the structure a DOCX states before falling back to reading the text."""

    if source.table_cells:
        line = _classify_cells(source.table_cells, page=page, ocr=ocr, raw=source.text)
        line.table_start = source.table_start
        return line
    raw = " ".join(source.text.split())
    if source.heading:
        body = _clean_heading(raw)
        return _Line(
            page=page,
            raw=raw,
            kind="heading",
            body=body,
            heading_kind=_section_kind(body),
            ocr=ocr,
        )
    if source.list_kind is not None and source.body is not None:
        level = source.list_level or 0
        body = " ".join(source.body.split())
        if source.list_kind == "numbered" and level == 0:
            number = None
            if source.marker is not None:
                digits = re.findall(r"\d+", source.marker)
                number = int(digits[-1]) if digits else None
            section = _section_kind(body)
            if section is not None and len(body.split()) <= 6:
                return _Line(
                    page=page,
                    raw=raw,
                    kind="heading",
                    body=_clean_heading(body),
                    heading_kind=section,
                    ocr=ocr,
                )
            return _Line(
                page=page,
                raw=raw,
                kind="step",
                body=body,
                number=number,
                indent=source.indent,
                ocr=ocr,
            )
        if source.list_kind == "bullet":
            return _Line(
                page=page,
                raw=raw,
                kind="bullet",
                body=body,
                level=level,
                indent=source.indent,
                ocr=ocr,
            )
        return _Line(page=page, raw=raw, kind="substep", body=body, level=level, ocr=ocr)
    line = _classify_text(source.text, page=page, ocr=ocr)
    line.paragraph = True
    return line


def _page_furniture(pages: list[list[str]]) -> set[tuple[int, int]]:
    """Positions of running headers, footers, and page numbers.

    Only the first and last two lines of a page are candidates, and a text line
    counts as a running header or footer only when it recurs on several pages.
    """

    edges: dict[int, list[int]] = {}
    for page_index, lines in enumerate(pages):
        non_empty = [index for index, line in enumerate(lines) if line.strip()]
        edges[page_index] = sorted(set(non_empty[:2] + non_empty[-2:]))
    furniture: set[tuple[int, int]] = set()
    counts: dict[str, int] = {}
    for page_index, positions in edges.items():
        for position in positions:
            text = " ".join(pages[page_index][position].split())
            if _PAGE_NUMBER.fullmatch(text):
                furniture.add((page_index, position))
            counts.setdefault(re.sub(r"\d+", "#", text.casefold()), 0)
    if len(pages) >= 2:
        for page_index, positions in edges.items():
            seen: set[str] = set()
            for position in positions:
                key = re.sub(r"\d+", "#", " ".join(pages[page_index][position].split()).casefold())
                if key not in seen:
                    counts[key] += 1
                    seen.add(key)
        for page_index, positions in edges.items():
            for position in positions:
                key = re.sub(r"\d+", "#", " ".join(pages[page_index][position].split()).casefold())
                if counts[key] >= 2 and counts[key] * 2 >= len(pages):
                    furniture.add((page_index, position))
    return furniture


def _lines(document: ExtractedDocument) -> list[_Line]:
    page_sources = [page.source_lines() for page in document.pages]
    furniture = (
        _page_furniture([[line.text for line in lines] for lines in page_sources])
        if not any(page.lines for page in document.pages)
        else set()
    )
    result: list[_Line] = []
    for page_index, (page, sources) in enumerate(zip(document.pages, page_sources, strict=True)):
        explicit = bool(page.lines)
        blank = False
        pending_marker: str | None = None
        for position, source in enumerate(sources):
            if not source.text.strip():
                blank = True
                continue
            if (page_index, position) in furniture:
                result.append(
                    _Line(
                        page=page.number,
                        raw=" ".join(source.text.split()),
                        kind="furniture",
                        body="",
                        ocr=page.ocr_derived,
                    )
                )
                continue
            if not explicit and _MARKER_ONLY.match(source.text.strip()):
                # PDF text often puts a list marker on its own line.
                pending_marker = " ".join(source.text.split())
                continue
            if pending_marker is not None:
                indent = source.text[: len(source.text) - len(source.text.lstrip())]
                source = SourceLine(text=f"{indent}{pending_marker} {source.text.strip()}")
                pending_marker = None
            line = (
                _classify_source_line(source, page=page.number, ocr=page.ocr_derived)
                if explicit
                else _classify_text(source.text, page=page.number, ocr=page.ocr_derived)
            )
            if line.kind == "rule":
                continue
            line.blank_before = blank
            blank = False
            if line.table_row and not explicit:
                line.table_start = not (result and result[-1].table_row)
            result.append(line)
        if pending_marker is not None:
            result.append(_classify_text(pending_marker, page=page.number, ocr=page.ocr_derived))
    return result


# ------------------------------------------------------------------ value analysis

_APPROXIMATE_PREFIX = (
    r"(?P<prefix>(?:~|\u2248|<|>|\u2264|\u2265|"
    r"\b(?:approx(?:imately|\.)?|about|around|roughly|circa|ca\.|up\s+to|at\s+least|"
    r"at\s+most|no\s+more\s+than|no\s+less\s+than|more\s+than|less\s+than|over|under|"
    r"max(?:imum)?|minimum|within)\s*)?)"
)
_RANGE_PART = r"(?:(?P<low>\d+(?:[.,]\d+)*)\s*(?:-|\u2013|\u2014|to)\s*)?"
_AMOUNT_UNIT = (
    r"(?:[n\u00b5\u03bcu]l|ml|l|pg|ng|[\u00b5\u03bcu]g|mg|g|kg|"
    r"micro-?lit(?:er|re)s?|milli-?lit(?:er|re)s?|lit(?:er|re)s?|"
    r"nanograms?|micrograms?|milligrams?|grams?|kilograms?)"
)
_NAME_TERMINATORS = (
    r"then|to|into|onto|and|with|in|at|for|from|per|until|on|containing|using|or|by|"
    r"before|after|while|if|under|over|through|so|each"
)
# A reagent-name token; a dot is allowed only inside a number, as in "0.5 M".
_NAME_TOKEN = r"(?:[\w%\-+'/()\u00b5\u03bc]|\.(?=\d))+"
_AMOUNT = re.compile(
    _APPROXIMATE_PREFIX
    + r"(?<![\w.,])"
    + _RANGE_PART
    + r"(?P<quantity>\d+(?:[.,]\d+)*)\s*(?P<unit>"
    + _AMOUNT_UNIT
    + r")(?![\w/])"
    + r"(?P<plusminus>\s*\u00b1\s*\d+(?:[.,]\d+)?)?"
    + r"(?:\s+(?:of\s+)?(?P<name>(?!(?:"
    + _NAME_TERMINATORS
    + r")\b)"
    + _NAME_TOKEN
    + r"(?:\s+(?!(?:"
    + _NAME_TERMINATORS
    + r")\b)"
    + _NAME_TOKEN
    + r"){0,7}?)"
    + r"(?=\s*(?:[.,;:\[\]]|$)|\s+(?:"
    + _NAME_TERMINATORS
    + r")\b))?",
    re.I,
)
_CONTAINERS = {
    "tube",
    "tubes",
    "microcentrifuge",
    "eppendorf",
    "falcon",
    "conical",
    "centrifuge",
    "plate",
    "plates",
    "flask",
    "flasks",
    "beaker",
    "beakers",
    "bottle",
    "bottles",
    "vial",
    "vials",
    "cuvette",
    "cuvettes",
    "well",
    "wells",
    "column",
    "columns",
    "syringe",
    "syringes",
    "tip",
    "tips",
    "pipette",
    "pipettes",
    "reaction",
    "volumetric",
    "glass",
    "plastic",
    "capacity",
    "aliquot",
    "aliquots",
    "dish",
    "dishes",
    "jar",
    "jars",
    "cylinder",
    "tray",
    "trays",
    "vessel",
    "vessels",
    "container",
    "containers",
}
_UNUSUAL_UNIT = re.compile(
    r"(?<![\w.,])\d+(?:[.,]\d+)?\s*(?:drops?|pinch(?:es)?|spatulas?|scoops?|volumes?|vols?|"
    r"parts?|tablets?|squirts?|dash(?:es)?|cups?|tsp|tbsp|teaspoons?|tablespoons?)\b",
    re.I,
)
_UNSPECIFIED_AMOUNT = re.compile(
    r"\b(?:a\s+few\s+drops|a\s+drop|drops?\s+of|a\s+pinch|a\s+small\s+amount|a\s+little|"
    r"appropriate\s+(?:amount|volume)|sufficient|excess|as\s+(?:needed|required|necessary)|"
    r"q\.\s?s\.|to\s+volume|(?:an?\s+)?equal\s+volume|same\s+volume|"
    r"to\s+(?:a\s+)?final\s+volume|make\s+up\s+to)\b",
    re.I,
)
_TIME_UNIT = r"(?:seconds?|secs?|s|minutes?|mins?|min|hours?|hrs?|hr|h|days?)"
_DURATION = re.compile(
    _APPROXIMATE_PREFIX
    + r"(?<![\w.,])"
    + r"(?:(?P<low>\d+(?:\.\d+)?)\s*(?:-|\u2013|\u2014|to)\s*)?"
    + r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>"
    + _TIME_UNIT
    + r")\b(?![/\-\u00b0])",
    re.I,
)
_UNCLEAR_TIMING = re.compile(
    r"\b(?:overnight|o/n|several\s+(?:seconds|minutes|hours|days)|"
    r"a\s+few\s+(?:seconds|minutes|hours|days)|briefly)\b",
    re.I,
)
# "until" matters for timing only when the step also states a duration ("spin
# 5 min or until clear"). "Do not proceed until sealed" is a gate the operator
# confirms by completing the step, and it sets no value to get wrong.
_UNTIL = re.compile(r"\b(?:until|as\s+long\s+as)\b", re.I)
_REPETITION = re.compile(
    r"\b(?:repeat(?:ed|ing)?|twice|thrice|again|every)\b|"
    r"\b(?:\d+|two|three|four|five|six|seven|eight|nine|ten)\s+"
    r"(?:times|rounds|cycles|washes|repetitions)\b|"
    r"\b\d+\s*[x\u00d7]\s*(?=\d)",
    re.I,
)
_CONDITIONAL = re.compile(
    r"\b(?:if|unless|in\s+case|optional(?:ly)?|as\s+appropriate|depending\s+on|"
    r"alternatively|either)\b",
    re.I,
)
_INLINE_WARNING = re.compile(r"\b(?:warning|caution|danger|hazard|do not)\b", re.I)
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\"'])")

_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def _unit_seconds(unit: str) -> int:
    folded = unit.casefold()
    if folded.startswith(("hour", "hr")) or folded == "h":
        return _UNIT_SECONDS["h"]
    if folded.startswith("min"):
        return _UNIT_SECONDS["m"]
    if folded.startswith("day"):
        return _UNIT_SECONDS["d"]
    return _UNIT_SECONDS["s"]


def _flag(code: str, message: str, excerpt: str | None = None) -> ExtractionFlag:
    bounded = excerpt.strip()[:MAX_EXCERPT_CHARACTERS] if excerpt else None
    return ExtractionFlag(code=code, message=message[:500], excerpt=bounded or None)


def _quoted(text: str) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= 80 else f"{compact[:77]}..."


def _requirements(
    instruction: str,
    *,
    step_id: str,
) -> tuple[tuple[ProtocolRequirement, ...], list[ExtractionFlag]]:
    requirements: list[ProtocolRequirement] = []
    flags: list[ExtractionFlag] = []
    # Line by line, so a reagent name can never run into the next sub-step.
    matches = [
        (line, match) for line in instruction.splitlines() for match in _AMOUNT.finditer(line)
    ]
    for line, match in matches:
        prefix = (match.group("prefix") or "").strip()
        preceding = line[: match.start()].rstrip().casefold()
        if (not prefix and preceding.endswith((" at", "(at"))) or preceding == "at":
            # "centrifuge at 12000 g" is a setting, not an amount to take.
            continue
        amount_text = match.group(0).strip()
        raw_name = match.group("name")
        name = " ".join(raw_name.split()).strip(" .") if raw_name else ""
        if name and name.split()[0].casefold() in _CONTAINERS:
            continue  # "a 1.5 mL tube" names a container size
        quantity_text = match.group("quantity")
        amount_label = amount_text if not name else amount_text[: amount_text.rfind(name)].strip()
        exact = True
        if match.group("low") is not None:
            flags.append(
                _flag(
                    "QUANTITY_RANGE",
                    f"The source gives a range ('{_quoted(amount_label)}'), so no quantity "
                    "was set. Enter the amount to use, or leave it unset.",
                    amount_text,
                )
            )
            exact = False
        elif prefix or match.group("plusminus"):
            flags.append(
                _flag(
                    "QUANTITY_APPROXIMATE",
                    f"The source gives an approximate or bounded amount "
                    f"('{_quoted(amount_label)}'), so no exact quantity was set.",
                    amount_text,
                )
            )
            exact = False
        elif "," in quantity_text:
            flags.append(
                _flag(
                    "QUANTITY_AMBIGUOUS_NUMBER",
                    f"'{_quoted(amount_label)}' uses a comma, which can be a decimal point "
                    "or a thousands separator, so no quantity was set.",
                    amount_text,
                )
            )
            exact = False
        if not name or not re.search(r"[A-Za-z]", name):
            flags.append(
                _flag(
                    "QUANTITY_WITHOUT_REAGENT",
                    f"'{_quoted(amount_label)}' has no reagent name next to it, so no "
                    "reagent requirement was created. Add one if something must be checked.",
                    amount_text,
                )
            )
            continue
        quantity: Decimal | None = None
        unit: str | None = None
        if exact:
            try:
                quantity = Decimal(quantity_text)
                unit = match.group("unit")
            except InvalidOperation:
                quantity = None
        if len(requirements) >= MAX_REQUIREMENTS_PER_STEP:
            flags.append(
                _flag(
                    "REQUIREMENT_LIMIT_REACHED",
                    "The step names more reagents than LabJarvis tracks per step; "
                    "later ones were not turned into requirements.",
                )
            )
            break
        requirements.append(
            ProtocolRequirement(
                requirement_id=f"{step_id}-req-{len(requirements) + 1}",
                name=name.casefold(),
                quantity=quantity,
                unit=unit,
            )
        )
    for match in _UNUSUAL_UNIT.finditer(instruction):
        flags.append(
            _flag(
                "QUANTITY_UNUSUAL_UNIT",
                f"'{_quoted(match.group(0))}' uses a unit LabJarvis does not measure, so it "
                "was not turned into a reagent requirement.",
                match.group(0),
            )
        )
    for match in _UNSPECIFIED_AMOUNT.finditer(instruction):
        flags.append(
            _flag(
                "QUANTITY_UNSPECIFIED",
                f"The amount is not given as a number ('{_quoted(match.group(0))}').",
                match.group(0),
            )
        )
    return tuple(requirements), flags


@dataclass(slots=True)
class _Duration:
    start: int
    end: int
    text: str
    seconds: Decimal
    unit_seconds: int
    qualified: str | None  # "range" or "approximate"


def _durations(instruction: str) -> list[_Duration]:
    found: list[_Duration] = []
    for match in _DURATION.finditer(instruction):
        unit_seconds = _unit_seconds(match.group("unit"))
        qualified: str | None = None
        if match.group("low") is not None:
            qualified = "range"
        elif (match.group("prefix") or "").strip():
            qualified = "approximate"
        found.append(
            _Duration(
                start=match.start(),
                end=match.end(),
                text=match.group(0).strip(),
                seconds=Decimal(match.group("value")) * unit_seconds,
                unit_seconds=unit_seconds,
                qualified=qualified,
            )
        )
    # "1 h 30 min" is one duration written in two parts.
    merged: list[_Duration] = []
    for duration in found:
        previous = merged[-1] if merged else None
        gap = instruction[previous.end : duration.start] if previous is not None else ""
        if (
            previous is not None
            and previous.qualified is None
            and duration.qualified is None
            and re.fullmatch(r"\s*(?:,|and)?\s*", gap)
            and duration.unit_seconds < previous.unit_seconds
        ):
            merged[-1] = _Duration(
                start=previous.start,
                end=duration.end,
                text=instruction[previous.start : duration.end].strip(),
                seconds=previous.seconds + duration.seconds,
                unit_seconds=duration.unit_seconds,
                qualified=None,
            )
        else:
            merged.append(duration)
    return merged


def _timing(instruction: str) -> tuple[int | None, list[ExtractionFlag]]:
    flags: list[ExtractionFlag] = []
    durations = _durations(instruction)
    for duration in durations:
        if duration.qualified == "range":
            flags.append(
                _flag(
                    "TIMING_RANGE",
                    f"The source gives a time range ('{_quoted(duration.text)}'), so no timer "
                    "was set.",
                    duration.text,
                )
            )
        elif duration.qualified == "approximate":
            flags.append(
                _flag(
                    "TIMING_APPROXIMATE",
                    f"The source gives an approximate or bounded time "
                    f"('{_quoted(duration.text)}'), so no timer was set.",
                    duration.text,
                )
            )
    unclear = list(_UNCLEAR_TIMING.finditer(instruction))
    if durations:
        unclear.extend(_UNTIL.finditer(instruction))
    for match in unclear:
        flags.append(
            _flag(
                "TIMING_UNCLEAR",
                f"The timing depends on something LabJarvis cannot measure "
                f"('{_quoted(match.group(0))}'), so no timer was set.",
                match.group(0),
            )
        )
    if len(durations) > 1:
        flags.append(
            _flag(
                "TIMING_MULTIPLE",
                "The step mentions more than one duration ("
                + ", ".join(f"'{_quoted(duration.text)}'" for duration in durations[:4])
                + "), so no timer was set. Set the timer yourself or split the step.",
            )
        )
        return None, flags
    if len(durations) != 1 or durations[0].qualified is not None or unclear:
        return None, flags
    duration = durations[0]
    if duration.seconds != duration.seconds.to_integral_value() or not (
        1 <= duration.seconds <= MAX_DURATION_SECONDS
    ):
        flags.append(
            _flag(
                "TIMING_OUT_OF_RANGE",
                f"'{_quoted(duration.text)}' cannot be used as a timer; timers must be whole "
                "seconds between 1 second and 7 days.",
                duration.text,
            )
        )
        return None, flags
    return int(duration.seconds), flags


def _behaviour_flags(instruction: str) -> list[ExtractionFlag]:
    flags: list[ExtractionFlag] = []
    repetition = _REPETITION.search(instruction)
    if repetition is not None:
        flags.append(
            _flag(
                "REPETITION",
                f"The step repeats ('{_quoted(repetition.group(0))}'), but LabJarvis runs each "
                "step once. Split it into separate steps or confirm it as written.",
                repetition.group(0),
            )
        )
    condition = _CONDITIONAL.search(instruction)
    if condition is not None:
        flags.append(
            _flag(
                "CONDITIONAL",
                f"The step depends on a condition or choice ('{_quoted(condition.group(0))}'). "
                "Confirm how it should be followed.",
                condition.group(0),
            )
        )
    return flags


_LEADING_MARKER = re.compile(
    "^(?:\\(?\\d{1,3}(?:\\.\\d{1,3})*[.)]|\\(?[a-z]{1,4}[.)]|[\u2022\u25e6\u25aa\u25cf\u25cb\u25a0\\-*+\u2013>])\\s+",
    re.I,
)


def _inline_warnings(instruction: str) -> list[str]:
    notes: list[str] = []
    for line in instruction.splitlines():
        line = _LEADING_MARKER.sub("", line.strip()).replace("**", "")
        for sentence in _SENTENCE_BREAK.split(line):
            if _INLINE_WARNING.search(sentence):
                notes.append(sentence.strip())
    return notes


# ------------------------------------------------------------------ step assembly


@dataclass(slots=True)
class _StepBuilder:
    page: int
    section: str | None
    number: int | None
    ocr: bool
    table_row: bool
    lines: list[str]
    raw_lines: list[str]
    safety: list[str] = field(default_factory=list)
    flags: list[ExtractionFlag] = field(default_factory=list)
    sink: str = "instruction"

    def add_raw(self, raw: str) -> None:
        self.raw_lines.append(raw)

    def new_line(self, text: str) -> None:
        self.lines.append(text)
        self.sink = "instruction"

    def continue_text(self, text: str) -> None:
        if self.sink == "safety" and self.safety and not _TERMINAL.search(self.safety[-1]):
            self.safety[-1] = _join(self.safety[-1], text)
            return
        self.sink = "instruction"
        if self.lines and not _TERMINAL.search(self.lines[-1]):
            self.lines[-1] = _join(self.lines[-1], text)
        else:
            self.lines.append(text)


def _join(previous: str, text: str) -> str:
    if not previous:
        return text
    # A line broken after a hyphen keeps the hyphen: removing it would guess
    # whether "2-" + "mercaptoethanol" was a hyphenated word or a wrap.
    return f"{previous}{text}" if previous.endswith("-") else f"{previous} {text}"


def _title(instruction: str) -> str:
    first = instruction.splitlines()[0].strip() if instruction.strip() else ""
    sentence = _SENTENCE_BREAK.split(first)[0].strip() or first
    if len(sentence) <= MAX_TITLE_CHARACTERS:
        return sentence
    cut = sentence[: MAX_TITLE_CHARACTERS - 1]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return f"{cut.rstrip(' ,;:')}\u2026"


def _bounded_label(text: str | None, maximum: int) -> str | None:
    if text is None or not text.strip():
        return None
    text = text.strip()
    return text if len(text) <= maximum else f"{text[: maximum - 1]}\u2026"


def _dedupe(flags: list[ExtractionFlag]) -> tuple[ExtractionFlag, ...]:
    seen: set[tuple[str, str | None]] = set()
    unique: list[ExtractionFlag] = []
    for flag in flags:
        key = (flag.code, flag.excerpt)
        if key in seen:
            continue
        seen.add(key)
        unique.append(flag)
    if len(unique) > MAX_FLAGS_PER_STEP:
        unique = [
            *unique[: MAX_FLAGS_PER_STEP - 1],
            _flag("FLAG_LIMIT_REACHED", "This step has more review flags than can be listed."),
        ]
    return tuple(unique)


# ------------------------------------------------------------------ parsing

_EXCLUDED_BY_STAGE: tuple[frozenset[str | None], ...] = (
    # 1. only sections the document calls a procedure or method
    frozenset({"materials", "references", "notes", "safety", "background", None}),
    # 2. everything that is not clearly something other than a procedure
    frozenset({"materials", "references", "notes", "safety", "background"}),
    # 3. anything except references
    frozenset({"references"}),
)


def _assign_sections(lines: list[_Line]) -> list[tuple[str | None, str | None]]:
    """Return ``(section title, section kind)`` in effect for each line.

    An unrecognised heading ("Part B", "Day 2", a document title) is a
    subsection of whatever section it appears in, so "Procedure > Part B" stays
    a procedure and "Materials > Buffer A" stays materials.
    """

    title: str | None = None
    kind: str | None = None
    result: list[tuple[str | None, str | None]] = []
    for line in lines:
        if line.kind == "heading":
            title = line.body or title
            if line.heading_kind is not None:
                kind = line.heading_kind
        result.append((title, kind))
    return result


def _promote_headings(lines: list[_Line], active_top: set[int]) -> None:
    """Treat short title-like lines as headings where they cannot be step text.

    Outside a step a short, title-like line is a heading. Inside a step it is
    one only if it also sits directly before the next step - otherwise it may be
    a wrapped instruction line, and rejoining is the safer reading because the
    text stays visible in the step either way.
    """

    in_step = False
    for index, line in enumerate(lines):
        if index in active_top:
            in_step = True
            continue
        if line.kind == "heading":
            in_step = False
            continue
        if line.kind != "text" or line.table_row:
            continue
        if not _looks_like_heading(line.raw):
            continue
        if not in_step:
            line.kind, line.body = "heading", _clean_heading(line.raw)
            continue
        # Blank lines are not kept, so the next entry is the next non-blank line.
        if index + 1 in active_top and line.raw.strip("*_ ")[:1].isupper():
            line.kind, line.body = "heading", _clean_heading(line.raw)
            in_step = False


def _outermost(lines: list[_Line], candidates: list[int]) -> set[int]:
    """The least-nested candidates.

    Nesting is the stated list level first, then indentation - but only among
    items whose format states an indentation (Word lists). A typed number or a
    table row has no indentation to compare, so it is never counted as nested.
    """

    level = min(lines[index].level for index in candidates)
    at_level = [index for index in candidates if lines[index].level == level]
    indents = [indent for index in at_level if (indent := lines[index].indent) is not None]
    if not indents:
        return set(at_level)
    least = min(indents)
    return {
        index for index in at_level if lines[index].indent is None or lines[index].indent == least
    }


def _top_candidates(lines: list[_Line], in_scope: list[bool]) -> tuple[str, set[int]] | None:
    scoped = [index for index, line in enumerate(lines) if in_scope[index]]
    numbered = [index for index in scoped if lines[index].kind == "step"]
    if numbered:
        return "number", _outermost(lines, numbered)
    hierarchical = [index for index in scoped if lines[index].kind == "hier"]
    if hierarchical:
        depth = min(lines[index].depth for index in hierarchical)
        return "hier", {index for index in hierarchical if lines[index].depth == depth}
    bullets = [index for index in scoped if lines[index].kind == "bullet"]
    if bullets:
        return "bullet", _outermost(lines, bullets)
    return None


def parse_protocol(document: ExtractedDocument) -> ParsedProtocol:
    """Structure extracted text into draft steps plus every line not used by one."""

    lines = _lines(document)
    choice: tuple[str, set[int], list[bool]] | None = None
    for excluded in _EXCLUDED_BY_STAGE:
        sections = _assign_sections(lines)
        in_scope = [kind not in excluded for _, kind in sections]
        candidates = _top_candidates(lines, in_scope)
        if candidates is not None:
            choice = (candidates[0], candidates[1], in_scope)
            break
    if choice is None:
        raise DocumentError(
            "PROTOCOL_STEPS_REQUIRED",
            "no protocol steps were found; LabJarvis looks for numbered or bulleted "
            "steps (typed or Word list numbering), ideally under a Procedure or "
            "Method heading",
        )
    top_kind, top, in_scope = choice
    # Promoted headings carry no section kind, so they rename sections without
    # changing which ones are in scope - the choice above still holds.
    _promote_headings(lines, top)
    sections = _assign_sections(lines)

    builders: list[_StepBuilder] = []
    unassigned: list[UnassignedSourceText] = []
    pending_warnings: list[tuple[int, _Line]] = []
    current: _StepBuilder | None = None

    def leave(index: int, reason: str) -> None:
        line = lines[index]
        section = _bounded_label(sections[index][0], MAX_SECTION_CHARACTERS)
        previous = unassigned[-1] if unassigned else None
        if (
            previous is not None
            and previous.reason == reason
            and previous.page == line.page
            and previous.section == section
        ):
            unassigned[-1] = UnassignedSourceText(
                page=previous.page,
                section=previous.section,
                text=f"{previous.text}\n{line.raw}",
                reason=reason,
            )
        else:
            unassigned.append(
                UnassignedSourceText(page=line.page, section=section, text=line.raw, reason=reason)
            )

    for index, line in enumerate(lines):
        section_title, _ = sections[index]
        if line.kind == "furniture":
            if line.raw:
                leave(index, PAGE_HEADER_FOOTER)
            continue
        if line.kind == "heading":
            current = None
            continue
        if not in_scope[index]:
            current = None
            # A recognised materials/references/... section is named as such;
            # text under a title or unrecognised heading is simply outside the steps.
            leave(index, NON_PROCEDURE_SECTION if sections[index][1] else OUTSIDE_STEPS)
            continue
        if index in top:
            current = _StepBuilder(
                page=line.page,
                section=section_title,
                number=line.number,
                ocr=line.ocr,
                table_row=line.table_row,
                lines=[line.body],
                raw_lines=[line.raw],
            )
            if line.table_row and " | " in line.body:
                current.flags.append(
                    _flag(
                        "TABLE_ROW",
                        "This step came from a table row; its columns were joined with ' | '. "
                        "Check the column headings in the source.",
                        line.raw,
                    )
                )
            for _, warning in pending_warnings:
                current.safety.append(warning.body)
                current.add_raw(warning.raw)
                current.flags.append(
                    _flag(
                        "WARNING_PLACEMENT",
                        "A warning that appeared before this step was attached to it. "
                        "Confirm it applies to this step (it may apply to the whole section).",
                        warning.raw,
                    )
                )
            pending_warnings = []
            builders.append(current)
            continue
        if line.table_row and line.table_start and index + 1 in top and lines[index + 1].table_row:
            # The header row ("Step | Action | Time") of a table whose rows are
            # the steps: it describes the columns, it is not part of the step before.
            current = None
            leave(index, OUTSIDE_STEPS)
            continue
        if current is None:
            if line.kind == "warning":
                pending_warnings.append((index, line))
            else:
                leave(index, OUTSIDE_STEPS)
            continue

        current.add_raw(line.raw)
        if line.kind == "warning":
            current.safety.append(line.body)
            current.sink = "safety"
            current.flags.append(
                _flag(
                    "WARNING_PLACEMENT",
                    "A separate warning line after this step was attached to it. Confirm it "
                    "applies to this step and not to the next one.",
                    line.raw,
                )
            )
        elif line.kind in {"note", "substep", "bullet", "hier", "step"} or line.table_row:
            current.new_line(line.raw)
            if line.kind == "note" and line.blank_before:
                current.flags.append(
                    _flag(
                        "PARAGRAPH_ATTACHED",
                        "A separate note after this step was attached to it. Confirm it "
                        "belongs to this step and not to the whole protocol.",
                        line.raw,
                    )
                )
        elif line.paragraph or line.blank_before:
            current.new_line(line.body)
            # An indented paragraph is a Markdown list continuation and plainly
            # belongs to the step; an unindented one might be a general remark.
            if line.paragraph or line.level == 0:
                current.flags.append(
                    _flag(
                        "PARAGRAPH_ATTACHED",
                        "A separate paragraph after this step was attached to it. Confirm it "
                        "belongs to this step.",
                        line.raw,
                    )
                )
        else:
            current.continue_text(line.body)

    for index, _ in pending_warnings:
        leave(index, WARNING_WITHOUT_STEP)

    if len(builders) > MAX_STEPS:
        raise DocumentError(
            "PROTOCOL_STEP_COUNT_EXCEEDED",
            f"document has more than {MAX_STEPS} steps",
        )

    steps: list[ProtocolStepDraft] = []
    previous: _StepBuilder | None = None
    for position, builder in enumerate(builders, start=1):
        step_id = f"step-{position}"
        flags: list[ExtractionFlag] = []
        if position == 1 and top_kind == "bullet":
            flags.append(
                _flag(
                    "BULLET_STEPS",
                    "No numbered steps were found, so bullet points were used as steps. "
                    "Confirm the grouping and order.",
                )
            )
        if position == 1 and top_kind == "hier":
            flags.append(
                _flag(
                    "SUBSECTION_STEPS",
                    "No top-level numbered steps were found, so numbered sub-sections were "
                    "used as steps. Confirm the grouping and order.",
                )
            )
        if top_kind == "number" and builder.number is not None:
            expected = previous.number + 1 if previous and previous.number is not None else 1
            if builder.number != expected:
                restarted = (
                    previous is not None
                    and builder.number <= (previous.number or 0)
                    and builder.section != previous.section
                )
                flags.append(
                    _flag(
                        "NUMBERING_RESTARTED" if restarted else "NUMBERING_IRREGULAR",
                        (
                            f"The source restarts numbering at {builder.number} in "
                            f"'{builder.section or 'a new section'}'. Steps were numbered "
                            "continuously; confirm the sections run in this order and are "
                            "not alternatives."
                        )
                        if restarted
                        else (
                            f"The source numbers this step {builder.number} where "
                            f"{expected} was expected. Steps were numbered continuously; "
                            "check for a missing, repeated or misplaced step."
                        ),
                        builder.raw_lines[0],
                    )
                )
        if builder.ocr:
            flags.append(
                _flag(
                    "OCR_DERIVED",
                    "This step's text was read by OCR. Compare every word and number with "
                    "the original document.",
                )
            )

        instruction_lines: list[str] = []
        overflow: list[str] = []
        length = 0
        for text in builder.lines:
            if not text.strip():
                continue
            if overflow or length + len(text) + 1 > MAX_INSTRUCTION_CHARACTERS:
                overflow.append(text)
            else:
                instruction_lines.append(text)
                length += len(text) + 1
        if not instruction_lines:
            # A single line longer than the limit: keep what fits, preserve the rest.
            first = builder.lines[0]
            instruction_lines = [first[:MAX_INSTRUCTION_CHARACTERS]]
            overflow = [first[MAX_INSTRUCTION_CHARACTERS:], *overflow[1:]]
        if overflow:
            flags.append(
                _flag(
                    "STEP_TEXT_OVERFLOW",
                    "This step's text is longer than 5,000 characters. The remainder is "
                    "listed under source text not used as steps.",
                )
            )
            unassigned.append(
                UnassignedSourceText(
                    page=builder.page,
                    section=_bounded_label(builder.section, MAX_SECTION_CHARACTERS),
                    text="\n".join(overflow),
                    reason=STEP_TEXT_OVERFLOW,
                )
            )
        instruction = "\n".join(instruction_lines)

        requirements, requirement_flags = _requirements(instruction, step_id=step_id)
        duration, timing_flags = _timing(instruction)
        flags.extend(requirement_flags)
        flags.extend(timing_flags)
        flags.extend(_behaviour_flags(instruction))
        flags.extend(builder.flags)

        safety: list[str] = []
        for note in [*builder.safety, *_inline_warnings(instruction)]:
            note = " ".join(note.split())
            if not note or note in safety:
                continue
            if len(note) > MAX_SAFETY_NOTE_CHARACTERS:
                # The full warning stays readable in the instruction.
                instruction = f"{instruction}\n{note}"[:MAX_INSTRUCTION_CHARACTERS]
                note = f"{note[: MAX_SAFETY_NOTE_CHARACTERS - 1]}\u2026"
            safety.append(note)
        safety = safety[:MAX_SAFETY_NOTES_PER_STEP]

        excerpt = "\n".join(builder.raw_lines)
        if len(excerpt) > MAX_EXCERPT_CHARACTERS:
            excerpt = f"{excerpt[: MAX_EXCERPT_CHARACTERS - 1]}\u2026"

        steps.append(
            ProtocolStepDraft(
                id=step_id,
                number=position,
                title=_title(instruction),
                instruction=instruction,
                requirements=requirements,
                duration_seconds=duration,
                safety_notes=tuple(safety),
                source=SourceReference(
                    page=builder.page,
                    section=_bounded_label(builder.section, MAX_SECTION_CHARACTERS),
                    excerpt=excerpt,
                    ocr_derived=builder.ocr,
                ),
                flags=_dedupe(flags),
            )
        )
        previous = builder

    return ParsedProtocol(steps=tuple(steps), unassigned=tuple(unassigned))


def parse_protocol_steps(document: ExtractedDocument) -> tuple[ProtocolStepDraft, ...]:
    """Propose steps only; see :func:`parse_protocol` for the unassigned text too."""

    return parse_protocol(document).steps
