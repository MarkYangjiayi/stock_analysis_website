"""Add durable financial-flow enrichment runs.

Revision ID: 0016_financial_flow_runs
Revises: 0015_add_rsi_alerts
"""

from alembic import op
import sqlalchemy as sa


revision = "0016_financial_flow_runs"
down_revision = "0015_add_rsi_alerts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("financial_flow_runs"):
        return
    op.create_table(
        "financial_flow_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ticker", sa.String(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("period_type", sa.String(), nullable=False),
        sa.Column("input_fingerprint", sa.String(), nullable=False),
        sa.Column("cache_identity", sa.String(), nullable=False),
        sa.Column("schema_version", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("stage", sa.String(), nullable=False),
        sa.Column("coverage_level", sa.String(), nullable=False),
        sa.Column("active_key", sa.String(), nullable=True),
        sa.Column("global_slot", sa.String(), nullable=True),
        sa.Column("owner_token", sa.String(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("source_snapshots", sa.JSON(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("validation_report", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["ticker"], ["tickers.ticker"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("active_key"),
        sa.UniqueConstraint("global_slot"),
        sa.UniqueConstraint(
            "ticker", "period_end", "period_type", "cache_identity",
            name="uix_financial_flow_period_cache",
        ),
    )
    for column in (
        "ticker", "period_end", "period_type", "input_fingerprint",
        "cache_identity", "status", "coverage_level", "owner_token",
        "lease_expires_at", "created_at",
    ):
        op.create_index(
            f"ix_financial_flow_runs_{column}",
            "financial_flow_runs",
            [column],
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("financial_flow_runs"):
        op.drop_table("financial_flow_runs")
