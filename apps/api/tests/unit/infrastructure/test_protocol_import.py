"""Protocol import turns ordinary researcher documents into reviewable steps.

Each test feeds a document written the way protocols are actually written -
Word auto-numbering, sub-steps, wrapped PDF lines, running headers, Markdown,
materials and reference lists - and checks two things: the structure proposed,
and that no source text went missing on the way. The rule under test is
*extract and structure; never silently invent, alter or discard*.
"""

from __future__ import annotations

import re
from decimal import Decimal
from io import BytesIO

import fitz
import pytest
from docx import Document
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls

from labjarvis.domain.protocol_validation import validate_protocol_steps
from labjarvis.infrastructure.documents import (
    DOCX_MIME,
    MARKDOWN_MIME,
    PDF_MIME,
    TEXT_MIME,
    DocumentError,
    DocumentExtractor,
    ExtractedDocument,
    ExtractedPage,
)
from labjarvis.infrastructure.protocol_import import (
    NON_PROCEDURE_SECTION,
    OUTSIDE_STEPS,
    PAGE_HEADER_FOOTER,
    WARNING_WITHOUT_STEP,
    ParsedProtocol,
    parse_protocol,
)

EXTRACTOR = DocumentExtractor(max_bytes=5_000_000, max_pages=20)


def text_document(text: str) -> ExtractedDocument:
    return ExtractedDocument(
        media_type=TEXT_MIME,
        sha256="0" * 64,
        pages=(ExtractedPage(number=1, text=text),),
    )


def parse_text(text: str) -> ParsedProtocol:
    return parse_protocol(text_document(text))


def flag_codes(parsed: ParsedProtocol, step_number: int) -> list[str]:
    return [flag.code for flag in parsed.steps[step_number - 1].flags]


_MARKER = re.compile(
    r"^(?:#{1,6}\s+|step\s*\d+\s*[.:)]?\s*|\(?\d{1,3}(?:\.\d{1,3})*[.):]?(?:\s+|$)|"
    r"\(?[a-z]{1,4}[.)](?:\s+|$)|[•\-*+>|]\s*)",
    re.I,
)


def _normalise(text: str) -> str:
    return " ".join(text.replace("**", "").replace("|", " ").split()).casefold()


def assert_nothing_lost(document: ExtractedDocument, parsed: ParsedProtocol) -> None:
    """Every source line's words appear in a step, a heading, or the unassigned list."""

    places: list[str] = []
    for step in parsed.steps:
        places.append(step.instruction)
        places.extend(step.safety_notes)
        places.append(step.source.section or "")
    for item in parsed.unassigned:
        places.append(item.text)
        places.append(item.section or "")
    corpus = _normalise("\n".join(places))
    for page in document.pages:
        for line in page.text.splitlines():
            # A table row's first cell may be the step number, so drop that too.
            content = _MARKER.sub("", line.strip().lstrip("| ")).strip(" :*_#|")
            if not content or re.fullmatch(r"[-:| ]+", content):
                continue
            assert _normalise(content) in corpus, f"source text was lost: {line!r}"


# --------------------------------------------------------------- DOCX fixtures


def _add_multilevel_numbering(document: Document) -> int:
    """Register a Word multi-level list: 1. / a) / bullet, as Word writes it."""

    numbering = document.part.numbering_part.element
    abstract = parse_xml(
        f'<w:abstractNum {nsdecls("w")} w:abstractNumId="90">'
        '<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/>'
        '<w:lvlText w:val="%1."/><w:pPr><w:ind w:left="360"/></w:pPr></w:lvl>'
        '<w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="lowerLetter"/>'
        '<w:lvlText w:val="%2)"/><w:pPr><w:ind w:left="720"/></w:pPr></w:lvl>'
        '<w:lvl w:ilvl="2"><w:start w:val="1"/><w:numFmt w:val="bullet"/>'
        '<w:lvlText w:val=""/><w:pPr><w:ind w:left="1080"/></w:pPr></w:lvl>'
        "</w:abstractNum>"
    )
    numbering.insert(0, abstract)
    numbering.append(
        parse_xml(f'<w:num {nsdecls("w")} w:numId="90"><w:abstractNumId w:val="90"/></w:num>')
    )
    return 90


