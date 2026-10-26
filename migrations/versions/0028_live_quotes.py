"""Live quote leases, latest WebSocket trades and the feed heartbeat for the analysis header.

Revision ID: 0028_live_quotes
Revises: 0027_quality_scorecard
"""

from alembic import op
import sqlalchemy as sa


revision = "0028_live_quotes"
down_revision = "0027_quality_scorecard"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Fresh installs already get the tables from 0001's create_all.
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("live_quote_leases"):
        op.create_table(
            "live_quote_leases",
            sa.Column("ticker", sa.String(), primary_key=True),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_live_quote_leases_expires_at", "live_quote_leases", ["expires_at"])
    if not inspector.has_table("live_quotes"):
        op.create_table(
            "live_quotes",
            sa.Column("ticker", sa.String(), primary_key=True),
            sa.Column("price", sa.Float(), nullable=False),
            sa.Column("traded_at", sa.DateTime(), nullable=False),
            sa.Column("session_date", sa.Date(), nullable=False),
            sa.Column("received_at", sa.DateTime(), nullable=False),
        )
    if not inspector.has_table("live_quote_feed"):
        op.create_table(
            "live_quote_feed",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("connected", sa.Boolean(), nullable=False),
            sa.Column("tickers", sa.JSON(), nullable=False),
            sa.Column("heartbeat_at", sa.DateTime(), nullable=False),
            sa.Column("error", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table in ("live_quote_feed", "live_quotes", "live_quote_leases"):
        if inspector.has_table(table):
            op.drop_table(table)
