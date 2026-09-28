from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from labjarvis.domain.models import (
    EntrySource,
    LabSession,
    NotebookEntry,
    PendingAction,
    PendingActionStatus,
    Protocol,
    ProtocolStepDraft,
    ProtocolVersion,
    ProtocolVersionStatus,
    ReagentVerification,
    SessionState,
    SessionStep,
    SourceReference,
    StepState,
    VerificationDecision,
    VerificationOutcome,
)
from labjarvis.infrastructure.database import (
    ConcurrencyError,
    SqlAlchemyUnitOfWork,
    create_session_factory,
)
from labjarvis.infrastructure.db.unit_of_work import (
    SqlAlchemyUnitOfWork as ExtractedUnitOfWork,
)

NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


def session_record(*, version: int = 0, state: SessionState = SessionState.DRAFT) -> LabSession:
    return LabSession(
        id="session-1",
        protocol_version_id="protocol-version-1",
        protocol_approved=True,
        state=state,
        steps=(SessionStep(step_id="step-1", state=StepState.NOT_STARTED),),
        active_step_id=None,
        version=version,
        created_at=NOW,
        label="Batch A",
    )


def protocol_records() -> tuple[Protocol, ProtocolVersion]:
    protocol = Protocol(id="protocol-1", title="Synthetic protocol", created_at=NOW)
    version = ProtocolVersion(
        id="protocol-version-1",
        protocol_id=protocol.id,
        version_number=1,
        revision=0,
        status=ProtocolVersionStatus.APPROVED,
        steps=(
            ProtocolStepDraft(
                id="step-1",
                number=1,
                title="Mix sample",
                instruction="Mix the synthetic sample.",
                requirements=(),
                duration_seconds=None,
                safety_notes=(),
                source=SourceReference(page=1, section="Demo", excerpt="1. Mix sample."),
            ),
        ),
        source_sha256="a" * 64,
        created_at=NOW,
        source_text="Original reviewed source text.",
        approved_at=NOW,
    )
    return protocol, version


def add_protocol(uow: SqlAlchemyUnitOfWork) -> None:
    protocol, version = protocol_records()
    uow.protocols.add(protocol, version)


def notebook_entry() -> NotebookEntry:
    return NotebookEntry(
        id="entry-1",
        session_id="session-1",
        entry_type="observation",
        source=EntrySource.TYPED_INPUT,
        payload={"value": "clear"},
        created_at=NOW,
        correlation_id="correlation-1",
    )


def pending_action(action_id: str) -> PendingAction:
    return PendingAction(
        id=action_id,
        session_id="session-1",
        intent="complete_step",
        payload={},
        source=EntrySource.MODEL_PROPOSAL,
        status=PendingActionStatus.PENDING,
        expected_session_version=0,
        created_at=NOW,
        correlation_id=f"correlation-{action_id}",
        model_inference_id="inference-1",
    )


def reagent_verification(verification_id: str) -> ReagentVerification:
    return ReagentVerification(
        id=verification_id,
        session_id="session-1",
        step_id="step-1",
        requirement_id="requirement-1",
        expected_label="Sodium chloride",
        observed_label="Sodium chloride",
        outcome=VerificationOutcome.MATCH,
        score=1.0,
        expected_normalised="sodium chloride",
        observed_normalised="sodium chloride",
        match_threshold=0.8,
        uncertain_threshold=0.6,
        model_confidence=1.0,
        decision=VerificationDecision.PENDING,
        model_inference_id="inference-1",
        created_at=NOW,
    )


def test_unit_of_work_rolls_back_all_command_effects(tmp_path) -> None:
    factory = create_session_factory(
        f"sqlite+pysqlite:///{tmp_path / 'rollback.db'}",
        create_schema=True,
    )

    with (
        pytest.raises(RuntimeError, match="force rollback"),
        SqlAlchemyUnitOfWork(factory) as uow,
    ):
        add_protocol(uow)
        uow.sessions.add(session_record())
        uow.notebook.append(notebook_entry())
        raise RuntimeError("force rollback")

    with SqlAlchemyUnitOfWork(factory) as uow:
        assert uow.sessions.get("session-1") is None
        assert uow.notebook.list_for_session("session-1") == ()


def test_stale_session_version_is_rejected(tmp_path) -> None:
    factory = create_session_factory(
        f"sqlite+pysqlite:///{tmp_path / 'concurrency.db'}",
        create_schema=True,
    )
    with SqlAlchemyUnitOfWork(factory) as uow:
        add_protocol(uow)
        uow.sessions.add(session_record())

    with SqlAlchemyUnitOfWork(factory) as uow:
        advanced = replace(session_record(), state=SessionState.READY, version=1)
        uow.sessions.save(advanced, expected_version=0)

    with (
        pytest.raises(ConcurrencyError, match="version changed"),
        SqlAlchemyUnitOfWork(factory) as uow,
    ):
        stale_update = replace(session_record(), state=SessionState.ABORTED, version=1)
        uow.sessions.save(stale_update, expected_version=0)


def test_session_steps_and_append_only_notebook_round_trip(tmp_path) -> None:
    factory = create_session_factory(
        f"sqlite+pysqlite:///{tmp_path / 'round-trip.db'}",
        create_schema=True,
    )
    with SqlAlchemyUnitOfWork(factory) as uow:
        add_protocol(uow)
        uow.sessions.add(session_record())
        uow.notebook.append(notebook_entry())

    with SqlAlchemyUnitOfWork(factory) as uow:
        loaded = uow.sessions.get("session-1")
        entries = uow.notebook.list_for_session("session-1")

    assert loaded == session_record()
    assert len(entries) == 1
    assert entries[0].payload == {"value": "clear"}
    assert entries[0].correlation_id == "correlation-1"