def _list_paragraph(document: Document, text: str, *, num_id: int, level: int) -> None:
    paragraph = document.add_paragraph(text)
    properties = paragraph._p.get_or_add_pPr()
    properties.append(
        parse_xml(
            f'<w:numPr {nsdecls("w")}><w:ilvl w:val="{level}"/>'
            f'<w:numId w:val="{num_id}"/></w:numPr>'
        )
    )


def _docx_bytes(document: Document) -> bytes:
    stream = BytesIO()
    document.save(stream)
    return stream.getvalue()


def realistic_docx() -> bytes:
    document = Document()
    document.add_heading("Plasmid miniprep", 0)
    document.add_paragraph("This protocol isolates plasmid DNA from a 5 mL overnight culture.")
    document.add_heading("Materials", 1)
    document.add_paragraph("P1 resuspension buffer", style="List Bullet")
    document.add_paragraph("1. Ethanol 70% (typed numbering)")
    document.add_heading("Procedure", 1)
    num_id = _add_multilevel_numbering(document)
    _list_paragraph(
        document, "Pellet the cells by centrifugation for 10 min.", num_id=num_id, level=0
    )
    _list_paragraph(document, "Prepare the lysis mix:", num_id=num_id, level=0)
    _list_paragraph(document, "Add 250 uL buffer P1", num_id=num_id, level=1)
    _list_paragraph(document, "Add 250 uL buffer P2", num_id=num_id, level=1)
    _list_paragraph(document, "invert gently", num_id=num_id, level=2)
    document.add_paragraph("Warning: buffer P2 contains sodium hydroxide.")
    _list_paragraph(document, "Incubate for 5 min.", num_id=num_id, level=0)
    table = document.add_table(rows=3, cols=3)
    for row, cells in enumerate(
        (
            ("Step", "Action", "Time"),
            ("4", "Centrifuge the lysate", "10 min"),
            ("5", "Transfer the supernatant to a column", ""),
        )
    ):
        for column, value in enumerate(cells):
            table.cell(row, column).text = value
    document.add_heading("References", 1)
    document.add_paragraph(
        "Birnboim HC, Doly J. (1979) Nucleic Acids Res 7: 1513.", style="List Number"
    )
    return _docx_bytes(document)


def test_word_auto_numbered_list_with_substeps_and_table_becomes_ordered_steps() -> None:
    extracted = EXTRACTOR.extract("miniprep.docx", DOCX_MIME, realistic_docx())
    parsed = parse_protocol(extracted)

    assert [step.id for step in parsed.steps] == [f"step-{n}" for n in range(1, 6)]
    assert [step.number for step in parsed.steps] == [1, 2, 3, 4, 5]
    assert parsed.steps[0].instruction == "Pellet the cells by centrifugation for 10 min."
    assert parsed.steps[0].duration_seconds == 600
    # Word-generated sub-step markers are reproduced and the sub-steps stay in the step.
    assert parsed.steps[1].instruction.splitlines() == [
        "Prepare the lysis mix:",
        "a) Add 250 uL buffer P1",
        "b) Add 250 uL buffer P2",
        "• invert gently",
    ]
    assert [(r.name, r.quantity, r.unit) for r in parsed.steps[1].requirements] == [
        ("buffer p1", Decimal("250"), "uL"),
        ("buffer p2", Decimal("250"), "uL"),
    ]
    # The standalone warning is kept as a safety note and flagged for placement.
    assert parsed.steps[1].safety_notes == ("Warning: buffer P2 contains sodium hydroxide.",)
    assert "WARNING_PLACEMENT" in flag_codes(parsed, 2)
    assert parsed.steps[2].duration_seconds == 300
    # Table rows stay in document order instead of being appended at the end.
    assert parsed.steps[3].instruction == "Centrifuge the lysate | 10 min"
    assert "TABLE_ROW" in flag_codes(parsed, 4)
    assert parsed.steps[4].instruction == "Transfer the supernatant to a column"
    assert all(step.source.section == "Procedure" for step in parsed.steps)

    reasons = {(item.reason, item.section) for item in parsed.unassigned}
    assert (NON_PROCEDURE_SECTION, "Materials") in reasons
    assert (NON_PROCEDURE_SECTION, "References") in reasons
    assert (OUTSIDE_STEPS, "Procedure") in reasons  # the "Step | Action | Time" header
    assert (OUTSIDE_STEPS, "Plasmid miniprep") in reasons  # the introduction
    validate_protocol_steps(parsed.steps)
    assert_nothing_lost(extracted, parsed)


