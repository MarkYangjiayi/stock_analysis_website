"""Retain provider beta separately from the local one-year technical beta.

Revision ID: 0018_add_provider_beta
Revises: 0017_daily_report_runs
"""

from alembic import op
import sqlalchemy as sa


revision = "0018_add_provider_beta"
down_revision = "0017_daily_report_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("stock_screener_snapshot"):
        return
    existing = {
        column["name"]
        for column in inspector.get_columns("stock_screener_snapshot")
    }
    if "provider_beta" not in existing:
        op.add_column(
            "stock_screener_snapshot",
            sa.Column("provider_beta", sa.Numeric(), nullable=True),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("stock_screener_snapshot"):
        return
    existing = {
        column["name"]
        for column in inspector.get_columns("stock_screener_snapshot")
    }
    if "provider_beta" in existing:
        op.drop_column("stock_screener_snapshot", "provider_beta")
