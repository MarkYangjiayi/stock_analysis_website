"""Add durable earnings-quality analysis runs.

Revision ID: 0011_earnings_quality_analysis
Revises: 0010_personal_workspace_state
"""

from alembic import op
import sqlalchemy as sa


revision = "0011_earnings_quality_analysis"
down_revision = "0010_personal_workspace_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("earnings_quality_analysis_runs"):
        return

    op.create_table(
        "earnings_quality_analysis_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ticker", sa.String(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("period_type", sa.String(), nullable=False),
        sa.Column("statement_fingerprint", sa.String(), nullable=False),
        sa.Column("cache_identity", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("prompt_version", sa.String(), nullable=False),
        sa.Column("schema_version", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("stage", sa.String(), nullable=False),
        sa.Column("active_key", sa.String(), nullable=True),
        sa.Column("global_slot", sa.String(), nullable=True),
        sa.Column("owner_token", sa.String(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("sec_accession", sa.String(), nullable=True),
        sa.Column("source_checksum", sa.String(), nullable=True),
        sa.Column("source_snapshots", sa.JSON(), nullable=True),
        sa.Column("ai_result", sa.JSON(), nullable=True),
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
    )
    op.create_index(
        "ix_earnings_quality_period_cache",
        "earnings_quality_analysis_runs",
        ["ticker", "period_end", "period_type", "cache_identity"],
    )
    for column in (
        "ticker",
        "period_end",
        "period_type",
        "statement_fingerprint",
        "cache_identity",
        "status",
        "owner_token",
        "lease_expires_at",
        "sec_accession",
        "source_checksum",
        "created_at",
    ):
        op.create_index(
            f"ix_earnings_quality_analysis_runs_{column}",
            "earnings_quality_analysis_runs",
            [column],
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("earnings_quality_analysis_runs"):
        op.drop_table("earnings_quality_analysis_runs")
