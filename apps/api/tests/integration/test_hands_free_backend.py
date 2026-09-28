"""Backend support for the hands-free workflow: multi-frame labels and wake gating."""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import count
from pathlib import Path

import pytest

from labjarvis.application.errors import ApplicationError
from labjarvis.application.service import LabJarvisService
from labjarvis.domain.models import VerificationOutcome
from labjarvis.domain.wake_phrase import addressed_to_assistant
from labjarvis.infrastructure.database import SqlAlchemyUnitOfWork, create_session_factory
from labjarvis.infrastructure.documents import DOCX_MIME, DocumentExtractor
from labjarvis.infrastructure.fake_models import (
    FakeLabelRecognition,
    FakeSpeechToText,
    FakeTextUnderstanding,
)
from labjarvis.infrastructure.inference import DirectInferenceRunner
from labjarvis.infrastructure.media_storage import DiscardRawMediaStore

SAMPLE = (
    Path(__file__).parents[4]
    / "sample_data"
    / "protocols"
    / "development-only-ethanol-protocol.docx"
)


@pytest.fixture
def service() -> LabJarvisService:
    factory = create_session_factory("sqlite+pysqlite://", create_schema=True)
    ids = count(1)
    return LabJarvisService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(factory),
        document_extractor=DocumentExtractor(max_bytes=1_000_000, max_pages=10),
        speech=FakeSpeechToText(),
        text=FakeTextUnderstanding(),
        labels=FakeLabelRecognition(),
        inference_runner=DirectInferenceRunner(),
        clock=lambda: datetime(2026, 9, 26, tzinfo=UTC),
        new_id=lambda: f"id-{next(ids)}",
        command_confidence_threshold=0.75,
        reagent_match_threshold=92,
        reagent_uncertain_threshold=72,
        raw_media_store=DiscardRawMediaStore(),
        retain_transcripts=True,
        wake_phrase="Jarvis",
    )


def active(service: LabJarvisService):  # type: ignore[no-untyped-def]
    imported = service.import_protocol(SAMPLE.name, DOCX_MIME, SAMPLE.read_bytes())
    approved = service.approve_protocol(imported.version.id, expected_revision=0)
    session = service.create_session(approved.id)
    return service.start_session(session.id, expected_version=0)


def reagent_step_session(service: LabJarvisService):  # type: ignore[no-untyped-def]
    session = active(service)
    version = service.get_protocol_version(session.protocol_version_id)
    while True:
        step = next(s for s in version.steps if s.id == session.active_step_id)
        if step.requirements:
            return session
        service.invoke_tool(
            session.id, "complete_step", {}, source=__import__(
                "labjarvis.domain.models", fromlist=["EntrySource"]
            ).EntrySource.TYPED_INPUT, model_proposed=False, correlation_id="c",
            expected_version=session.version,
        )  # fmt: skip
        session = service.get_session(session.id)


@pytest.mark.parametrize(
    ("frames", "outcome", "frame_count"),
    [
        ([b"FAKE:ethanol", b"FAKE:ETHANOL", b"FAKE:ethanol"], VerificationOutcome.MATCH, "3"),
        ([b"FAKE:methanol"] * 3, VerificationOutcome.MISMATCH, "3"),
        ([b"FAKE:ethanol", b"FAKE:methanol", b"FAKE:ethanol"], VerificationOutcome.UNCERTAIN, "3"),
    ],
)
def test_multi_frame_verification_records_the_consensus_and_every_frame(
    service: LabJarvisService,
    frames: list[bytes],
    outcome: VerificationOutcome,
    frame_count: str,
) -> None:
    session = reagent_step_session(service)
    result = service.verify_reagent(
        session.id,
        frames[0],
        content_type="image/jpeg",
        correlation_id="corr",
        expected_version=session.version,
        idempotency_key="frames-1",
        additional_frames=tuple(frames[1:]),
    )

    assert result.outcome is outcome
    review = service.get_session_review(session.id)
    vision = [i for i in review.inferences if i.modality == "vision"]
    assert len(vision) == 3
    assert {i.output["frame_count"] for i in vision} == {frame_count}
    proposal = next(e for e in review.entries if e.entry_type == "reagent_verification_proposal")
    assert proposal.payload["frame_count"] == frame_count
    assert proposal.payload["consensus_algorithm"] == "unanimous-v1"


def test_too_many_frames_are_refused(service: LabJarvisService) -> None:
    session = reagent_step_session(service)
    with pytest.raises(ApplicationError, match="at most"):
        service.verify_reagent(
            session.id,
            b"FAKE:ethanol",
            content_type="image/jpeg",
            correlation_id="corr",
            expected_version=session.version,
            idempotency_key="frames-2",
            additional_frames=(b"FAKE:ethanol",) * 5,
        )


def test_speech_without_the_wake_phrase_is_discarded_without_trace(
    service: LabJarvisService,
) -> None:
    session = active(service)
    before = service.get_session_review(session.id)

    result = service.handle_audio_command(
        session.id,
        b"FAKE:record the temperature as 37 degrees Celsius",
        content_type="audio/webm",
        idempotency_key="vad-1",
        expected_version=session.version,
        correlation_id="corr-vad-1",
        require_wake_phrase=True,
    )

    after = service.get_session_review(session.id)
    assert result.parsed_command.intent == "WAKE_PHRASE_ABSENT"
    assert result.pending_action is None
    assert result.transcript == ""
    assert after.entries == before.entries
    assert after.inferences == before.inferences


def test_speech_with_the_wake_phrase_is_interpreted(service: LabJarvisService) -> None:
    session = active(service)
    result = service.handle_audio_command(
        session.id,
        b"FAKE:Jarvis, record the temperature as 37 degrees Celsius",
        content_type="audio/webm",
        idempotency_key="vad-2",
        expected_version=session.version,
        correlation_id="corr-vad-2",
        require_wake_phrase=True,
    )

    assert result.parsed_command.intent == "RECORD_MEASUREMENT"
    assert result.pending_action is not None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Jarvis, pause", True),
        ("hey Jarvis what is the next step", True),
        ("jarvis.", False),
        ("I told Jarvis to pause", False),
        ("pause the session", False),
        ("", False),
    ],
)
def test_wake_phrase_detection(text: str, expected: bool) -> None:
    assert addressed_to_assistant(text, "Jarvis") is expected


def test_a_spoken_reagent_request_returns_camera_guidance_not_a_proposal(
    service: LabJarvisService,
) -> None:
    """Regression: at 1399c89 this raised KeyError('VERIFY_REAGENT') (HTTP 500)."""

    session = active(service)
    result = service.handle_text_command(
        session.id,
        "check reagent",
        idempotency_key="verify-1",
        expected_version=session.version,
        correlation_id="corr-verify-1",
    )

    assert result.parsed_command.intent == "VERIFY_REAGENT"
    assert result.pending_action is None
    assert "camera" in result.response_text
