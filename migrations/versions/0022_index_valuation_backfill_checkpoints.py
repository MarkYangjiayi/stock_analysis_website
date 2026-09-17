"""Add durable index-valuation backfill checkpoints.

Revision ID: 0022_index_valuation_backfill_checkpoints
Revises: 0021_index_valuation_snapshots
"""

from alembic import op
import sqlalchemy as sa


revision = "0022_index_valuation_backfill_checkpoints"
down_revision = "0021_index_valuation_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("index_valuation_backfill_checkpoints"):
        op.create_table(
            "index_valuation_backfill_checkpoints",
            sa.Column(
                "ticker",
                sa.String(),
                sa.ForeignKey("tickers.ticker"),
                primary_key=True,
            ),
            sa.Column("fundamentals_normalized_at", sa.DateTime(), nullable=False),
            sa.Column(
                "raw_snapshot_id",
                sa.Integer(),
                sa.ForeignKey("raw_data_snapshots.id"),
                nullable=True,
            ),
        )
        op.create_index(
            "ix_index_valuation_backfill_checkpoints_normalized_at",
            "index_valuation_backfill_checkpoints",
            ["fundamentals_normalized_at"],
        )

    op.execute("PRAGMA optimize")


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("index_valuation_backfill_checkpoints"):
        op.drop_table("index_valuation_backfill_checkpoints")
