"""Bounded extraction of protocol text from PDF, DOCX, plain-text and Markdown uploads.

Extraction turns an untrusted upload into ordered source lines. It does not
decide what is a step - that is :mod:`labjarvis.infrastructure.protocol_import` -
but it does carry the structure a format states explicitly (Word list numbering,
heading styles, table cells), because that structure is lost once only text is
left and the parser would otherwise have to guess it back.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from hashlib import sha256
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any
from zipfile import BadZipFile, ZipFile

import fitz  # type: ignore[import-untyped]
from docx import Document
from docx.opc.exceptions import PackageNotFoundError
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from labjarvis.infrastructure.document_ocr import DocumentOcrError, require_document_ocr
from labjarvis.ports.models import DocumentOcrPort

PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TEXT_MIME = "text/plain"
MARKDOWN_MIME = "text/markdown"

_TEXT_SUFFIXES = {".txt": TEXT_MIME, ".md": MARKDOWN_MIME, ".markdown": MARKDOWN_MIME}
# Browsers label .md files inconsistently (and Windows often sends nothing), so
# text uploads are recognised by extension plus a strict decode, not by MIME alone.
_TEXT_CONTENT_TYPES = {
    "",
    "text/plain",
    "text/markdown",
    "text/x-markdown",
    "application/octet-stream",
}
_ALLOWED_CONTROL_CHARACTERS = {"\t", "\n", "\r", "\f"}


class DocumentError(ValueError):
    """A document is unsupported or violates configured import limits."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class SourceLine:
    """One line of extracted text plus any structure the format stated for it.

    ``text`` is what the reviewer sees in the source pane, including a rendered
    list marker where Word generated one. The other fields are set only when the
    document itself says so; for PDF and plain text they stay empty and the
    parser reads structure from the text.
    """

    text: str
    heading: bool = False
    list_kind: str | None = None  # "numbered", "lettered" or "bullet"
    list_level: int | None = None
    marker: str | None = None
    body: str | None = None
    table_cells: tuple[str, ...] = ()
    table_start: bool = False
    indent: int | None = None  # left indentation in twips, where the format states it


@dataclass(frozen=True, slots=True)
class ExtractedPage:
    number: int
    text: str
    ocr_derived: bool = False
    ocr_adapter: str | None = None
    ocr_model_name: str | None = None
    lines: tuple[SourceLine, ...] = field(default=())

    def source_lines(self) -> tuple[SourceLine, ...]:
        if self.lines:
            return self.lines
        return tuple(SourceLine(text=line) for line in self.text.splitlines())


@dataclass(frozen=True, slots=True)
class ExtractedDocument:
    media_type: str
    sha256: str
    pages: tuple[ExtractedPage, ...]

    @property
    def text(self) -> str:
        return "\n".join(
            (
                f"[OCR-derived page {page.number}; "
                f"adapter={page.ocr_adapter}; model={page.ocr_model_name}]\n{page.text}"
                if page.ocr_derived
                else page.text
            )
            for page in self.pages
        )


