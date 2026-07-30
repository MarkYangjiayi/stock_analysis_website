"""Add durable anomaly scan runs.

Revision ID: 0005_add_anomaly_scan_runs
Revises: 0004_add_rrg_price_snapshots
"""

from alembic import op
import sqlalchemy as sa


revision = "0005_add_anomaly_scan_runs"
down_revision = "0004_add_rrg_price_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("anomaly_scan_runs"):
        return
    op.create_table(
        "anomaly_scan_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("trigger", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("active_key", sa.String(), nullable=True),
        sa.Column("requested_limit", sa.Integer(), nullable=False),
        sa.Column("threshold_pct", sa.Float(), nullable=False),
        sa.Column("universe_as_of", sa.Date(), nullable=True),
        sa.Column("quote_as_of", sa.DateTime(), nullable=True),
        sa.Column("results", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "active_key",
            name="uq_anomaly_scan_runs_active_key",
        ),
    )
    op.create_index(
        "ix_anomaly_scan_runs_trigger",
        "anomaly_scan_runs",
        ["trigger"],
    )
    op.create_index(
        "ix_anomaly_scan_runs_status",
        "anomaly_scan_runs",
        ["status"],
    )
    op.create_index(
        "ix_anomaly_scan_runs_universe_as_of",
        "anomaly_scan_runs",
        ["universe_as_of"],
    )
    op.create_index(
        "ix_anomaly_scan_runs_quote_as_of",
        "anomaly_scan_runs",
        ["quote_as_of"],
    )
    op.create_index(
        "ix_anomaly_scan_runs_created_at",
        "anomaly_scan_runs",
        ["created_at"],
    )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("anomaly_scan_runs"):
        op.drop_table("anomaly_scan_runs")
