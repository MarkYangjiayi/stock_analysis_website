"""Add consensus-revision and short-interest columns to the screener snapshot.

Revision ID: 0024_expectation_signals
Revises: 0023_theme_trends
"""

from alembic import op
import sqlalchemy as sa


revision = "0024_expectation_signals"
down_revision = "0023_theme_trends"
branch_labels = None
depends_on = None

COLUMNS = (
    "eps_revision_30d",
    "eps_revision_90d",
    "eps_revision_breadth_30d",
    "eps_estimate_analysts",
    "short_interest_change",
    "short_ratio",
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
