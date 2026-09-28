import json
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from labjarvis.application.service import ApplicationError, LabJarvisService
from labjarvis.domain.models import (
    ProtocolVersionStatus,
    SessionState,
    VerificationDecision,
    VerificationOutcome,
)
from labjarvis.infrastructure.database import SqlAlchemyUnitOfWork, create_session_factory
from labjarvis.infrastructure.documents import DOCX_MIME, DocumentExtractor
from labjarvis.infrastructure.fake_models import (
    FakeLabelRecognition,
    FakeSpeechToText,
    FakeTextUnderstanding,
    ModelInputError,
)
from labjarvis.infrastructure.inference import DirectInferenceRunner
from labjarvis.infrastructure.media_storage import DiscardRawMediaStore

NOW = datetime(2026, 7, 16, 13, 0, tzinfo=UTC)


def id_sequence() -> Iterator[str]:
    for number in range(1, 100):
        yield f"generated-{number}"


@pytest.fixture
def services(tmp_path) -> LabJarvisService:
    factory = create_session_factory(
        f"sqlite+pysqlite:///{tmp_path / 'workflow.db'}",
        create_schema=True,
    )
    identifiers = id_sequence()
    return LabJarvisService(
        uow_factory=lambda: SqlAlchemyUnitOfWork(factory),
        document_extractor=DocumentExtractor(max_bytes=1_000_000, max_pages=10),
        speech=FakeSpeechToText(),
        text=FakeTextUnderstanding(),
        labels=FakeLabelRecognition(),
        inference_runner=DirectInferenceRunner(),
        clock=lambda: NOW,
        new_id=lambda: next(identifiers),
        command_confidence_threshold=0.75,
        reagent_match_threshold=92,
        reagent_uncertain_threshold=72,
        raw_media_store=DiscardRawMediaStore(),
        retain_transcripts=True,
    )


@pytest.fixture
def sample_protocol_bytes() -> bytes:
    path = (
        Path(__file__).parents[4]
        / "sample_data"
        / "protocols"
        / "development-only-ethanol-protocol.docx"
    )
    return path.read_bytes()


def start_session(
    services: LabJarvisService,
    sample_protocol_bytes: bytes,
):
    imported = services.import_protocol(
        "development-only-ethanol-protocol.docx",
        DOCX_MIME,
        sample_protocol_bytes,
    )
    assert imported.version.status is ProtocolVersionStatus.DRAFT
    approved = services.approve_protocol(imported.version.id, expected_revision=0)
    session = services.create_session(approved.id)
    return services.start_session(session.id, expected_version=0)


def test_principal_fake_workflow(services, sample_protocol_bytes) -> None:
    active = start_session(services, sample_protocol_bytes)
    assert active.state is SessionState.ACTIVE

    proposal = services.handle_text_command(
        active.id,
        "Jarvis, record the temperature as 37 degrees Celsius",
        idempotency_key="command-1",
        expected_version=active.version,
        correlation_id="correlation-command-1",
    )
    assert proposal.pending_action is not None
    entries_before = services.list_notebook(active.id)
    assert not any(entry.entry_type == "measurement" for entry in entries_before)

    replayed = services.handle_text_command(
        active.id,
        "Jarvis, record the temperature as 37 degrees Celsius",
        idempotency_key="command-1",
        expected_version=active.version,
        correlation_id="correlation-command-1",
    )
    assert replayed.pending_action == proposal.pending_action
    assert replayed.replayed is True
    assert services.list_notebook(active.id) == entries_before

    confirmed = services.confirm_pending_action(
        proposal.pending_action.id,
        accepted=True,
        correlation_id="correlation-confirm-1",
    )
    assert confirmed.entry is not None
    assert confirmed.entry.entry_type == "measurement"
    assert confirmed.entry.payload["value"] == "37"

    match = services.verify_reagent(
        active.id,
        b"FAKE:ethanol",
        content_type="image/jpeg",
        expected_version=services.get_session(active.id).version,
        idempotency_key="label-1",
        correlation_id="correlation-label-1",
    )
    services.confirm_reagent_verification(
        match.id,
        accepted=False,
        corrected_observed_label=None,
        expected_version=services.get_session(active.id).version,
        idempotency_key="label-1-reject",
        correlation_id="correlation-label-1-reject",
    )
    wrong = services.verify_reagent(
        active.id,
        b"FAKE:methanol",
        content_type="image/jpeg",
        expected_version=services.get_session(active.id).version,
        idempotency_key="label-2",
        correlation_id="correlation-label-2",
    )
    assert match.outcome is VerificationOutcome.MATCH
    assert wrong.outcome is VerificationOutcome.MISMATCH
    assert services.get_session(active.id).state is SessionState.ACTIVE

    exported = services.export_notebook(active.id, "json")
    payload = json.loads(exported.content)
    assert exported.media_type == "application/json"
    assert any(
        entry["entry_type"] == "measurement" for entry in payload["notebook_entries"]
    )
    assert any(
        entry["entry_type"] == "reagent_verification_proposal"
        for entry in payload["notebook_entries"]
    )


