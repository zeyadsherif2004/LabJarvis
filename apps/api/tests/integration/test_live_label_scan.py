"""Live "scan until stable" label checking: probes record nothing, stability
authorises nothing, and the mode is refused when OCR would be hosted."""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import count
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from labjarvis.application.errors import ApplicationError
from labjarvis.application.service import LabJarvisService
from labjarvis.domain.live_scan import (
    RULE_VERSION,
    ProbeReading,
    ScanRule,
    ScanStatus,
    evaluate_scan,
)
from labjarvis.domain.models import EntrySource, VerificationDecision, VerificationOutcome
from labjarvis.infrastructure.database import SqlAlchemyUnitOfWork, create_session_factory
from labjarvis.infrastructure.documents import DOCX_MIME, DocumentExtractor
from labjarvis.infrastructure.fake_models import (
    FakeLabelRecognition,
    FakeSpeechToText,
    FakeTextUnderstanding,
)
from labjarvis.infrastructure.inference import DirectInferenceRunner
from labjarvis.infrastructure.media_storage import DiscardRawMediaStore
from labjarvis.settings import ModelMode, Settings

SAMPLE = (
    Path(__file__).parents[4]
    / "sample_data"
    / "protocols"
    / "development-only-ethanol-protocol.docx"
)


def build(*, live: bool = True) -> LabJarvisService:
    factory = create_session_factory("sqlite+pysqlite://", create_schema=True)
    ids = count(1)
    return LabJarvisService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(factory),
        document_extractor=DocumentExtractor(max_bytes=1_000_000, max_pages=10),
        speech=FakeSpeechToText(),
        text=FakeTextUnderstanding(),
        labels=FakeLabelRecognition(),
        inference_runner=DirectInferenceRunner(),
        clock=lambda: datetime(2026, 9, 27, tzinfo=UTC),
        new_id=lambda: f"id-{next(ids)}",
        command_confidence_threshold=0.75,
        reagent_match_threshold=92,
        reagent_uncertain_threshold=72,
        raw_media_store=DiscardRawMediaStore(),
        retain_transcripts=True,
        live_label_scan=live,
    )


def reagent_session(service: LabJarvisService):  # type: ignore[no-untyped-def]
    imported = service.import_protocol(SAMPLE.name, DOCX_MIME, SAMPLE.read_bytes())
    approved = service.approve_protocol(imported.version.id, expected_revision=0)
    session = service.start_session(service.create_session(approved.id).id, expected_version=0)
    version = service.get_protocol_version(session.protocol_version_id)
    while not next(s for s in version.steps if s.id == session.active_step_id).requirements:
        service.invoke_tool(
            session.id, "complete_step", {}, source=EntrySource.TYPED_INPUT,
            model_proposed=False, correlation_id="c", expected_version=session.version,
        )  # fmt: skip
        session = service.get_session(session.id)
    return session


def probe(service: LabJarvisService, session_id: str, label: str):  # type: ignore[no-untyped-def]
    return service.probe_reagent_label(
        session_id, f"FAKE:{label}".encode(), content_type="image/jpeg"
    )


def test_a_probe_reads_and_compares_but_records_nothing() -> None:
    service = build()
    session = reagent_session(service)
    before = service.get_session_review(session.id)

    readings = [probe(service, session.id, label) for label in ("ethanol", "methanol", "ethano1")]

    after = service.get_session_review(session.id)
    assert [r.outcome for r in readings] == ["MATCH", "MISMATCH", "UNCERTAIN"]
    assert readings[0].observed_normalised == "ethanol"
    # No inference row, no notebook entry, no pending reading: a probe is not evidence.
    assert len(after.inferences) == len(before.inferences)
    assert len(after.entries) == len(before.entries)
    assert service.get_session_snapshot(session.id).pending_verification is None


def test_probes_are_refused_unless_live_scan_is_enabled() -> None:
    service = build(live=False)
    session = reagent_session(service)
    with pytest.raises(ApplicationError) as caught:
        probe(service, session.id, "ethanol")
    assert caught.value.code == "LIVE_LABEL_SCAN_UNAVAILABLE"


def test_a_stable_scan_only_creates_a_pending_reading_with_its_provenance() -> None:
    service = build()
    session = reagent_session(service)
    rule = ScanRule(required_consecutive=3, interval_ms=400, timeout_ms=12_000)
    readings: list[ProbeReading] = []
    frames: list[bytes] = []
    for label in ("ethanol", "methanol", "ethanol", "ethanol"):
        result = probe(service, session.id, label)
        readings.append(ProbeReading(result.outcome, result.observed_normalised))
        frames.append(f"FAKE:{label}".encode())
        state = evaluate_scan(readings, elapsed_ms=400 * len(readings), rule=rule)
        if state.status is ScanStatus.STABLE:
            break
    assert state.status is ScanStatus.STABLE
    assert state.frame_indexes == (0, 2, 3)
    stable_frames = [frames[index] for index in state.frame_indexes]

    verification = service.verify_reagent(
        session.id,
        stable_frames[0],
        content_type="image/jpeg",
        correlation_id="c",
        expected_version=session.version,
        idempotency_key="scan-1",
        additional_frames=tuple(stable_frames[1:]),
        capture_metadata={
            "mode": "live_scan",
            "scan_rule": RULE_VERSION,
            "scan_frames_examined": str(state.frames_examined),
        },
    )

    # Stability authorised nothing: the reading waits for a person.
    assert verification.outcome is VerificationOutcome.MATCH
    assert verification.decision is VerificationDecision.PENDING
    proposal = next(
        e
        for e in service.get_session_review(session.id).entries
        if e.entry_type == "reagent_verification_proposal"
    )
    assert proposal.payload["capture_mode"] == "live_scan"
    assert proposal.payload["capture_scan_rule"] == RULE_VERSION
    assert proposal.payload["capture_scan_frames_examined"] == "4"
    assert proposal.payload["frame_count"] == "3"


def test_live_scan_is_refused_at_startup_when_ocr_is_hosted(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="local OCR"):
        Settings(
            data_dir=tmp_path,
            database_url=f"sqlite+pysqlite:///{tmp_path / 'x.db'}",
            model_mode=ModelMode.LOCAL,
            vision_provider="openai",
            allow_hosted_models=True,
            hosted_api_key=SecretStr("unit-test-placeholder-not-a-real-key"),
            hosted_vision_model_name="some-vision-model",
            live_label_scan_enabled=True,
        )


def test_live_scan_is_allowed_with_a_loopback_vision_server(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite+pysqlite:///{tmp_path / 'x.db'}",
        model_mode=ModelMode.LOCAL,
        vision_provider="openai_compatible",
        allow_hosted_models=True,
        hosted_vision_api_key=SecretStr("unit-test-placeholder-not-a-real-key"),
        hosted_vision_base_url="http://127.0.0.1:8080/v1",
        hosted_vision_model_name="local-vlm",
        hosted_vision_image_input=True,
        live_label_scan_enabled=True,
    )
    assert settings.stage_locality("vision") == "local"
