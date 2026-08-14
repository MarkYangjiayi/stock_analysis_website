"""Add resumable normalized fundamentals cache for the daily screener.

Revision ID: 0014_screener_fund_cache
Revises: 0013_repair_membership_source
"""

from alembic import op
import sqlalchemy as sa


revision = "0014_screener_fund_cache"
down_revision = "0013_repair_membership_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("screener_fundamental_cache"):
        op.create_table(
            "screener_fundamental_cache",
            sa.Column("ticker", sa.String(), nullable=False),
            sa.Column("as_of_date", sa.Date(), nullable=False),
            sa.Column("fetched_at", sa.DateTime(), nullable=False),
            sa.Column("normalized_data", sa.JSON(), nullable=False),
            sa.Column("raw_snapshot_id", sa.Integer(), nullable=True),
            sa.Column("source", sa.String(), nullable=False),
            sa.ForeignKeyConstraint(["raw_snapshot_id"], ["raw_data_snapshots.id"]),
            sa.ForeignKeyConstraint(["ticker"], ["tickers.ticker"]),
            sa.PrimaryKeyConstraint("ticker"),
        )
        op.create_index(
            "ix_screener_fundamental_cache_as_of_date",
            "screener_fundamental_cache",
            ["as_of_date"],
        )
        op.create_index(
            "ix_screener_fundamental_cache_fetched_at",
            "screener_fundamental_cache",
            ["fetched_at"],
        )
    if not inspector.has_table("screener_fundamental_refresh_plans"):
        op.create_table(
            "screener_fundamental_refresh_plans",
            sa.Column("target_date", sa.Date(), nullable=False),
            sa.Column("tickers", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("target_date"),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("screener_fundamental_refresh_plans"):
        op.drop_table("screener_fundamental_refresh_plans")
    if inspector.has_table("screener_fundamental_cache"):
        op.drop_table("screener_fundamental_cache")
