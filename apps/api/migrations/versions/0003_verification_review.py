"""Record verification thresholds, normalised labels, and operator decisions."""

import sqlalchemy as sa
from alembic import op

revision = "0003_verification_review"
down_revision = "0002_preserve_protocol_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "reagent_verifications",
        sa.Column("expected_normalised", sa.String(length=250), nullable=False, server_default=""),
    )
    op.add_column(
        "reagent_verifications",
        sa.Column("observed_normalised", sa.String(length=250), nullable=False, server_default=""),
    )
    op.add_column(
        "reagent_verifications",
        sa.Column("match_threshold", sa.Float(), nullable=False, server_default="0"),
    )
    op.add_column(
        "reagent_verifications",
        sa.Column("uncertain_threshold", sa.Float(), nullable=False, server_default="0"),
    )
    op.add_column(
        "reagent_verifications",
        sa.Column("model_confidence", sa.Float(), nullable=True),
    )
    op.add_column(
        "reagent_verifications",
        sa.Column("decision", sa.String(length=20), nullable=False, server_default="PENDING"),
    )
    op.add_column(
        "reagent_verifications",
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    with op.batch_alter_table("reagent_verifications") as batch:
        batch.drop_column("resolved_at")
        batch.drop_column("decision")
        batch.drop_column("model_confidence")
        batch.drop_column("uncertain_threshold")
        batch.drop_column("match_threshold")
        batch.drop_column("observed_normalised")
        batch.drop_column("expected_normalised")
