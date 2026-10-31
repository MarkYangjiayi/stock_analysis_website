"""Core-tier flag on watchlist tickers and versioned investment theses.

Revision ID: 0029_core_tier_theses
Revises: 0028_live_quotes
"""

from alembic import op
import sqlalchemy as sa


revision = "0029_core_tier_theses"
down_revision = "0028_live_quotes"
branch_labels = None
depends_on = None


def _watchlist_columns() -> set[str] | None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("personal_watchlist_items"):
        return None
    return {column["name"] for column in inspector.get_columns("personal_watchlist_items")}


def upgrade() -> None:
    # Fresh installs already get both from 0001's create_all.
    columns = _watchlist_columns()
    if columns is not None and "is_core" not in columns:
        op.add_column(
            "personal_watchlist_items",
            sa.Column("is_core", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    if not sa.inspect(op.get_bind()).has_table("ticker_thesis_versions"):
        op.create_table(
            "ticker_thesis_versions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("ticker", sa.String(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("content", sa.JSON(), nullable=False),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("ticker", "version", name="uix_ticker_thesis_version"),
        )
        op.create_index("ix_ticker_thesis_versions_ticker", "ticker_thesis_versions", ["ticker"])


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("ticker_thesis_versions"):
        op.drop_table("ticker_thesis_versions")
    columns = _watchlist_columns()
    if columns is not None and "is_core" in columns:
        op.drop_column("personal_watchlist_items", "is_core")
