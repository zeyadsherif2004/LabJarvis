"""Preserve extracted source text beside reviewed protocol versions."""

import sqlalchemy as sa
from alembic import op

revision = "0002_preserve_protocol_source"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "protocol_versions",
        sa.Column("source_text", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    with op.batch_alter_table("protocol_versions") as batch:
        batch.drop_column("source_text")
