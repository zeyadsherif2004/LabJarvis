"""Add optional session labels and protocol-level archive metadata."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_session_labels_archive"
down_revision = "0008_simplify_step_states"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("label", sa.String(length=120), nullable=True))
    op.add_column(
        "protocols",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_sessions_label_created",
        "sessions",
        ["label", "created_at"],
    )
    op.create_index(
        "ix_protocols_archived_created",
        "protocols",
        ["archived_at", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_protocols_archived_created", table_name="protocols")
    op.drop_index("ix_sessions_label_created", table_name="sessions")
    op.drop_column("protocols", "archived_at")
    op.drop_column("sessions", "label")
