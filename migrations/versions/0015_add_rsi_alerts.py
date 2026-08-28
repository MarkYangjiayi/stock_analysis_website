"""Persist idempotent daily RSI alerts.

Revision ID: 0015_add_rsi_alerts
Revises: 0014_screener_fund_cache
"""

from alembic import op
import sqlalchemy as sa


revision = "0015_add_rsi_alerts"
down_revision = "0014_screener_fund_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("rsi_alerts"):
        return
    op.create_table(
        "rsi_alerts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ticker", sa.String(), nullable=False),
        sa.Column("price_date", sa.Date(), nullable=False),
        sa.Column("period", sa.Integer(), nullable=False),
        sa.Column("rsi_value", sa.Float(), nullable=False),
        sa.Column("zone", sa.String(), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("notified_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "ticker",
            "price_date",
            "period",
            "zone",
            name="uix_rsi_alert_ticker_date_period_zone",
        ),
    )
    op.create_index("ix_rsi_alerts_ticker", "rsi_alerts", ["ticker"])
    op.create_index("ix_rsi_alerts_price_date", "rsi_alerts", ["price_date"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("rsi_alerts"):
        op.drop_table("rsi_alerts")
