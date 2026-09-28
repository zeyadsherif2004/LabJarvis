"""Tie an utterance to the proposal it was recorded to answer.

Spoken confirmation needs three things the previous utterance table could not
express. First, *what the utterance was for*: interpreting a command and
answering a confirmation are different acts and must not be conflated in the
audit trail. Second, *which proposal it answered*, so a reviewer can see the
proposal and the human reply as one pair. Third, *a hash of the audio*, which is
what lets the backend refuse a confirmation that simply replays the recording
that produced the proposal in the first place - the only structural freshness
check available to a server that cannot trust client timestamps.

Existing rows are backfilled as command utterances with no audio hash, which is
exactly what they were.
"""

import sqlalchemy as sa
from alembic import op

from labjarvis.infrastructure.migration_support import column_exists, index_exists

revision = "0006_spoken_confirmations"
down_revision = "0005_utterance_cross_checks"
branch_labels = None
depends_on = None

# (name, type, server default). Built fresh per use: a Column object cannot be
# attached to more than one table, and reusing one across a retry would fail.
NEW_COLUMNS: tuple[tuple[str, sa.types.TypeEngine[str], str], ...] = (
    ("purpose", sa.String(length=20), "command"),
    ("pending_action_id", sa.String(length=36), ""),
    ("audio_sha256", sa.String(length=64), ""),
    ("decision", sa.String(length=20), ""),
)


def upgrade() -> None:
    connection = op.get_bind()

    # Runtime schema creation against a current metadata may already have made
    # `utterances` with these columns, while a database contaminated earlier has
    # the 0005 shape and needs all four. Each is checked on its own so both
    # states, and any mixture, converge on the same result.
    for name, column_type, default in NEW_COLUMNS:
        if not column_exists(connection, "utterances", name):
            op.add_column(
                "utterances",
                sa.Column(name, column_type, nullable=False, server_default=default),
            )
    # Looked up on every spoken confirmation to reject a replayed recording.
    if not index_exists(connection, "utterances", "ix_utterances_session_audio"):
        op.create_index(
            "ix_utterances_session_audio",
            "utterances",
            ["session_id", "audio_sha256"],
        )


def downgrade() -> None:
    op.drop_index("ix_utterances_session_audio", table_name="utterances")
    op.drop_column("utterances", "decision")
    op.drop_column("utterances", "audio_sha256")
    op.drop_column("utterances", "pending_action_id")
    op.drop_column("utterances", "purpose")
