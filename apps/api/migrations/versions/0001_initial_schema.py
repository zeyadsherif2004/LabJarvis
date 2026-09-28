"""Create the initial LabJarvis persistence schema."""

import sqlalchemy as sa
from alembic import op

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "protocols",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("title", sa.String(length=250), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "protocol_versions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("protocol_id", sa.String(length=36), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("steps_json", sa.Text(), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["protocol_id"], ["protocols.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "protocol_id",
            "version_number",
            name="uq_protocol_version_number",
        ),
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("protocol_version_id", sa.String(length=36), nullable=False),
        sa.Column("protocol_approved", sa.Boolean(), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("steps_json", sa.Text(), nullable=False),
        sa.Column("active_step_id", sa.String(length=80), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["protocol_version_id"],
            ["protocol_versions.id"],
            ondelete="RESTRICT",
        ),
    )
    op.create_table(
        "pending_actions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("intent", sa.String(length=80), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("expected_session_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("correlation_id", sa.String(length=80), nullable=False),
        sa.Column("model_inference_id", sa.String(length=36), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "notebook_entries",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("entry_type", sa.String(length=80), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("correlation_id", sa.String(length=80), nullable=False),
        sa.Column("supersedes_entry_id", sa.String(length=36), nullable=True),
        sa.Column("model_inference_id", sa.String(length=36), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_notebook_session_created",
        "notebook_entries",
        ["session_id", "created_at"],
    )
    op.create_table(
        "model_inferences",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("session_id", sa.String(length=36), nullable=True),
        sa.Column("modality", sa.String(length=20), nullable=False),
        sa.Column("adapter", sa.String(length=120), nullable=False),
        sa.Column("model_name", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("input_sha256", sa.String(length=64), nullable=False),
        sa.Column("output_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"]),
    )
    op.create_table(
        "reagent_verifications",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("step_id", sa.String(length=80), nullable=False),
        sa.Column("expected_label", sa.String(length=250), nullable=False),
        sa.Column("observed_label", sa.String(length=250), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("model_inference_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("session_id", sa.String(length=36), nullable=True),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("actor", sa.String(length=100), nullable=False),
        sa.Column("correlation_id", sa.String(length=80), nullable=False),
        sa.Column("details_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"]),
    )
    op.create_table(
        "idempotency_records",
        sa.Column("scope", sa.String(length=120), primary_key=True),
        sa.Column("key", sa.String(length=120), primary_key=True),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column("response_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("idempotency_records")
    op.drop_table("audit_events")
    op.drop_table("reagent_verifications")
    op.drop_table("model_inferences")
    op.drop_index("ix_notebook_session_created", table_name="notebook_entries")
    op.drop_table("notebook_entries")
    op.drop_table("pending_actions")
    op.drop_table("sessions")
    op.drop_table("protocol_versions")
    op.drop_table("protocols")