def test_draft_steps_can_be_reviewed_before_approval(services, sample_protocol_bytes) -> None:
    imported = services.import_protocol("review.docx", DOCX_MIME, sample_protocol_bytes)
    assert "development-only" in imported.version.source_text.casefold()
    reviewed_steps = tuple(
        replace(
            step,
            number=index,
            instruction=(
                "Add 10 mL ethanol to the reviewed demonstration vessel."
                if index == 1
                else step.instruction
            ),
        )
        for index, step in enumerate(imported.version.steps, start=1)
    )

    reviewed = services.update_protocol_draft(
        imported.version.id,
        reviewed_steps,
        expected_revision=0,
    )

    assert reviewed.revision == 1
    assert "reviewed demonstration" in reviewed.steps[0].instruction
    with pytest.raises(ApplicationError) as conflict:
        services.approve_protocol(reviewed.id, expected_revision=0)
    assert conflict.value.code == "PROTOCOL_VERSION_CONFLICT"
    approved = services.approve_protocol(reviewed.id, expected_revision=1)
    assert approved.status is ProtocolVersionStatus.APPROVED


def test_standalone_approval_revalidates_the_saved_draft(
    services,
    sample_protocol_bytes,
) -> None:
    imported = services.import_protocol(
        "invalid-saved-draft.docx",
        DOCX_MIME,
        sample_protocol_bytes,
    )
    with services._uow_factory() as uow:
        saved = uow.protocols.get_version(imported.version.id)
        assert saved is not None
        invalid = replace(
            saved,
            revision=1,
            steps=(replace(saved.steps[0], source=replace(saved.steps[0].source, excerpt=" ")),),
        )
        uow.protocols.save_version(invalid, expected_revision=0)

    with pytest.raises(ApplicationError) as caught:
        services.approve_protocol(imported.version.id, expected_revision=1)

    assert caught.value.code == "SOURCE_EXCERPT_REQUIRED"


def test_state_command_requires_confirmation(services, sample_protocol_bytes) -> None:
    active = start_session(services, sample_protocol_bytes)

    proposal = services.handle_text_command(
        active.id,
        "pause",
        idempotency_key="pause-1",
        expected_version=active.version,
        correlation_id="correlation-pause",
    )

    assert services.get_session(active.id).state is SessionState.ACTIVE
    assert proposal.pending_action is not None
    result = services.confirm_pending_action(
        proposal.pending_action.id,
        accepted=True,
        correlation_id="correlation-confirm-pause",
    )
    assert result.session.state is SessionState.PAUSED


def test_rejected_or_unsupported_command_does_not_mutate_session(
    services,
    sample_protocol_bytes,
) -> None:
    active = start_session(services, sample_protocol_bytes)
    unsupported = services.handle_text_command(
        active.id,
        "make everything perfect",
        idempotency_key="unknown-1",
        expected_version=active.version,
        correlation_id="correlation-unknown",
    )
    assert unsupported.pending_action is None
    assert services.get_session(active.id) == active

    pause = services.handle_text_command(
        active.id,
        "pause",
        idempotency_key="pause-reject",
        expected_version=active.version,
        correlation_id="correlation-pause-reject",
    )
    assert pause.pending_action is not None
    rejected = services.confirm_pending_action(
        pause.pending_action.id,
        accepted=False,
        correlation_id="correlation-reject",
    )
    assert rejected.session == active


def test_grounded_guidance_returns_reviewed_steps_without_advancing(
    services,
    sample_protocol_bytes,
) -> None:
    active = start_session(services, sample_protocol_bytes)

    current = services.handle_text_command(
        active.id,
        "what is the current step",
        idempotency_key="current-step-1",
        expected_version=active.version,
        correlation_id="correlation-current",
    )
    following = services.handle_text_command(
        active.id,
        "what is the next step",
        idempotency_key="next-step-1",
        expected_version=active.version,
        correlation_id="correlation-next",
    )

    assert current.pending_action is None
    assert "Add 10 mL ethanol" in current.response_text
    assert following.pending_action is None
    assert "Incubate" in following.response_text
    assert services.get_session(active.id) == active


def test_timer_and_early_end_require_explicit_confirmation(
    services,
    sample_protocol_bytes,
) -> None:
    active = start_session(services, sample_protocol_bytes)
    timer = services.handle_text_command(
        active.id,
        "set a timer for 5 minutes",
        idempotency_key="timer-1",
        expected_version=active.version,
        correlation_id="correlation-timer",
    )
    assert timer.pending_action is not None
    assert services.get_session(active.id) == active

    timer_result = services.confirm_pending_action(
        timer.pending_action.id,
        accepted=True,
        correlation_id="correlation-timer-confirm",
    )
    assert timer_result.entry is not None
    assert timer_result.entry.entry_type == "timer"
    assert timer_result.entry.payload["duration_seconds"] == "300"

    ending = services.handle_text_command(
        active.id,
        "end session",
        idempotency_key="end-1",
        expected_version=active.version,
        correlation_id="correlation-end",
    )
    assert ending.pending_action is not None
    assert services.get_session(active.id).state is SessionState.ACTIVE

    ended = services.confirm_pending_action(
        ending.pending_action.id,
        accepted=True,
        correlation_id="correlation-end-confirm",
    )
    assert ended.session.state is SessionState.ABORTED