class DocumentExtractor:
    """Extract trusted text representations from untrusted bounded uploads."""

    def __init__(
        self,
        *,
        max_bytes: int,
        max_pages: int,
        max_docx_uncompressed_bytes: int = 50 * 1024 * 1024,
        max_docx_entries: int = 2000,
        max_extracted_characters: int = 1_000_000,
        document_ocr: DocumentOcrPort | None = None,
        min_pdf_text_characters_per_page: int = 8,
    ) -> None:
        self._max_bytes = max_bytes
        self._max_pages = max_pages
        self._max_docx_uncompressed_bytes = max_docx_uncompressed_bytes
        self._max_docx_entries = max_docx_entries
        self._max_extracted_characters = max_extracted_characters
        self._document_ocr = document_ocr
        self._min_pdf_text_characters_per_page = min_pdf_text_characters_per_page

    def extract(
        self,
        filename: str,
        content_type: str,
        data: bytes,
        *,
        allow_ocr: bool = False,
    ) -> ExtractedDocument:
        if len(data) > self._max_bytes:
            raise DocumentError("DOCUMENT_TOO_LARGE", "document exceeds the configured size limit")

        suffix = Path(filename).suffix.casefold()
        media = content_type.split(";", 1)[0].strip().casefold()
        if suffix == ".pdf" and media == PDF_MIME and data.startswith(b"%PDF-"):
            pages = self._extract_pdf(data)
            if self._pdf_requires_ocr(pages):
                if not allow_ocr:
                    raise DocumentError(
                        "DOCUMENT_OCR_REQUIRED",
                        "PDF has too little extractable text; retry explicitly with document OCR",
                    )
                try:
                    ocr_pages = require_document_ocr(self._document_ocr).extract_pages(data)
                except DocumentOcrError as exc:
                    raise DocumentError(exc.code, str(exc)) from exc
                pages = tuple(
                    ExtractedPage(
                        number=page.page,
                        text=page.text,
                        ocr_derived=True,
                        ocr_adapter=page.adapter,
                        ocr_model_name=page.model_name,
                    )
                    for page in ocr_pages
                )
            media_type = PDF_MIME
        elif suffix == ".docx" and media == DOCX_MIME and data.startswith(b"PK\x03\x04"):
            self._validate_docx_archive(data)
            pages = self._extract_docx(data)
            media_type = DOCX_MIME
        elif suffix in _TEXT_SUFFIXES and media in _TEXT_CONTENT_TYPES:
            pages = self._extract_text(data)
            media_type = _TEXT_SUFFIXES[suffix]
        else:
            raise DocumentError(
                "DOCUMENT_SIGNATURE_MISMATCH",
                "document extension, MIME type, and signature do not agree",
            )

        if sum(len(page.text) for page in pages) > self._max_extracted_characters:
            raise DocumentError(
                "DOCUMENT_TEXT_TOO_LARGE",
                "extracted text exceeds the configured character limit",
            )
        if not any(page.text.strip() for page in pages):
            raise DocumentError(
                "DOCUMENT_TEXT_REQUIRED",
                "document must contain extractable text; image-only files are unsupported",
            )
        return ExtractedDocument(
            media_type=media_type,
            sha256=sha256(data).hexdigest(),
            pages=pages,
        )

    def _pdf_requires_ocr(self, pages: tuple[ExtractedPage, ...]) -> bool:
        usable = sum(
            1
            for page in pages
            for character in page.text
            if character.isalnum()
        )
        return usable < self._min_pdf_text_characters_per_page * max(1, len(pages))

    # ------------------------------------------------------------------ text

    def _extract_text(self, data: bytes) -> tuple[ExtractedPage, ...]:
        """Decode a plain-text or Markdown upload, refusing anything binary.

        A text file has no signature to check, so the checks are that it decodes
        strictly and contains no control characters a text editor would not
        write. That rejects a renamed PDF, archive, or executable.
        """

        if data.startswith((b"%PDF-", b"PK\x03\x04")):
            raise DocumentError(
                "DOCUMENT_SIGNATURE_MISMATCH",
                "document extension, MIME type, and signature do not agree",
            )
        try:
            if data.startswith((b"\xff\xfe", b"\xfe\xff")):
                text = data.decode("utf-16")
            else:
                text = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise DocumentError(
                "DOCUMENT_TEXT_ENCODING",
                "text protocols must be saved as UTF-8",
            ) from exc
        if any(
            ord(character) < 32 and character not in _ALLOWED_CONTROL_CHARACTERS
            for character in text
        ):
            raise DocumentError(
                "DOCUMENT_SIGNATURE_MISMATCH",
                "document extension, MIME type, and signature do not agree",
            )
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        # A form feed is how text exported from a PDF marks a page break, so it
        # keeps its page provenance rather than collapsing into page 1.
        chunks = text.split("\f")
        if len(chunks) > self._max_pages:
            raise DocumentError(
                "DOCUMENT_TOO_MANY_PAGES",
                "document exceeds the configured page limit",
            )
        return tuple(
            ExtractedPage(number=index, text=chunk.strip("\n"))
            for index, chunk in enumerate(chunks, start=1)
        )

    # ------------------------------------------------------------------ DOCX

    def _validate_docx_archive(self, data: bytes) -> None:
        try:
            with ZipFile(BytesIO(data)) as archive:
                entries = archive.infolist()
                if len(entries) > self._max_docx_entries:
                    raise DocumentError(
                        "DOCUMENT_TOO_MANY_ENTRIES",
                        "DOCX archive exceeds the configured entry limit",
                    )
                if sum(entry.file_size for entry in entries) > self._max_docx_uncompressed_bytes:
                    raise DocumentError(
                        "DOCUMENT_EXPANDED_TOO_LARGE",
                        "DOCX archive exceeds the configured uncompressed size limit",
                    )
                if any(entry.flag_bits & 0x1 for entry in entries):
                    raise DocumentError(
                        "DOCUMENT_ENCRYPTED",
                        "encrypted DOCX files are unsupported",
                    )

                names = [entry.filename for entry in entries]
                unsafe_name = any(
                    "\\" in name
                    or PurePosixPath(name).is_absolute()
                    or ".." in PurePosixPath(name).parts
                    for name in names
                )
                if unsafe_name or len(names) != len(set(names)):
                    raise DocumentError("DOCUMENT_CORRUPT", "DOCX archive has unsafe entries")
                required = {"[Content_Types].xml", "word/document.xml"}
                if not required.issubset(names):
                    raise DocumentError("DOCUMENT_CORRUPT", "DOCX package structure is invalid")
        except BadZipFile as exc:
            raise DocumentError("DOCUMENT_CORRUPT", "DOCX could not be parsed") from exc

    def _extract_docx(self, data: bytes) -> tuple[ExtractedPage, ...]:
        try:
            document = Document(BytesIO(data))
        except (PackageNotFoundError, ValueError, BadZipFile) as exc:
            raise DocumentError("DOCUMENT_CORRUPT", "DOCX could not be parsed") from exc

        numbering = _DocxNumbering(document)
        lines: list[SourceLine] = []
        # Paragraphs and tables are read in document order. Reading all
        # paragraphs and then all tables moved every table-based step to the end
        # of the protocol.
        for block in _docx_blocks(document.element.body, document):
            if isinstance(block, Paragraph):
                line = _docx_paragraph_line(block, numbering)
                if line is not None:
                    lines.append(line)
            else:
                lines.extend(_docx_table_lines(block))
        return (
            ExtractedPage(
                number=1,
                text="\n".join(line.text for line in lines),
                lines=tuple(lines),
            ),
        )

    # ------------------------------------------------------------------ PDF

    def _extract_pdf(self, data: bytes) -> tuple[ExtractedPage, ...]:
        try:
            document = fitz.open(stream=data, filetype="pdf")
        except (fitz.FileDataError, RuntimeError) as exc:
            raise DocumentError("DOCUMENT_CORRUPT", "PDF could not be parsed") from exc
        try:
            if document.needs_pass:
                raise DocumentError("DOCUMENT_ENCRYPTED", "encrypted PDFs are unsupported")
            if document.page_count > self._max_pages:
                raise DocumentError(
                    "DOCUMENT_TOO_MANY_PAGES",
                    "document exceeds the configured page limit",
                )
            return tuple(
                ExtractedPage(number=index + 1, text=page.get_text("text").strip())
                for index, page in enumerate(document)
            )
        finally:
            document.close()