def test_indented_word_list_style_is_nested_under_the_outer_list() -> None:
    document = Document()
    document.add_paragraph("Procedure")
    document.add_paragraph("Prepare the master mix.", style="List Number")
    document.add_paragraph("Add 10 uL buffer", style="List Number 2")
    document.add_paragraph("Run the PCR program.", style="List Number")
    extracted = EXTRACTOR.extract("pcr.docx", DOCX_MIME, _docx_bytes(document))

    parsed = parse_protocol(extracted)

    assert len(parsed.steps) == 2
    assert parsed.steps[0].instruction == "Prepare the master mix.\n1. Add 10 uL buffer"
    assert parsed.steps[1].instruction == "Run the PCR program."
    assert parsed.steps[1].flags == ()


def test_word_numbering_is_rendered_into_the_preserved_source_text() -> None:
    document = Document()
    document.add_paragraph("Add 5 mL water.", style="List Number")
    document.add_paragraph("Stir for 2 minutes.", style="List Number")

    extracted = EXTRACTOR.extract("stir.docx", DOCX_MIME, _docx_bytes(document))

    assert extracted.text == "1. Add 5 mL water.\n2. Stir for 2 minutes."


# ---------------------------------------------------------------- PDF fixtures


def realistic_pdf() -> bytes:
    pages = (
        "Procedure\n1.\nAdd 5 mL water to the\nflask and swirl.\n2. Mix for 2 min.\n3. Continue",
        "mixing for another 1 min.\n4. Stop the stirrer.",
    )
    document = fitz.open()
    for number, body in enumerate(pages, start=1):
        page = document.new_page()
        page.insert_text((72, 40), "Teaching Lab SOP 12 - revision 3")
        page.insert_text((72, 100), body)
        page.insert_text((72, 800), f"Page {number} of 2")
    data = document.tobytes()
    document.close()
    return data


def test_pdf_wrapped_lines_marker_lines_page_breaks_and_running_headers() -> None:
    extracted = EXTRACTOR.extract("sop.pdf", PDF_MIME, realistic_pdf())
    parsed = parse_protocol(extracted)

    assert [step.instruction for step in parsed.steps] == [
        "Add 5 mL water to the flask and swirl.",
        "Mix for 2 min.",
        "Continue mixing for another 1 min.",
        "Stop the stirrer.",
    ]
    assert [step.source.page for step in parsed.steps] == [1, 1, 1, 2]
    assert parsed.steps[2].duration_seconds == 60
    furniture = [item for item in parsed.unassigned if item.reason == PAGE_HEADER_FOOTER]
    assert "Teaching Lab SOP 12 - revision 3" in "\n".join(item.text for item in furniture)
    assert "Page 2 of 2" in "\n".join(item.text for item in furniture)
    assert_nothing_lost(extracted, parsed)


# ------------------------------------------------------------ text and Markdown


MARKDOWN_PROTOCOL = """# DNA extraction

Adapted from the teaching lab handout.

## Reagents
- Lysis buffer
- Proteinase K

## Procedure
1. Add 200 µL lysis buffer to the sample.
   Vortex for 15 s.
2. Add 20 µL proteinase K.
   - Mix by inverting
   - Do not vortex
3. **Caution:** incubate at 56 °C for 10 min.

> Note: samples can be stored at -20 °C.

| Step | Action |
|---|---|
| 4 | Centrifuge for 1 min |

## References
1. Sambrook J, Russell DW. Molecular Cloning.
"""


