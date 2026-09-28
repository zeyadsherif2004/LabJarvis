from decimal import Decimal
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import fitz
import pytest
from docx import Document

from labjarvis.infrastructure.documents import (
    DOCX_MIME,
    PDF_MIME,
    DocumentError,
    DocumentExtractor,
    ExtractedDocument,
    ExtractedPage,
)
from labjarvis.infrastructure.protocol_import import parse_protocol_steps


def make_docx(*paragraphs: str) -> bytes:
    document = Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    stream = BytesIO()
    document.save(stream)
    return stream.getvalue()


def make_pdf(text: str | None) -> bytes:
    document = fitz.open()
    page = document.new_page()
    if text is not None:
        page.insert_text((72, 72), text)
    data = document.tobytes()
    document.close()
    return data


def test_numbered_steps_preserve_source_and_extract_requirements() -> None:
    document = ExtractedDocument(
        media_type=DOCX_MIME,
        sha256="abc",
        pages=(
            ExtractedPage(
                number=1,
                text=(
                    "Preparation\n"
                    "1. Add 10 mL ethanol.\n"
                    "2. Incubate the sample for 5 minutes.\n"
                    "3. Warning: do not open the tube."
                ),
            ),
        ),
    )

    steps = parse_protocol_steps(document)

    assert [step.number for step in steps] == [1, 2, 3]
    requirement = steps[0].requirements[0]
    assert requirement.name == "ethanol"
    assert requirement.quantity == Decimal("10")
    assert requirement.unit == "mL"
    assert steps[0].source.page == 1
    assert steps[0].source.section == "Preparation"
    assert steps[1].duration_seconds == 300
    assert steps[2].safety_notes == ("Warning: do not open the tube.",)


def test_docx_extraction_keeps_heading_context() -> None:
    extractor = DocumentExtractor(max_bytes=1_000_000, max_pages=10)

    extracted = extractor.extract(
        "safe-protocol.docx",
        DOCX_MIME,
        make_docx("Preparation", "1. Add 10 mL ethanol."),
    )

    assert extracted.media_type == DOCX_MIME
    assert "Add 10 mL ethanol" in extracted.pages[0].text
    assert len(extracted.sha256) == 64


def test_pdf_extraction_preserves_page_number() -> None:
    extractor = DocumentExtractor(max_bytes=1_000_000, max_pages=10)

    extracted = extractor.extract("safe.pdf", PDF_MIME, make_pdf("1. Mix sample."))

    assert extracted.pages[0].number == 1
    assert "Mix sample" in extracted.pages[0].text


def test_extension_signature_mismatch_is_rejected() -> None:
    extractor = DocumentExtractor(max_bytes=1_000_000, max_pages=10)

    with pytest.raises(DocumentError, match="signature") as error:
        extractor.extract("protocol.pdf", PDF_MIME, b"not a pdf")

    assert error.value.code == "DOCUMENT_SIGNATURE_MISMATCH"


def test_oversized_document_is_rejected_before_parsing() -> None:
    extractor = DocumentExtractor(max_bytes=4, max_pages=10)

    with pytest.raises(DocumentError, match="size limit") as error:
        extractor.extract("protocol.pdf", PDF_MIME, b"%PDF-too-large")

    assert error.value.code == "DOCUMENT_TOO_LARGE"


def test_image_only_pdf_requires_explicit_ocr_without_inventing_steps() -> None:
    extractor = DocumentExtractor(max_bytes=1_000_000, max_pages=10)

    with pytest.raises(DocumentError, match="document OCR") as error:
        extractor.extract("image-only.pdf", PDF_MIME, make_pdf(None))

    assert error.value.code == "DOCUMENT_OCR_REQUIRED"


def test_document_with_no_numbered_steps_is_rejected() -> None:
    extracted = ExtractedDocument(
        media_type=DOCX_MIME,
        sha256="abc",
        pages=(ExtractedPage(number=1, text="A paragraph without reviewed steps."),),
    )

    with pytest.raises(DocumentError, match="numbered or bulleted steps") as error:
        parse_protocol_steps(extracted)

    assert error.value.code == "PROTOCOL_STEPS_REQUIRED"


def test_generated_development_sample_is_parseable_and_clearly_labelled() -> None:
    sample_path = (
        Path(__file__).parents[5]
        / "sample_data"
        / "protocols"
        / "development-only-ethanol-protocol.docx"
    )
    extractor = DocumentExtractor(max_bytes=1_000_000, max_pages=10)

    extracted = extractor.extract(sample_path.name, DOCX_MIME, sample_path.read_bytes())
    steps = parse_protocol_steps(extracted)

    assert "development-only" in extracted.text.casefold()
    assert len(steps) == 3
    assert steps[0].requirements[0].name == "ethanol"


def test_docx_expansion_limit_is_checked_before_xml_parsing() -> None:
    stream = BytesIO()
    with ZipFile(stream, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("word/document.xml", "<document />")
        archive.writestr("word/media/padding.bin", b"A" * 2048)
    extractor = DocumentExtractor(
        max_bytes=10_000,
        max_pages=10,
        max_docx_uncompressed_bytes=1024,
        max_docx_entries=100,
        max_extracted_characters=10_000,
    )

    with pytest.raises(DocumentError) as error:
        extractor.extract("protocol.docx", DOCX_MIME, stream.getvalue())

    assert error.value.code == "DOCUMENT_EXPANDED_TOO_LARGE"


def test_docx_entry_limit_is_checked_before_xml_parsing() -> None:
    stream = BytesIO()
    with ZipFile(stream, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("word/document.xml", "<document />")
        archive.writestr("word/extra.xml", "<extra />")
    extractor = DocumentExtractor(
        max_bytes=10_000,
        max_pages=10,
        max_docx_entries=2,
    )

    with pytest.raises(DocumentError) as error:
        extractor.extract("protocol.docx", DOCX_MIME, stream.getvalue())

    assert error.value.code == "DOCUMENT_TOO_MANY_ENTRIES"


def test_extracted_character_limit_applies_to_docx() -> None:
    extractor = DocumentExtractor(
        max_bytes=1_000_000,
        max_pages=10,
        max_extracted_characters=1000,
    )

    with pytest.raises(DocumentError) as error:
        extractor.extract("protocol.docx", DOCX_MIME, make_docx("A" * 1100))

    assert error.value.code == "DOCUMENT_TEXT_TOO_LARGE"