# ---------------------------------------------------------------- DOCX helpers


def _docx_blocks(container: Any, document: Any) -> Iterator[Paragraph | Table]:
    for child in container.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, document)
        elif child.tag == qn("w:tbl"):
            yield Table(child, document)
        elif child.tag == qn("w:sdt"):
            # Content controls wrap ordinary paragraphs; python-docx's own
            # paragraph list skips them, which silently dropped their text.
            content = child.find(qn("w:sdtContent"))
            if content is not None:
                yield from _docx_blocks(content, document)


_HEADING_STYLE_PREFIXES = ("heading", "title", "subtitle")


def _docx_paragraph_line(paragraph: Paragraph, numbering: _DocxNumbering) -> SourceLine | None:
    body = " ".join(paragraph.text.split())
    if not body:
        return None
    style_name = (paragraph.style.name or "").casefold() if paragraph.style is not None else ""
    heading = style_name.startswith(_HEADING_STYLE_PREFIXES)
    reference = _paragraph_numbering(paragraph)
    rendered = numbering.render(*reference) if reference is not None and not heading else None
    if rendered is None:
        return SourceLine(text=body, heading=heading)
    marker, kind, level = rendered
    prefix = "  " * level
    return SourceLine(
        text=f"{prefix}{marker} {body}" if marker else f"{prefix}{body}",
        list_kind=kind,
        list_level=level,
        marker=marker or None,
        body=body,
        # Word's "List Number 2" is a separate list at level 0 that is only
        # indented further, so indentation is what marks it as nested.
        indent=_left_indent(paragraph, numbering, *reference) if reference is not None else None,
    )


