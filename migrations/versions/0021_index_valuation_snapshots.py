"""Add immutable index-level valuation snapshots.

Revision ID: 0021_index_valuation_snapshots
Revises: 0020_daily_report_market_context
"""

from alembic import op
import sqlalchemy as sa


revision = "0021_index_valuation_snapshots"
down_revision = "0020_daily_report_market_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("index_valuation_snapshots"):
        op.create_table(
            "index_valuation_snapshots",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "pipeline_run_id",
                sa.Integer(),
                sa.ForeignKey("pipeline_runs.id"),
                nullable=False,
            ),
            sa.Column("universe", sa.String(), nullable=False),
            sa.Column("date", sa.Date(), nullable=False),
            sa.Column("member_count", sa.Integer(), nullable=False),
            sa.Column("covered_count", sa.Integer(), nullable=False),
            sa.Column("loss_maker_count", sa.Integer(), nullable=False),
            sa.Column("coverage_pct", sa.Float(), nullable=True),
            sa.Column("equity_total", sa.Float(), nullable=True),
            sa.Column("earnings_ttm_total", sa.Float(), nullable=True),
            sa.Column("earnings_ttm_earners", sa.Float(), nullable=True),
            sa.Column("index_pe", sa.Float(), nullable=True),
            sa.Column("index_pe_earners", sa.Float(), nullable=True),
            sa.Column("median_pe", sa.Float(), nullable=True),
        )
        op.create_index(
            "ix_index_valuation_snapshots_run_universe_date",
            "index_valuation_snapshots",
            ["pipeline_run_id", "universe", "date"],
            unique=True,
        )

    op.execute("PRAGMA optimize")


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("index_valuation_snapshots"):
        op.drop_table("index_valuation_snapshots")
