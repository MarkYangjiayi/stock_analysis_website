"""Add quality-scorecard columns to the screener snapshot.

Revision ID: 0027_quality_scorecard
Revises: 0026_data_freshness_alerts
"""

from alembic import op
import sqlalchemy as sa


revision = "0027_quality_scorecard"
down_revision = "0026_data_freshness_alerts"
branch_labels = None
depends_on = None

COLUMNS = (
    "quality_score",
    "quality_profitability",
    "quality_growth",
    "quality_cash_conversion",
    "quality_balance_sheet",
    "quality_capital_allocation",
    "piotroski_f_score",
    "altman_z",
    "roic_5y_avg",
    "fcf_conversion_5y",
    "share_change_5y",
)


def _existing_columns() -> set[str] | None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("stock_screener_snapshot"):
        return None
    return {column["name"] for column in inspector.get_columns("stock_screener_snapshot")}


def upgrade() -> None:
    existing = _existing_columns()
    if existing is None:
        return
    for name in COLUMNS:
        if name not in existing:
            op.add_column("stock_screener_snapshot", sa.Column(name, sa.Numeric(), nullable=True))


def downgrade() -> None:
    existing = _existing_columns()
    if existing is None:
        return
    for name in reversed(COLUMNS):
        if name in existing:
            op.drop_column("stock_screener_snapshot", name)
