"""Bounded PDF page rendering for document OCR, separate from label comparison."""

from __future__ import annotations

import fitz  # type: ignore[import-untyped]

from labjarvis.infrastructure.model_errors import ModelInputError, ModelUnavailableError
from labjarvis.ports.models import (
    AdapterReadiness,
    DocumentOcrPort,
    LabelRecognitionPort,
    OcrPage,
)


class DocumentOcrError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class UnavailableDocumentOcr:
    adapter_name = "document-ocr-unavailable"
    model_name = "none"

    def extract_pages(self, document: bytes) -> tuple[OcrPage, ...]:
        del document
        raise DocumentOcrError(
            "DOCUMENT_OCR_UNAVAILABLE",
            "document OCR is unavailable in this execution profile",
        )

    def readiness(self) -> AdapterReadiness:
        return AdapterReadiness(
            ready=False,
            adapter=self.adapter_name,
            model_name=self.model_name,
            detail="document OCR is unavailable in this execution profile",
            configured=False,
        )


class RenderedDocumentOcr:
    """Render bounded PDF pages and transcribe each through an OCR recognizer."""

    adapter_name = "rendered-document-ocr"

    def __init__(
        self,
        *,
        recognizer: LabelRecognitionPort,
        dpi: int,
        max_page_pixels: int,
        max_total_pixels: int,
    ) -> None:
        self._recognizer = recognizer
        self._dpi = dpi
        self._max_page_pixels = max_page_pixels
        self._max_total_pixels = max_total_pixels
        self.model_name = recognizer.model_name

    def extract_pages(self, document: bytes) -> tuple[OcrPage, ...]:
        try:
            pdf = fitz.open(stream=document, filetype="pdf")
        except (fitz.FileDataError, RuntimeError) as exc:
            raise DocumentOcrError("DOCUMENT_OCR_FAILED", "PDF rendering failed") from exc
        total_pixels = 0
        pages: list[OcrPage] = []
        scale = self._dpi / 72
        try:
            for index, page in enumerate(pdf):
                width = max(1, round(page.rect.width * scale))
                height = max(1, round(page.rect.height * scale))
                page_pixels = width * height
                total_pixels += page_pixels
                if (
                    page_pixels > self._max_page_pixels
                    or total_pixels > self._max_total_pixels
                ):
                    raise DocumentOcrError(
                        "DOCUMENT_OCR_PIXEL_LIMIT",
                        "rendered PDF exceeds the configured OCR pixel limit",
                    )
                pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                try:
                    result = self._recognizer.recognise(
                        pixmap.tobytes("png"),
                        content_type="image/png",
                    )
                except (
                    ModelInputError,
                    ModelUnavailableError,
                    OSError,
                    RuntimeError,
                    ValueError,
                ) as exc:
                    raise DocumentOcrError(
                        "DOCUMENT_OCR_FAILED",
                        f"document OCR failed on page {index + 1}",
                    ) from exc
                pages.append(
                    OcrPage(
                        page=index + 1,
                        text=result.visible_text.strip(),
                        width=pixmap.width,
                        height=pixmap.height,
                        adapter=result.adapter,
                        model_name=result.model_name,
                    )
                )
        finally:
            pdf.close()
        return tuple(pages)

    def readiness(self) -> AdapterReadiness:
        readiness = self._recognizer.readiness()
        return AdapterReadiness(
            ready=readiness.ready,
            adapter=self.adapter_name,
            model_name=self.model_name,
            detail=readiness.detail,
            configured=readiness.configured,
            dependency_installed=readiness.dependency_installed,
            model_cached=readiness.model_cached,
            loaded=readiness.loaded,
            provider_reachable=readiness.provider_reachable,
            inference_tested=readiness.inference_tested,
        )


def require_document_ocr(adapter: DocumentOcrPort | None) -> DocumentOcrPort:
    if adapter is None:
        raise DocumentOcrError(
            "DOCUMENT_OCR_UNAVAILABLE",
            "document OCR is not configured",
        )
    return adapter
