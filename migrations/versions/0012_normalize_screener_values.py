"""Normalize public screener values and retain raw PEG.

Revision ID: 0012_normalize_screener_values
Revises: 0011_earnings_quality_analysis
"""

from alembic import op
import sqlalchemy as sa


revision = "0012_normalize_screener_values"
down_revision = "0011_earnings_quality_analysis"
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
    if "peg_ratio_raw" not in existing:
        op.add_column(
            "stock_screener_snapshot",
            sa.Column("peg_ratio_raw", sa.Numeric(), nullable=True),
        )
    if "technical_quality" not in existing:
        op.add_column(
            "stock_screener_snapshot",
            sa.Column("technical_quality", sa.String(), nullable=True),
        )

    connection = op.get_bind()
    connection.execute(sa.text(
        """
        UPDATE stock_screener_snapshot
        SET peg_ratio_raw = peg_ratio
        WHERE peg_ratio IS NOT NULL AND peg_ratio_raw IS NULL
        """
    ))
    connection.execute(sa.text(
        """
        UPDATE stock_screener_snapshot
        SET peg_ratio = NULL
        WHERE peg_ratio <= 0
        """
    ))


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("stock_screener_snapshot"):
        return
    existing = {
        column["name"]
        for column in inspector.get_columns("stock_screener_snapshot")
    }
    if "peg_ratio_raw" in existing:
        op.get_bind().execute(sa.text(
            """
            UPDATE stock_screener_snapshot
            SET peg_ratio = peg_ratio_raw
            WHERE peg_ratio IS NULL AND peg_ratio_raw <= 0
            """
        ))
    if "technical_quality" in existing:
        op.drop_column("stock_screener_snapshot", "technical_quality")
    if "peg_ratio_raw" in existing:
        op.drop_column("stock_screener_snapshot", "peg_ratio_raw")
