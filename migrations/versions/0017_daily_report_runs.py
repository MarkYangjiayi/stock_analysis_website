"""Add auditable daily report delivery runs.

Revision ID: 0017_daily_report_runs
Revises: 0016_financial_flow_runs
"""

from alembic import op
import sqlalchemy as sa


revision = "0017_daily_report_runs"
down_revision = "0016_financial_flow_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("daily_report_runs"):
        return
    op.create_table(
        "daily_report_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("report_type", sa.String(), nullable=False),
        sa.Column("renderer_version", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("source_results", sa.JSON(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("notification_delivered", sa.Boolean(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_daily_report_runs_report_type",
        "daily_report_runs",
        ["report_type"],
    )
    op.create_index(
        "ix_daily_report_runs_status",
        "daily_report_runs",
        ["status"],
    )
    op.create_index(
        "ix_daily_report_runs_created_at",
        "daily_report_runs",
        ["created_at"],
    )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("daily_report_runs"):
        op.drop_table("daily_report_runs")