def test_markdown_protocol_with_nested_lists_table_and_sections() -> None:
    extracted = EXTRACTOR.extract(
        "extraction.md",
        MARKDOWN_MIME,
        MARKDOWN_PROTOCOL.encode(),
    )
    parsed = parse_protocol(extracted)

    assert extracted.media_type == MARKDOWN_MIME
    assert [step.number for step in parsed.steps] == [1, 2, 3, 4]
    assert parsed.steps[0].instruction == (
        "Add 200 µL lysis buffer to the sample.\nVortex for 15 s."
    )
    assert parsed.steps[0].flags == ()
    assert parsed.steps[1].instruction.splitlines()[1:] == ["- Mix by inverting", "- Do not vortex"]
    assert parsed.steps[1].safety_notes == ("Do not vortex",)
    assert parsed.steps[2].instruction.startswith("Caution: incubate at 56 °C for 10 min.")
    assert parsed.steps[2].duration_seconds == 600
    assert "PARAGRAPH_ATTACHED" in flag_codes(parsed, 3)  # the blockquote note
    assert parsed.steps[3].instruction == "Centrifuge for 1 min"
    unassigned = {(item.reason, item.section) for item in parsed.unassigned}
    assert (NON_PROCEDURE_SECTION, "Reagents") in unassigned
    assert (NON_PROCEDURE_SECTION, "References") in unassigned
    assert (OUTSIDE_STEPS, "DNA extraction") in unassigned
    assert_nothing_lost(extracted, parsed)


def test_plain_text_bullets_are_used_when_there_are_no_numbered_steps() -> None:
    parsed = parse_text("Method\n• Add 50 mL ethanol to the beaker.\n• Incubate for 10 minutes.")

    assert [step.instruction for step in parsed.steps] == [
        "Add 50 mL ethanol to the beaker.",
        "Incubate for 10 minutes.",
    ]
    assert flag_codes(parsed, 1) == ["BULLET_STEPS"]


def test_procedure_section_excludes_numbered_materials_and_references() -> None:
    text = (
        "Materials\n"
        "1. Ethanol 70% (Sigma)\n"
        "2-Mercaptoethanol, 14.3 M\n"
        "Procedure\n"
        "1. Add 50 mL ethanol to the beaker.\n"
        "2. Stir.\n"
        "References\n"
        "1. Smith J. et al. (2019) Nature 12: 1-5."
    )
    parsed = parse_text(text)

    assert [step.instruction for step in parsed.steps] == [
        "Add 50 mL ethanol to the beaker.",
        "Stir.",
    ]
    assert [item.text for item in parsed.unassigned] == [
        "1. Ethanol 70% (Sigma)\n2-Mercaptoethanol, 14.3 M",
        "1. Smith J. et al. (2019) Nature 12: 1-5.",
    ]
    assert_nothing_lost(text_document(text), parsed)


def test_numbered_section_headings_are_not_mistaken_for_steps() -> None:
    parsed = parse_text("1. Materials\nBuffer A\n2. Procedure\n1. Add buffer.\n2. Mix.")

    assert [step.instruction for step in parsed.steps] == ["Add buffer.", "Mix."]
    assert parsed.steps[0].source.section == "Procedure"


def test_without_any_section_heading_every_numbered_step_is_used() -> None:
    parsed = parse_text("1. Add 5 mL water.\n2. Stir.")

    assert [step.instruction for step in parsed.steps] == ["Add 5 mL water.", "Stir."]
    assert parsed.unassigned == ()


# ------------------------------------------------------------------ text shape


