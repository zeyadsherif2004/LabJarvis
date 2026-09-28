"""Opt-in smoke tests that execute downloaded models and never run in CI."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from labjarvis.infrastructure.model_factory import ModelAdapters, build_model_adapters
from labjarvis.ports.models import CommandContext
from labjarvis.settings import ModelMode, Settings

pytestmark = [
    pytest.mark.model,
    pytest.mark.skipif(
        os.getenv("LABJARVIS_RUN_MODEL_TESTS") != "1",
        reason="set LABJARVIS_RUN_MODEL_TESTS=1 after downloading local models",
    ),
]


def configured_adapters() -> ModelAdapters:
    return build_model_adapters(Settings(model_mode=ModelMode.LOCAL))


def required_fixture(variable: str) -> Path:
    value = os.getenv(variable)
    if value is None:
        pytest.fail(f"{variable} must point to a consented, non-sensitive smoke fixture")
    path = Path(value)
    if not path.is_file():
        pytest.fail(f"{variable} does not identify a file")
    return path


def test_real_speech_model_transcribes_supplied_audio() -> None:
    audio = required_fixture("LABJARVIS_SMOKE_AUDIO")
    adapters = configured_adapters()
    try:
        result = adapters.speech.transcribe(audio.read_bytes(), content_type="audio/wav")
    finally:
        adapters.close()
    assert result.transcript.strip()
    assert result.adapter == "faster-whisper"


def test_real_text_model_returns_an_allowlisted_schema() -> None:
    adapters = configured_adapters()
    try:
        result = adapters.text.interpret(
            "Record the temperature as 37 degrees Celsius",
            CommandContext(
                session_id="smoke-session",
                session_state="ACTIVE",
                active_step_id="smoke-step",
                active_instruction="Record the incubation temperature.",
            ),
        )
    finally:
        adapters.close()
    assert result.command.intent in {
        "GET_CURRENT_STEP",
        "GET_NEXT_STEP",
        "REPEAT_INSTRUCTION",
        "RECORD_MEASUREMENT",
        "RECORD_OBSERVATION",
        "SET_TIMER",
        "VERIFY_REAGENT",
        "CORRECT_PREVIOUS_ENTRY",
        "PAUSE_SESSION",
        "RESUME_SESSION",
        "COMPLETE_STEP",
        "END_SESSION",
        "HELP",
        "UNSUPPORTED",
    }
    assert 0 <= result.confidence <= 1


def test_real_vision_model_reads_supplied_label_image() -> None:
    image = required_fixture("LABJARVIS_SMOKE_IMAGE")
    adapters = configured_adapters()
    try:
        result = adapters.labels.recognise(image.read_bytes(), content_type="image/jpeg")
    finally:
        adapters.close()
    assert result.visible_text.strip()
    assert result.adapter == "easyocr"


def test_real_document_ocr_reads_supplied_scanned_pdf() -> None:
    document = required_fixture("LABJARVIS_SMOKE_SCANNED_PDF")
    adapters = configured_adapters()
    try:
        pages = adapters.documents.extract_pages(document.read_bytes())
    finally:
        adapters.close()
    assert pages
    assert any(page.text.strip() for page in pages)
    assert all(page.adapter == "easyocr" for page in pages)
