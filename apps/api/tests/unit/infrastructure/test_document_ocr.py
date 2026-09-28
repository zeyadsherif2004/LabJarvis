from io import BytesIO

import fitz
import pytest
from PIL import Image

from labjarvis.infrastructure.document_ocr import DocumentOcrError, RenderedDocumentOcr
from labjarvis.infrastructure.documents import (
    PDF_MIME,
    DocumentError,
    DocumentExtractor,
)
from labjarvis.infrastructure.protocol_import import parse_protocol_steps
from labjarvis.ports.models import AdapterReadiness, LabelResult


def _blank_pdf(page_count: int = 1, *, width: int = 300, height: int = 200) -> bytes:
    document = fitz.open()
    for _ in range(page_count):
        document.new_page(width=width, height=height)
    data = document.tobytes()
    document.close()
    return data


class StubRecognizer:
    adapter_name = "stub-ocr"
    model_name = "stub-model"

    def __init__(self, texts: tuple[str, ...], *, fail_call: int | None = None) -> None:
        self._texts = iter(texts)
        self._fail_call = fail_call
        self.calls = 0
        self.rendered_sizes: list[tuple[int, int]] = []

    def recognise(self, image: bytes, *, content_type: str) -> LabelResult:
        assert content_type == "image/png"
        self.calls += 1
        with Image.open(BytesIO(image)) as rendered:
            self.rendered_sizes.append(rendered.size)
        if self.calls == self._fail_call:
            raise RuntimeError("synthetic page failure")
        return LabelResult(
            visible_text=next(self._texts),
            confidence=0.9,
            adapter=self.adapter_name,
            model_name=self.model_name,
        )

    def readiness(self) -> AdapterReadiness:
        return AdapterReadiness(
            ready=True,
            adapter=self.adapter_name,
            model_name=self.model_name,
            detail="synthetic fixture",
        )


def test_scanned_pdf_requires_explicit_ocr_retry_and_marks_provenance() -> None:
    recognizer = StubRecognizer(("1. Mix sample.",))
    ocr = RenderedDocumentOcr(
        recognizer=recognizer,
        dpi=100,
        max_page_pixels=1_000_000,
        max_total_pixels=1_000_000,
    )
    extractor = DocumentExtractor(
        max_bytes=1_000_000,
        max_pages=10,
        document_ocr=ocr,
        min_pdf_text_characters_per_page=8,
    )
    scanned = _blank_pdf()

    with pytest.raises(DocumentError) as caught:
        extractor.extract("scan.pdf", PDF_MIME, scanned)
    assert caught.value.code == "DOCUMENT_OCR_REQUIRED"
    assert recognizer.calls == 0

    extracted = extractor.extract("scan.pdf", PDF_MIME, scanned, allow_ocr=True)
    assert extracted.pages[0].text == "1. Mix sample."
    assert extracted.pages[0].ocr_derived is True
    assert extracted.pages[0].ocr_adapter == "stub-ocr"
    assert "OCR-derived page 1" in extracted.text
    assert parse_protocol_steps(extracted)[0].source.ocr_derived is True


def test_blank_ocr_page_remains_blank_with_page_provenance() -> None:
    recognizer = StubRecognizer(("", "2. Continue."))
    ocr = RenderedDocumentOcr(
        recognizer=recognizer,
        dpi=72,
        max_page_pixels=100_000,
        max_total_pixels=200_000,
    )

    pages = ocr.extract_pages(_blank_pdf(2, width=100, height=100))

    assert [page.page for page in pages] == [1, 2]
    assert pages[0].text == ""
    assert pages[0].width == 100
    assert pages[0].height == 100


def test_partial_page_failure_rejects_entire_document() -> None:
    recognizer = StubRecognizer(("1. First.", "3. Third."), fail_call=2)
    ocr = RenderedDocumentOcr(
        recognizer=recognizer,
        dpi=72,
        max_page_pixels=100_000,
        max_total_pixels=300_000,
    )

    with pytest.raises(DocumentOcrError) as caught:
        ocr.extract_pages(_blank_pdf(3, width=100, height=100))

    assert caught.value.code == "DOCUMENT_OCR_FAILED"
    assert recognizer.calls == 2


def test_rendered_pixel_limits_apply_before_ocr() -> None:
    recognizer = StubRecognizer(("never",))
    ocr = RenderedDocumentOcr(
        recognizer=recognizer,
        dpi=144,
        max_page_pixels=30_000,
        max_total_pixels=30_000,
    )

    with pytest.raises(DocumentOcrError) as caught:
        ocr.extract_pages(_blank_pdf(width=100, height=100))

    assert caught.value.code == "DOCUMENT_OCR_PIXEL_LIMIT"
    assert recognizer.calls == 0
