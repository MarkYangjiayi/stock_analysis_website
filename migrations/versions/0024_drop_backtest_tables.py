"""Drop the tables left by the removed Factor Lab backtester.

Revision ID: 0024_drop_backtest_tables
Revises: 0023_theme_trends
"""

from alembic import op
import sqlalchemy as sa


revision = "0024_drop_backtest_tables"
down_revision = "0023_theme_trends"
branch_labels = None
depends_on = None

# Children first: signal_snapshots references both other tables and
# backtest_runs references strategy_definitions.
BACKTEST_TABLES = ("signal_snapshots", "backtest_runs", "strategy_definitions")


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table in BACKTEST_TABLES:
        if inspector.has_table(table):
            op.drop_table(table)


def downgrade() -> None:
    # Restores the empty post-0002 schema only; dropped rows are not recoverable.
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("strategy_definitions"):
        op.create_table(
            "strategy_definitions",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("version", sa.String(), nullable=False),
            sa.Column("config", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("name", "version", name="uix_strategy_version"),
        )
        op.create_index("ix_strategy_definitions_name", "strategy_definitions", ["name"])
    if not inspector.has_table("backtest_runs"):
        op.create_table(
            "backtest_runs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("strategy_id", sa.Integer(), nullable=True),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("start_date", sa.Date(), nullable=False),
            sa.Column("end_date", sa.Date(), nullable=False),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("config", sa.JSON(), nullable=False),
            sa.Column("metrics", sa.JSON(), nullable=True),
            sa.Column("equity_curve", sa.JSON(), nullable=True),
            sa.Column("attribution", sa.JSON(), nullable=True),
            sa.Column("diagnostics", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["strategy_id"], ["strategy_definitions.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_backtest_runs_strategy_id", "backtest_runs", ["strategy_id"])
        op.create_index("ix_backtest_runs_status", "backtest_runs", ["status"])
    if not inspector.has_table("signal_snapshots"):
        op.create_table(
            "signal_snapshots",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("strategy_id", sa.Integer(), nullable=False),
            sa.Column("backtest_run_id", sa.Integer(), nullable=True),
            sa.Column("ticker", sa.String(), nullable=False),
            sa.Column("as_of_date", sa.Date(), nullable=False),
            sa.Column("score", sa.Float(), nullable=False),
            sa.Column("rank", sa.Integer(), nullable=False),
            sa.Column("target_weight", sa.Float(), nullable=True),
            sa.Column("factor_details", sa.JSON(), nullable=True),
            sa.ForeignKeyConstraint(["strategy_id"], ["strategy_definitions.id"]),
            sa.ForeignKeyConstraint(["backtest_run_id"], ["backtest_runs.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "backtest_run_id",
                "ticker",
                "as_of_date",
                name="uix_backtest_signal_date",
            ),
        )
        op.create_index("ix_signal_snapshots_strategy_id", "signal_snapshots", ["strategy_id"])
        op.create_index("ix_signal_snapshots_backtest_run_id", "signal_snapshots", ["backtest_run_id"])
        op.create_index("ix_signal_snapshots_ticker", "signal_snapshots", ["ticker"])
        op.create_index("ix_signal_snapshots_as_of_date", "signal_snapshots", ["as_of_date"])