def test_wrapped_lines_and_lettered_substeps_are_kept_in_their_step() -> None:
    text = (
        "1. Add 50 mL ethanol\n"
        "to the beaker and mix gently\n"
        "2. Prepare buffer:\n"
        "a. Add 5 µl Tris\n"
        "b. Add 10 ul EDTA\n"
        "3. Centrifuge at 4,000 x g for 15 min."
    )
    parsed = parse_text(text)

    assert parsed.steps[0].instruction == "Add 50 mL ethanol to the beaker and mix gently"
    assert parsed.steps[0].source.section is None
    assert parsed.steps[1].instruction == "Prepare buffer:\na. Add 5 µl Tris\nb. Add 10 ul EDTA"
    assert [(r.name, r.unit) for r in parsed.steps[1].requirements] == [
        ("tris", "µl"),
        ("edta", "ul"),
    ]
    # g-force is a setting, not a reagent amount
    assert parsed.steps[2].requirements == ()
    assert parsed.steps[2].duration_seconds == 900
    assert_nothing_lost(text_document(text), parsed)


def test_standalone_warnings_become_safety_notes_and_are_flagged_for_placement() -> None:
    text = (
        "Procedure\n"
        "WARNING: work in a fume hood.\n"
        "1. Add 5 mL HCl.\n"
        "CAUTION: HCl is corrosive;\n"
        "wear gloves.\n"
        "2. Neutralise with 5 mL NaOH."
    )
    parsed = parse_text(text)

    assert parsed.steps[0].instruction == "Add 5 mL HCl."
    assert parsed.steps[0].safety_notes == (
        "WARNING: work in a fume hood.",
        "CAUTION: HCl is corrosive; wear gloves.",
    )
    assert flag_codes(parsed, 1).count("WARNING_PLACEMENT") == 2
    assert parsed.steps[1].flags == ()
    assert_nothing_lost(text_document(text), parsed)


def test_a_warning_with_no_step_after_it_is_preserved_not_dropped() -> None:
    parsed = parse_text("Part A\n1. Stir.\nPart B\nCaution: surfaces stay hot for an hour.")

    assert parsed.steps[0].safety_notes == ()
    assert [(item.reason, item.text) for item in parsed.unassigned] == [
        (WARNING_WITHOUT_STEP, "Caution: surfaces stay hot for an hour.")
    ]


def test_separate_paragraph_after_a_step_is_attached_and_flagged() -> None:
    parsed = parse_text("1. Add 5 mL water.\n2. Stir.\n\nStore the product at 4 °C.")

    assert parsed.steps[1].instruction == "Stir.\nStore the product at 4 °C."
    assert "PARAGRAPH_ATTACHED" in flag_codes(parsed, 2)


def test_restarted_numbering_is_renumbered_and_flagged() -> None:
    parsed = parse_text("Part A\n1. Add 5 mL water.\n2. Mix.\nPart B\n1. Add 2 mL acid.\n2. Stir.")

    assert [step.id for step in parsed.steps] == ["step-1", "step-2", "step-3", "step-4"]
    assert [step.source.section for step in parsed.steps] == [
        "Part A",
        "Part A",
        "Part B",
        "Part B",
    ]
    assert flag_codes(parsed, 3) == ["NUMBERING_RESTARTED"]
    assert parsed.steps[2].source.excerpt == "1. Add 2 mL acid."
    validate_protocol_steps(parsed.steps)


def test_gaps_in_source_numbering_are_flagged_not_hidden() -> None:
    parsed = parse_text("3. Add 5 mL water.\n5. Stir.")

    assert [step.number for step in parsed.steps] == [1, 2]
    assert flag_codes(parsed, 1) == ["NUMBERING_IRREGULAR"]
    assert flag_codes(parsed, 2) == ["NUMBERING_IRREGULAR"]


def test_prose_without_steps_is_rejected_rather_than_split_by_guesswork() -> None:
    with pytest.raises(DocumentError) as error:
        parse_text("Mix 2 g NaCl into 100 mL water, then stir for 5 min.")

    assert error.value.code == "PROTOCOL_STEPS_REQUIRED"


# ---------------------------------------------------------- ambiguous values