def _left_indent(paragraph: Paragraph, numbering: _DocxNumbering, num_id: int, level: int) -> int:
    direct = paragraph.paragraph_format.left_indent
    if direct is not None:
        return int(direct.twips)
    from_list = numbering.indent(num_id, level)
    if from_list is not None:
        return from_list
    style = paragraph.style
    depth = 0
    while style is not None and depth < 20:
        styled = style.paragraph_format.left_indent
        if styled is not None:
            return int(styled.twips)
        style = style.base_style
        depth += 1
    return 0


def _docx_table_lines(table: Table) -> list[SourceLine]:
    lines: list[SourceLine] = []
    for row in table.rows:
        cells: list[str] = []
        seen: set[int] = set()
        for cell in row.cells:
            # A merged cell is returned once per grid column it spans.
            if id(cell._tc) in seen:
                continue
            seen.add(id(cell._tc))
            text = " ".join(cell.text.split())
            if text:
                cells.append(text)
        if cells:
            lines.append(
                SourceLine(
                    text=" | ".join(cells),
                    table_cells=tuple(cells),
                    table_start=not lines,
                )
            )
    return lines


def _paragraph_numbering(paragraph: Paragraph) -> tuple[int, int] | None:
    """Return ``(numId, ilvl)`` from the paragraph or, failing that, its style chain."""

    num_id, level = _num_pr(paragraph._p.pPr)
    style = paragraph.style
    depth = 0
    while num_id is None and style is not None and depth < 20:
        style_num_id, style_level = _num_pr(style.element.pPr)
        num_id = style_num_id
        if level is None:
            level = style_level
        style = style.base_style
        depth += 1
    if num_id is None or num_id == 0:
        return None
    return num_id, min(max(level or 0, 0), 8)


def _num_pr(properties: Any) -> tuple[int | None, int | None]:
    numbering = properties.find(qn("w:numPr")) if properties is not None else None
    if numbering is None:
        return None, None
    num_id = _child_value(numbering, "w:numId")
    level = _child_value(numbering, "w:ilvl")
    return (
        int(num_id) if num_id is not None and num_id.isdigit() else None,
        int(level) if level is not None and level.isdigit() else None,
    )


