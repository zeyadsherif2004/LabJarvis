"""Central SQLAlchemy metadata shared by aggregate repositories and migrations."""

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)

from labjarvis.domain.models import PendingActionStatus, VerificationDecision

metadata = MetaData()

protocols_table = Table(
    "protocols",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("title", String(250), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("archived_at", DateTime(timezone=True)),
)

protocol_versions_table = Table(
    "protocol_versions",
    metadata,
    Column("id", String(36), primary_key=True),
    Column(
        "protocol_id", String(36), ForeignKey("protocols.id", ondelete="CASCADE"), nullable=False
    ),
    Column("version_number", Integer, nullable=False),
    Column("revision", Integer, nullable=False),
    Column("status", String(20), nullable=False),
    Column("steps_json", Text, nullable=False),
    Column("source_sha256", String(64), nullable=False),
    Column("source_text", Text, nullable=False),
    # Source text import did not place in any step, kept for review (JSON list).
    Column("unassigned_json", Text, nullable=False, server_default="[]"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("approved_at", DateTime(timezone=True)),
    UniqueConstraint("protocol_id", "version_number", name="uq_protocol_version_number"),
)

sessions_table = Table(
    "sessions",
    metadata,
    Column("id", String(36), primary_key=True),
    Column(
        "protocol_version_id",
        String(36),
        ForeignKey("protocol_versions.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("protocol_approved", Boolean, nullable=False),
    Column("state", String(20), nullable=False),
    Column("steps_json", Text, nullable=False),
    Column("active_step_id", String(80)),
    Column("version", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("started_at", DateTime(timezone=True)),
    Column("ended_at", DateTime(timezone=True)),
    Column("label", String(120)),
)

pending_actions_table = Table(
    "pending_actions",
    metadata,
    Column("id", String(36), primary_key=True),
    Column(
        "session_id", String(36), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    ),
    Column("intent", String(80), nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("source", String(50), nullable=False),
    Column("status", String(20), nullable=False),
    Column("expected_session_version", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("correlation_id", String(80), nullable=False),
    Column("model_inference_id", String(36), nullable=False),
    Column("resolved_at", DateTime(timezone=True)),
)

Index(
    "uq_pending_actions_one_pending_per_session",
    pending_actions_table.c.session_id,
    unique=True,
    sqlite_where=pending_actions_table.c.status == PendingActionStatus.PENDING.value,
    postgresql_where=pending_actions_table.c.status == PendingActionStatus.PENDING.value,
)
Index(
    "ix_pending_actions_session_status_created",
    pending_actions_table.c.session_id,
    pending_actions_table.c.status,
    pending_actions_table.c.created_at,
)

notebook_entries_table = Table(
    "notebook_entries",
    metadata,
    Column("id", String(36), primary_key=True),
    Column(
        "session_id", String(36), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    ),
    Column("entry_type", String(80), nullable=False),
    Column("source", String(50), nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("correlation_id", String(80), nullable=False),
    Column("supersedes_entry_id", String(36)),
    Column("model_inference_id", String(36)),
    Index("ix_notebook_session_created", "session_id", "created_at"),
)

model_inferences_table = Table(
    "model_inferences",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("session_id", String(36), ForeignKey("sessions.id")),
    Column("modality", String(20), nullable=False),
    Column("adapter", String(120), nullable=False),
    Column("model_name", String(160), nullable=False),
    Column("status", String(20), nullable=False),
    Column("confidence", Float),
    Column("latency_ms", Integer, nullable=False),
    Column("input_sha256", String(64), nullable=False),
    Column("output_json", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

reagent_verifications_table = Table(
    "reagent_verifications",
    metadata,
    Column("id", String(36), primary_key=True),
    Column(
        "session_id", String(36), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    ),
    Column("step_id", String(80), nullable=False),
    Column("requirement_id", String(120), nullable=False, server_default=""),
    Column("expected_label", String(250), nullable=False),
    Column("observed_label", String(250), nullable=False),
    Column("outcome", String(20), nullable=False),
    Column("score", Float, nullable=False),
    Column("expected_normalised", String(250), nullable=False),
    Column("observed_normalised", String(250), nullable=False),
    Column("match_threshold", Float, nullable=False),
    Column("uncertain_threshold", Float, nullable=False),
    Column("model_confidence", Float),
    Column("decision", String(20), nullable=False),
    Column("model_inference_id", String(36)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("resolved_at", DateTime(timezone=True)),
)

Index(
    "uq_reagent_verifications_one_pending_per_requirement",
    reagent_verifications_table.c.session_id,
    reagent_verifications_table.c.step_id,
    reagent_verifications_table.c.requirement_id,
    unique=True,
    sqlite_where=reagent_verifications_table.c.decision == VerificationDecision.PENDING.value,
    postgresql_where=reagent_verifications_table.c.decision
    == VerificationDecision.PENDING.value,
)
Index(
    "ix_reagent_verifications_session_step_requirement_decision",
    reagent_verifications_table.c.session_id,
    reagent_verifications_table.c.step_id,
    reagent_verifications_table.c.requirement_id,
    reagent_verifications_table.c.decision,
)

session_timers_table = Table(
    "session_timers",
    metadata,
    Column("id", String(36), primary_key=True),
    Column(
        "session_id", String(36), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    ),
    Column("step_id", String(80)),
    Column("label", String(120), nullable=False),
    Column("duration_seconds", Integer, nullable=False),
    Column("state", String(20), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("deadline_at", DateTime(timezone=True)),
    Column("remaining_seconds_at_pause", Integer),
    Column("paused_at", DateTime(timezone=True)),
    Column("resolved_at", DateTime(timezone=True)),
    Column("correlation_id", String(80), nullable=False),
    Column("version", Integer, nullable=False),
    Index("ix_session_timers_session_state", "session_id", "state"),
)

utterances_table = Table(
    "utterances",
    metadata,
    Column("id", String(36), primary_key=True),
    Column(
        "session_id", String(36), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    ),
    Column("realtime_transcript", Text, nullable=False),
    Column("verification_transcript", Text, nullable=False),
    Column("canonical_transcript", Text, nullable=False),
    Column("agreement", String(20), nullable=False),
    Column("reasons_json", Text, nullable=False),
    Column("primary_model", String(160), nullable=False),
    Column("verification_model", String(160), nullable=False),
    Column("primary_inference_id", String(36), nullable=False),
    Column("verification_inference_id", String(36), nullable=False),
    Column("confirmed_transcript", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("correlation_id", String(80), nullable=False),
    Column("purpose", String(20), nullable=False, server_default="command"),
    Column("pending_action_id", String(36), nullable=False, server_default=""),
    Column("audio_sha256", String(64), nullable=False, server_default=""),
    Column("decision", String(20), nullable=False, server_default=""),
    Index("ix_utterances_session_created", "session_id", "created_at"),
    Index("ix_utterances_session_audio", "session_id", "audio_sha256"),
)

audit_events_table = Table(
    "audit_events",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("session_id", String(36), ForeignKey("sessions.id")),
    Column("event_type", String(100), nullable=False),
    Column("actor", String(100), nullable=False),
    Column("correlation_id", String(80), nullable=False),
    Column("details_json", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

idempotency_records_table = Table(
    "idempotency_records",
    metadata,
    Column("scope", String(120), primary_key=True),
    Column("key", String(120), primary_key=True),
    Column("request_sha256", String(64), nullable=False),
    Column("response_json", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True)),
)