def test_audio_command_and_append_only_correction(services, sample_protocol_bytes) -> None:
    active = start_session(services, sample_protocol_bytes)
    proposal = services.handle_audio_command(
        active.id,
        b"FAKE:record an observation as cloudy",
        content_type="audio/webm",
        idempotency_key="audio-1",
        expected_version=active.version,
        correlation_id="correlation-audio",
    )
    assert proposal.transcript == "record an observation as cloudy"
    assert proposal.pending_action is not None
    confirmed = services.confirm_pending_action(
        proposal.pending_action.id,
        accepted=True,
        correlation_id="correlation-audio-confirm",
    )
    assert confirmed.entry is not None

    corrected = services.correct_notebook_entry(
        confirmed.entry.id,
        {"observation": "slightly cloudy"},
        correlation_id="correlation-correction",
    )
    assert corrected.supersedes_entry_id == confirmed.entry.id
    entries = services.list_notebook(active.id)
    assert confirmed.entry in entries
    assert corrected in entries
    csv_export = services.export_notebook(active.id, "csv")
    assert csv_export.media_type.startswith("text/csv")
    assert b"supersedes_entry_id" in csv_export.content


def test_idempotency_key_cannot_be_reused_for_different_input(
    services,
    sample_protocol_bytes,
) -> None:
    active = start_session(services, sample_protocol_bytes)
    services.handle_text_command(
        active.id,
        "pause",
        idempotency_key="same-key",
        expected_version=active.version,
        correlation_id="correlation-one",
    )

    with pytest.raises(ApplicationError) as error:
        services.handle_text_command(
            active.id,
            "record an observation as clear",
            idempotency_key="same-key",
            expected_version=active.version,
            correlation_id="correlation-two",
        )

    assert error.value.code == "IDEMPOTENCY_CONFLICT"


def test_second_pending_action_is_blocked_until_the_first_is_resolved(
    services,
    sample_protocol_bytes,
) -> None:
    active = start_session(services, sample_protocol_bytes)
    pause = services.handle_text_command(
        active.id,
        "pause",
        idempotency_key="stale-pause",
        expected_version=active.version,
        correlation_id="correlation-stale-pause",
    )
    assert pause.pending_action is not None
    with pytest.raises(ApplicationError) as error:
        services.handle_text_command(
            active.id,
            "record an observation as clear",
            idempotency_key="blocked-observation",
            expected_version=active.version,
            correlation_id="correlation-blocked-observation",
        )

    assert error.value.code == "PENDING_ACTION_EXISTS"
    resolved = services.confirm_pending_action(
        pause.pending_action.id,
        accepted=True,
        correlation_id="correlation-pause-confirmed",
    )
    assert resolved.session.state is SessionState.PAUSED


def test_invalid_fake_image_rolls_back_without_notebook_effect(
    services,
    sample_protocol_bytes,
) -> None:
    active = start_session(services, sample_protocol_bytes)
    before = services.list_notebook(active.id)

    with pytest.raises(ModelInputError):
        services.verify_reagent(
            active.id,
            b"not-a-fake-fixture",
            content_type="image/jpeg",
            expected_version=active.version,
            idempotency_key="invalid-image",
            correlation_id="correlation-invalid-image",
        )

    assert services.list_notebook(active.id) == before


def test_reagent_verification_is_confirmed_or_corrected_append_only(
    services,
    sample_protocol_bytes,
) -> None:
    active = start_session(services, sample_protocol_bytes)
    proposed = services.verify_reagent(
        active.id,
        b"FAKE:ethano1",
        content_type="image/jpeg",
        expected_version=active.version,
        idempotency_key="label-proposal",
        correlation_id="correlation-label-proposal",
    )

    assert proposed.decision is VerificationDecision.PENDING
    assert proposed.observed_normalised == "ethano1"
    assert proposed.match_threshold == 92

    corrected = services.confirm_reagent_verification(
        proposed.id,
        accepted=True,
        corrected_observed_label="ethanol",
        expected_version=active.version,
        idempotency_key="label-correction",
        correlation_id="correlation-label-correction",
    )

    assert corrected.decision is VerificationDecision.CORRECTED
    assert corrected.outcome is VerificationOutcome.MATCH
    assert corrected.observed_label == "ethanol"
    assert any(
        entry.entry_type == "reagent_verification_confirmation"
        for entry in services.list_notebook(active.id)
    )