_ROMAN = (
    (1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
    (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i"),
)


def _roman(value: int) -> str:
    result = ""
    for amount, symbol in _ROMAN:
        while value >= amount:
            result += symbol
            value -= amount
    return result


def _letters(value: int) -> str:
    # Word repeats the letter after z: aa, bb, ...
    letter = chr(ord("a") + (value - 1) % 26)
    return letter * ((value - 1) // 26 + 1)


def _format_counter(value: int, number_format: str) -> str:
    if number_format == "lowerLetter":
        return _letters(value)
    if number_format == "upperLetter":
        return _letters(value).upper()
    if number_format == "lowerRoman":
        return _roman(value)
    if number_format == "upperRoman":
        return _roman(value).upper()
    if number_format == "decimalZero":
        return f"{value:02d}"
    return str(value)


@dataclass(slots=True)
class _Level:
    number_format: str
    text: str
    start: int
    indent: int | None


class _DocxNumbering:
    """Reproduce the list markers Word draws but never stores in paragraph text.

    ``paragraph.text`` omits automatic numbering entirely, so an ordinary Word
    numbered list arrived as unnumbered paragraphs and was rejected as having
    no steps. Counters follow Word: they continue per abstract list, deeper
    levels restart when a shallower level advances, and a ``startOverride``
    restarts the list.
    """

    def __init__(self, document: Any) -> None:
        self._abstract: dict[str, dict[int, _Level]] = {}
        self._instances: dict[int, tuple[str, dict[int, int]]] = {}
        self._counters: dict[str, list[int | None]] = {}
        self._started: set[int] = set()
        try:
            root = document.part.numbering_part.element
        except (KeyError, NotImplementedError, AttributeError):
            return
        for abstract in root.findall(qn("w:abstractNum")):
            abstract_id = abstract.get(qn("w:abstractNumId"))
            if abstract_id is None:
                continue
            levels: dict[int, _Level] = {}
            for level in abstract.findall(qn("w:lvl")):
                index = int(level.get(qn("w:ilvl"), "0"))
                number_format = _child_value(level, "w:numFmt") or "decimal"
                text = _child_value(level, "w:lvlText")
                start = _child_value(level, "w:start")
                levels[index] = _Level(
                    number_format=number_format,
                    text=text if text is not None else f"%{index + 1}.",
                    start=int(start) if start is not None and start.isdigit() else 1,
                    indent=_level_indent(level),
                )
            self._abstract[abstract_id] = levels
        for instance in root.findall(qn("w:num")):
            num_id = instance.get(qn("w:numId"))
            abstract_id = _child_value(instance, "w:abstractNumId")
            if num_id is None or abstract_id is None:
                continue
            overrides: dict[int, int] = {}
            for override in instance.findall(qn("w:lvlOverride")):
                start = _child_value(override, "w:startOverride")
                if start is not None and start.isdigit():
                    overrides[int(override.get(qn("w:ilvl"), "0"))] = int(start)
            self._instances[int(num_id)] = (abstract_id, overrides)

    def indent(self, num_id: int, level: int) -> int | None:
        instance = self._instances.get(num_id)
        if instance is None:
            return None
        definition = self._abstract.get(instance[0], {}).get(level)
        return definition.indent if definition is not None else None

    def render(self, num_id: int, level: int) -> tuple[str, str, int] | None:
        instance = self._instances.get(num_id)
        if instance is None:
            return None
        abstract_id, overrides = instance
        levels = self._abstract.get(abstract_id, {})
        definition = levels.get(level)
        if definition is None:
            return None
        counters = self._counters.setdefault(abstract_id, [None] * 9)
        if num_id not in self._started:
            self._started.add(num_id)
            for override_level, start in overrides.items():
                if 0 <= override_level < 9:
                    counters[override_level] = start - 1
        current = counters[level]
        counters[level] = (current + 1) if current is not None else definition.start
        for deeper in range(level + 1, 9):
            counters[deeper] = None

        if definition.number_format == "bullet":
            return "•", "bullet", level
        if definition.number_format == "none":
            return "", "bullet", level
        marker = definition.text
        for index in range(level, -1, -1):
            placeholder = f"%{index + 1}"
            if placeholder not in marker:
                continue
            level_definition = levels.get(index, definition)
            value = counters[index]
            if value is None:
                value = level_definition.start
            marker = marker.replace(
                placeholder,
                _format_counter(value, level_definition.number_format),
            )
        kind = (
            "numbered"
            if definition.number_format in {"decimal", "decimalZero"}
            else "lettered"
        )
        return marker.strip(), kind, level


def _level_indent(level: Any) -> int | None:
    properties = level.find(qn("w:pPr"))
    indentation = properties.find(qn("w:ind")) if properties is not None else None
    if indentation is None:
        return None
    value = indentation.get(qn("w:left")) or indentation.get(qn("w:start"))
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def _child_value(element: Any, tag: str) -> str | None:
    child = element.find(qn(tag))
    if child is None:
        return None
    value = child.get(qn("w:val"))
    return str(value) if value is not None else None