@pytest.mark.parametrize(
    ("instruction", "code"),
    [
        ("Add 1-2 mL PBS.", "QUANTITY_RANGE"),
        ("Add 1 to 2 mL PBS.", "QUANTITY_RANGE"),
        ("Add about 5 mL water.", "QUANTITY_APPROXIMATE"),
        ("Add ~5 mL water.", "QUANTITY_APPROXIMATE"),
        ("Add 1,5 mL water.", "QUANTITY_AMBIGUOUS_NUMBER"),
    ],
)
def test_ambiguous_amounts_keep_the_reagent_but_set_no_quantity(
    instruction: str,
    code: str,
) -> None:
    parsed = parse_text(f"1. {instruction}")

    requirement = parsed.steps[0].requirements[0]
    assert requirement.quantity is None
    assert requirement.unit is None
    assert code in flag_codes(parsed, 1)


@pytest.mark.parametrize(
    ("instruction", "code"),
    [
        ("Add 3 drops of indicator.", "QUANTITY_UNUSUAL_UNIT"),
        ("Add an equal volume of phenol.", "QUANTITY_UNSPECIFIED"),
        ("Bring to a final volume of 50 mL.", "QUANTITY_UNSPECIFIED"),
        ("Resuspend in 1 mL.", "QUANTITY_WITHOUT_REAGENT"),
    ],
)
def test_unmeasurable_amounts_are_flagged(instruction: str, code: str) -> None:
    parsed = parse_text(f"1. {instruction}")

    assert code in flag_codes(parsed, 1)


@pytest.mark.parametrize(
    ("instruction", "code"),
    [
        ("Heat for 5-10 minutes.", "TIMING_RANGE"),
        ("Incubate for about 30 min.", "TIMING_APPROXIMATE"),
        ("Incubate for at least 1 h.", "TIMING_APPROXIMATE"),
        ("Leave overnight.", "TIMING_UNCLEAR"),
        ("Spin for 5 min or until the pellet forms.", "TIMING_UNCLEAR"),
        ("Centrifuge for 5 min, then incubate for 10 min.", "TIMING_MULTIPLE"),
        ("Stain for 0.5 s.", "TIMING_OUT_OF_RANGE"),
    ],
)
def test_unclear_timings_set_no_timer(instruction: str, code: str) -> None:
    parsed = parse_text(f"1. {instruction}")

    assert parsed.steps[0].duration_seconds is None
    assert code in flag_codes(parsed, 1)


@pytest.mark.parametrize(
    ("instruction", "seconds"),
    [
        ("Incubate for 1.5 hours.", 5400),
        ("Incubate 1 h 30 min.", 5400),
        ("Incubate 30 min on ice.", 1800),
        ("Spin for 45 s.", 45),
    ],
)
def test_one_clear_duration_sets_the_timer_without_flags(instruction: str, seconds: int) -> None:
    parsed = parse_text(f"1. {instruction}")

    assert parsed.steps[0].duration_seconds == seconds
    assert parsed.steps[0].flags == ()


def test_repetitions_and_conditions_are_flagged() -> None:
    parsed = parse_text(
        "1. Wash 3 x 5 min in PBS.\n"
        "2. Repeat step 1 twice.\n"
        "3. If the solution is cloudy, filter it."
    )

    assert parsed.steps[0].duration_seconds == 300
    assert "REPETITION" in flag_codes(parsed, 1)
    assert "REPETITION" in flag_codes(parsed, 2)
    assert "CONDITIONAL" in flag_codes(parsed, 3)


def test_concentrations_and_containers_are_not_reagent_amounts() -> None:
    parsed = parse_text("1. Add 5 mL of 0.5 M NaCl into a 15 mL tube.")

    assert [(r.name, r.quantity, r.unit) for r in parsed.steps[0].requirements] == [
        ("0.5 m nacl", Decimal("5"), "mL")
    ]
    assert parsed.steps[0].flags == ()


def test_the_development_sample_imports_without_review_flags() -> None:
    parsed = parse_text(
        "Preparation\n"
        "1. Add 10 mL ethanol to the demonstration vessel.\n"
        "2. Incubate the synthetic sample for 5 minutes.\n"
        "3. Warning: do not proceed until the demonstration cap is sealed."
    )

    assert all(step.flags == () for step in parsed.steps)
    assert parsed.steps[2].safety_notes == (
        "Warning: do not proceed until the demonstration cap is sealed.",
    )