def test_protocol_and_version_round_trip(tmp_path) -> None:
    factory = create_session_factory(
        f"sqlite+pysqlite:///{tmp_path / 'protocol.db'}",
        create_schema=True,
    )
    protocol, version = protocol_records()
    with SqlAlchemyUnitOfWork(factory) as uow:
        uow.protocols.add(protocol, version)

    with SqlAlchemyUnitOfWork(factory) as uow:
        assert uow.protocols.get(protocol.id) == protocol
        assert uow.protocols.get_version(version.id) == version


def test_session_discovery_combines_label_state_date_and_protocol_filters(tmp_path) -> None:
    factory = create_session_factory(
        f"sqlite+pysqlite:///{tmp_path / 'session-filters.db'}",
        create_schema=True,
    )
    with SqlAlchemyUnitOfWork(factory) as uow:
        add_protocol(uow)
        uow.sessions.add(replace(session_record(), state=SessionState.ACTIVE))
        uow.sessions.add(
            replace(
                session_record(),
                id="session-2",
                label="Batch B",
                state=SessionState.PAUSED,
                created_at=NOW + timedelta(days=1),
            )
        )

    with SqlAlchemyUnitOfWork(factory) as uow:
        matches = uow.sessions.list_summaries(
            limit=20,
            states=(SessionState.ACTIVE,),
            label="batch a",
            created_from=NOW,
            created_to=NOW,
            protocol_version_id="protocol-version-1",
        )

    assert [item.session_id for item in matches] == ["session-1"]
    assert matches[0].label == "Batch A"
    with SqlAlchemyUnitOfWork(factory) as uow:
        assert [
            item.session_id
            for item in uow.sessions.list_summaries(
                limit=20,
                states=(SessionState.PAUSED,),
            )
        ] == ["session-2"]
        assert [
            item.session_id
            for item in uow.sessions.list_summaries(limit=20, label="batch b")
        ] == ["session-2"]
        assert [
            item.session_id
            for item in uow.sessions.list_summaries(
                limit=20,
                created_from=NOW + timedelta(days=1),
            )
        ] == ["session-2"]
        assert [
            item.session_id
            for item in uow.sessions.list_summaries(limit=20, created_to=NOW)
        ] == ["session-1"]
        assert [
            item.session_id
            for item in uow.sessions.list_summaries(limit=20, query="Batch B")
        ] == ["session-2"]


def test_protocol_discovery_hides_archived_by_default_and_can_select_it(tmp_path) -> None:
    factory = create_session_factory(
        f"sqlite+pysqlite:///{tmp_path / 'protocol-archive-filter.db'}",
        create_schema=True,
    )
    protocol, version = protocol_records()
    archived_at = NOW + timedelta(hours=1)
    archived = replace(protocol, id="protocol-2", title="Archived", archived_at=archived_at)
    archived_version = replace(
        version,
        id="protocol-version-2",
        protocol_id=archived.id,
    )
    with SqlAlchemyUnitOfWork(factory) as uow:
        uow.protocols.add(protocol, version)
        uow.protocols.add(archived, archived_version)

    with SqlAlchemyUnitOfWork(factory) as uow:
        current = uow.protocols.list_summaries(
            limit=20,
            query="Synthetic",
            created_from=NOW,
            created_to=NOW,
        )
        archived_only = uow.protocols.list_summaries(limit=20, archived=True)

    assert [item.protocol_id for item in current] == [protocol.id]
    assert [item.protocol_id for item in archived_only] == [archived.id]
    assert archived_only[0].archived_at == archived_at


def test_unit_of_work_requires_context_manager(tmp_path) -> None:
    factory = create_session_factory(
        f"sqlite+pysqlite:///{tmp_path / 'closed.db'}",
        create_schema=True,
    )
    uow = SqlAlchemyUnitOfWork(factory)

    with pytest.raises(RuntimeError, match="not active"):
        _ = uow.sessions


def test_only_one_pending_action_can_exist_for_a_session(tmp_path) -> None:
    """A missing partial unique index lets two incompatible proposals deadlock the UI."""

    factory = create_session_factory(
        f"sqlite+pysqlite:///{tmp_path / 'pending-action-unique.db'}",
        create_schema=True,
    )
    with SqlAlchemyUnitOfWork(factory) as uow:
        add_protocol(uow)
        uow.sessions.add(session_record())
        uow.pending_actions.add(pending_action("action-1"))

    with pytest.raises(Exception) as caught, SqlAlchemyUnitOfWork(factory) as uow:
        uow.pending_actions.add(pending_action("action-2"))

    assert getattr(caught.value, "code", None) == "PENDING_ACTION_EXISTS"


def test_only_one_pending_reagent_review_can_exist_per_requirement(tmp_path) -> None:
    """Concurrent uploads must not create two operator decisions for one requirement."""

    factory = create_session_factory(
        f"sqlite+pysqlite:///{tmp_path / 'verification-unique.db'}",
        create_schema=True,
    )
    with SqlAlchemyUnitOfWork(factory) as uow:
        add_protocol(uow)
        uow.sessions.add(session_record())
        uow.verifications.append(reagent_verification("verification-1"))

    with pytest.raises(Exception) as caught, SqlAlchemyUnitOfWork(factory) as uow:
        uow.verifications.append(reagent_verification("verification-2"))

    assert getattr(caught.value, "code", None) == "REAGENT_VERIFICATION_PENDING"


def test_legacy_database_module_reexports_unit_of_work() -> None:
    assert SqlAlchemyUnitOfWork is ExtractedUnitOfWork
