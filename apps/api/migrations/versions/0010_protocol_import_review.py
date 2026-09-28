"""Keep source text that protocol import did not place in any step.

Extraction flags need no column: they live inside each step in ``steps_json``,
and steps written before flags existed simply have none.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_protocol_import_review"
down_revision = "0009_session_labels_archive"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "protocol_versions",
        sa.Column("unassigned_json", sa.Text(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    with op.batch_alter_table("protocol_versions") as batch:
        batch.drop_column("unassigned_json")
