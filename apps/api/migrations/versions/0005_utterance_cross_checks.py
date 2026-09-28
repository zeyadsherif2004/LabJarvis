"""Record every reading taken of a spoken utterance, and whether they agreed.

A measurement in the notebook has to be traceable to what was actually heard.
When two transcription paths read the same audio, both readings are kept along
with the deterministic verdict on whether they agreed, so a later reviewer can
see not just the recorded value but how much confidence it was recorded with.
"""

import sqlalchemy as sa
from alembic import op

from labjarvis.infrastructure.migration_support import adopt_existing_table, index_exists

revision = "0005_utterance_cross_checks"
down_revision = "0004_durable_timers_and_requirement_ids"
branch_labels = None
depends_on = None

UTTERANCE_COLUMNS = {
    "id",
    "session_id",
    "realtime_transcript",
    "verification_transcript",
    "canonical_transcript",
    "agreement",
    "reasons_json",
    "primary_model",
    "verification_model",
    "primary_inference_id",
    "verification_inference_id",
    "confirmed_transcript",
    "created_at",
    "correlation_id",
}


def upgrade() -> None:
    connection = op.get_bind()

    # As with 0004: runtime schema creation may have produced this table early.
    # Extra columns are tolerated because create_all() may have run against a
    # metadata that already knew about 0006; that migration checks its own
    # columns individually.
    if adopt_existing_table(
        connection,
        "utterances",
        required_columns=UTTERANCE_COLUMNS,
        revision=revision,
    ):
        op.create_table(
            "utterances",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "session_id",
                sa.String(length=36),
                sa.ForeignKey("sessions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("realtime_transcript", sa.Text(), nullable=False),
            sa.Column("verification_transcript", sa.Text(), nullable=False),
            sa.Column("canonical_transcript", sa.Text(), nullable=False),
            sa.Column("agreement", sa.String(length=20), nullable=False),
            sa.Column("reasons_json", sa.Text(), nullable=False),
            sa.Column("primary_model", sa.String(length=160), nullable=False),
            sa.Column("verification_model", sa.String(length=160), nullable=False),
            sa.Column("primary_inference_id", sa.String(length=36), nullable=False),
            sa.Column("verification_inference_id", sa.String(length=36), nullable=False),
            sa.Column("confirmed_transcript", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("correlation_id", sa.String(length=80), nullable=False),
        )
    if not index_exists(connection, "utterances", "ix_utterances_session_created"):
        op.create_index(
            "ix_utterances_session_created",
            "utterances",
            ["session_id", "created_at"],
        )


def downgrade() -> None:
    op.drop_index("ix_utterances_session_created", table_name="utterances")
    op.drop_table("utterances")