def test_ocr_derived_steps_are_flagged_for_word_by_word_review() -> None:
    document = ExtractedDocument(
        media_type=PDF_MIME,
        sha256="0" * 64,
        pages=(ExtractedPage(number=1, text="1. Add 5 mL water.", ocr_derived=True),),
    )

    parsed = parse_protocol(document)

    assert parsed.steps[0].source.ocr_derived is True
    assert flag_codes(parsed, 1) == ["OCR_DERIVED"]


def test_overlong_step_text_is_preserved_in_the_unassigned_list() -> None:
    long_paragraph = " ".join(["Continue gently mixing the suspension."] * 200)
    parsed = parse_text(f"1. Start.\n\n{long_paragraph}\n\nMore detail follows.")

    assert len(parsed.steps[0].instruction) <= 5000
    assert "STEP_TEXT_OVERFLOW" in flag_codes(parsed, 1)
    overflow = [item for item in parsed.unassigned if item.reason == "STEP_TEXT_OVERFLOW"]
    assert "More detail follows." in overflow[0].text
    validate_protocol_steps(parsed.steps)


# ------------------------------------------------------------ text extraction


def test_text_and_markdown_uploads_are_decoded_strictly() -> None:
    utf8 = EXTRACTOR.extract("p.txt", "text/plain; charset=utf-8", "﻿1. Add 5 µL dye".encode())
    utf16 = EXTRACTOR.extract("p.txt", "text/plain", "1. Add water".encode("utf-16"))
    unlabelled = EXTRACTOR.extract("p.md", "application/octet-stream", b"1. Stir")
    paged = EXTRACTOR.extract("p.txt", "text/plain", b"1. Stir\n\x0c2. Wait")

    assert utf8.text == "1. Add 5 µL dye"
    assert utf8.media_type == TEXT_MIME
    assert utf16.text == "1. Add water"
    assert unlabelled.media_type == MARKDOWN_MIME
    assert [page.number for page in paged.pages] == [1, 2]
    assert [step.source.page for step in parse_protocol(paged).steps] == [1, 2]


@pytest.mark.parametrize(
    ("filename", "content_type", "data", "code"),
    [
        ("p.txt", "text/plain", b"caf\xe9", "DOCUMENT_TEXT_ENCODING"),
        ("p.txt", "text/plain", b"1. Add\x00water", "DOCUMENT_SIGNATURE_MISMATCH"),
        ("p.md", "text/markdown", b"%PDF-1.7 renamed", "DOCUMENT_SIGNATURE_MISMATCH"),
        ("p.txt", "application/pdf", b"1. Stir", "DOCUMENT_SIGNATURE_MISMATCH"),
        ("p.txt", "text/plain", b"   \n  ", "DOCUMENT_TEXT_REQUIRED"),
    ],
)
def test_text_uploads_that_are_not_text_are_rejected(
    filename: str,
    content_type: str,
    data: bytes,
    code: str,
) -> None:
    with pytest.raises(DocumentError) as error:
        EXTRACTOR.extract(filename, content_type, data)

    assert error.value.code == code


def test_every_imported_draft_passes_canonical_validation() -> None:
    samples = [
        parse_protocol(EXTRACTOR.extract("m.docx", DOCX_MIME, realistic_docx())),
        parse_protocol(EXTRACTOR.extract("s.pdf", PDF_MIME, realistic_pdf())),
        parse_protocol(EXTRACTOR.extract("e.md", MARKDOWN_MIME, MARKDOWN_PROTOCOL.encode())),
    ]

    for parsed in samples:
        assert validate_protocol_steps(parsed.steps) == parsed.steps
        requirement_ids = [r.requirement_id for s in parsed.steps for r in s.requirements]
        assert len(requirement_ids) == len(set(requirement_ids))
