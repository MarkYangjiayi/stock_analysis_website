"""Backfill legacy live index membership sources.

Revision ID: 0008_backfill_live_universe_source
Revises: 0007_market_breadth_snapshots
"""

from alembic import op
import sqlalchemy as sa


revision = "0008_backfill_live_universe_source"
down_revision = "0007_market_breadth_snapshots"
branch_labels = None
depends_on = None


LEGACY_SOURCE = "EODHD"
LIVE_SOURCE = "EODHD Live Index Components"
INDEX_UNIVERSES = ("SP500", "RUSSELL2000")


def _membership_table() -> sa.TableClause:
    return sa.table(
        "universe_membership",
        sa.column("id", sa.Integer),
        sa.column("universe", sa.String),
        sa.column("ticker", sa.String),
        sa.column("effective_from", sa.Date),
        sa.column("source", sa.String),
    )


def _replace_source(source: str, replacement: str) -> None:
    membership = _membership_table()
    candidate = membership.alias("candidate")
    existing = membership.alias("existing")
    duplicate_candidate_ids = sa.select(candidate.c.id).where(
        candidate.c.universe.in_(INDEX_UNIVERSES),
        candidate.c.source == source,
        sa.exists(
            sa.select(existing.c.id).where(
                existing.c.universe == candidate.c.universe,
                existing.c.ticker == candidate.c.ticker,
                existing.c.effective_from == candidate.c.effective_from,
                existing.c.source == replacement,
            )
        ),
    )
    op.execute(
        membership.delete().where(membership.c.id.in_(duplicate_candidate_ids))
    )
    op.execute(
        membership.update()
        .where(
            membership.c.universe.in_(INDEX_UNIVERSES),
            membership.c.source == source,
        )
        .values(source=replacement)
    )


def upgrade() -> None:
    _replace_source(LEGACY_SOURCE, LIVE_SOURCE)


def downgrade() -> None:
    _replace_source(LIVE_SOURCE, LEGACY_SOURCE)
