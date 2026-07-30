"""Add immutable RRG price snapshots.

Revision ID: 0004_add_rrg_price_snapshots
Revises: 0003_expand_screener_snapshot
"""

from alembic import op
import sqlalchemy as sa


revision = "0004_add_rrg_price_snapshots"
down_revision = "0003_expand_screener_snapshot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("rrg_price_snapshots"):
        op.create_table(
            "rrg_price_snapshots",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "pipeline_run_id",
                sa.Integer(),
                sa.ForeignKey("pipeline_runs.id"),
                nullable=False,
            ),
            sa.Column(
                "ticker",
                sa.String(),
                sa.ForeignKey("tickers.ticker"),
                nullable=False,
            ),
            sa.Column("date", sa.Date(), nullable=False),
            sa.Column("close", sa.Numeric(), nullable=False),
            sa.UniqueConstraint(
                "pipeline_run_id",
                "ticker",
                "date",
                name="uix_rrg_snapshot_run_ticker_date",
            ),
        )


def downgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if inspector.has_table("rrg_price_snapshots"):
        if inspector.has_table("data_publications"):
            connection.execute(
                sa.text(
                    "DELETE FROM data_publications "
                    "WHERE dataset = :dataset"
                ),
                {"dataset": "rrg_price_history"},
            )
        op.drop_table("rrg_price_snapshots")
