"""Add personal decision-support persistence.

Revision ID: 0009_personal_decision_support
Revises: 0008_backfill_live_universe_source
"""

from alembic import op
import sqlalchemy as sa


revision = "0009_personal_decision_support"
down_revision = "0008_backfill_live_universe_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("personal_watchlist_items"):
        op.create_table(
            "personal_watchlist_items",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("ticker", sa.String(), nullable=False),
            sa.Column("sort_order", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("ticker"),
        )
        op.create_index(
            "ix_personal_watchlist_items_ticker",
            "personal_watchlist_items",
            ["ticker"],
            unique=True,
        )
        op.create_index(
            "ix_personal_watchlist_items_sort_order",
            "personal_watchlist_items",
            ["sort_order"],
            unique=False,
        )

    if not inspector.has_table("ticker_valuation_scenarios"):
        op.create_table(
            "ticker_valuation_scenarios",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("ticker", sa.String(), nullable=False),
            sa.Column("scenario", sa.String(), nullable=False),
            sa.Column("fcf_growth_rate", sa.Float(), nullable=False),
            sa.Column("wacc", sa.Float(), nullable=False),
            sa.Column("perpetual_growth", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "ticker",
                "scenario",
                name="uix_ticker_valuation_scenario",
            ),
        )
        op.create_index(
            "ix_ticker_valuation_scenarios_ticker",
            "ticker_valuation_scenarios",
            ["ticker"],
            unique=False,
        )

    if not inspector.has_table("decision_brief_cache"):
        op.create_table(
            "decision_brief_cache",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("ticker", sa.String(), nullable=False),
            sa.Column("evidence_hash", sa.String(), nullable=False),
            sa.Column("model", sa.String(), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("evidence_ids", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "ticker",
                "evidence_hash",
                "model",
                name="uix_decision_brief_evidence_model",
            ),
        )
        op.create_index(
            "ix_decision_brief_cache_ticker",
            "decision_brief_cache",
            ["ticker"],
            unique=False,
        )
        op.create_index(
            "ix_decision_brief_cache_evidence_hash",
            "decision_brief_cache",
            ["evidence_hash"],
            unique=False,
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table_name in (
        "decision_brief_cache",
        "ticker_valuation_scenarios",
        "personal_watchlist_items",
    ):
        if inspector.has_table(table_name):
            op.drop_table(table_name)
