"""Expand the daily screener snapshot for practical Finviz parity.

Revision ID: 0003_expand_screener_snapshot
Revises: 0002_immutable_research_inputs
"""

from alembic import op
import sqlalchemy as sa


revision = "0003_expand_screener_snapshot"
down_revision = "0002_immutable_research_inputs"
branch_labels = None
depends_on = None


TEXT_COLUMNS = {"exchange", "country", "candlestick"}
DATE_COLUMNS = {"ipo_date"}
INTEGER_COLUMNS = {"shares_outstanding", "shares_float"}

NEW_COLUMNS = [
    "exchange", "country", "ipo_date", "short_float", "analyst_recommendation",
    "target_price", "shares_outstanding", "shares_float", "forward_pe", "peg_ratio",
    "ps_ratio", "price_cash", "price_fcf", "ev_ebitda", "ev_sales",
    "dividend_growth_1yr", "dividend_growth_3yr", "dividend_growth_5yr",
    "eps_growth_this_year", "eps_growth_next_year", "eps_growth_qoq",
    "eps_growth_ttm", "eps_growth_3yr", "eps_growth_5yr", "sales_growth_qoq",
    "sales_growth_ttm", "sales_growth_3yr", "roa", "roic", "current_ratio",
    "quick_ratio", "lt_debt_to_equity", "operating_margin", "net_profit_margin",
    "payout_ratio", "insider_ownership", "institutional_ownership",
    "average_volume_3m", "relative_volume", "ma200", "performance_1d",
    "performance_1w", "performance_1m", "performance_3m", "performance_6m",
    "performance_ytd", "performance_1yr", "volatility_1w", "volatility_1m",
    "gap", "change_from_open", "high_20d_rel", "low_20d_rel", "high_50d_rel",
    "low_50d_rel", "high_52w_rel", "low_52w_rel", "beta_1yr", "atr_14",
    "candlestick",
]


def _column(name: str) -> sa.Column:
    if name in TEXT_COLUMNS:
        return sa.Column(name, sa.String(), nullable=True)
    if name in DATE_COLUMNS:
        return sa.Column(name, sa.Date(), nullable=True)
    if name in INTEGER_COLUMNS:
        return sa.Column(name, sa.BigInteger(), nullable=True)
    return sa.Column(name, sa.Numeric(), nullable=True)


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing = {column["name"] for column in inspector.get_columns("stock_screener_snapshot")}
    for name in NEW_COLUMNS:
        if name not in existing:
            op.add_column("stock_screener_snapshot", _column(name))
    indexes = {index["name"] for index in inspector.get_indexes("stock_screener_snapshot")}
    for name in ("exchange", "country"):
        index_name = f"ix_stock_screener_snapshot_{name}"
        if index_name not in indexes:
            op.create_index(index_name, "stock_screener_snapshot", [name], unique=False)


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    indexes = {index["name"] for index in inspector.get_indexes("stock_screener_snapshot")}
    for name in ("country", "exchange"):
        index_name = f"ix_stock_screener_snapshot_{name}"
        if index_name in indexes:
            op.drop_index(index_name, table_name="stock_screener_snapshot")
    existing = {column["name"] for column in inspector.get_columns("stock_screener_snapshot")}
    for name in reversed(NEW_COLUMNS):
        if name in existing:
            op.drop_column("stock_screener_snapshot", name)
