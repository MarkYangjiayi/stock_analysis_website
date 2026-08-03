"""Add immutable market breadth snapshots.

Revision ID: 0007_market_breadth_snapshots
Revises: 0006_add_anomaly_scan_leases
"""

from alembic import op
import sqlalchemy as sa


revision = "0007_market_breadth_snapshots"
down_revision = "0006_add_anomaly_scan_leases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("market_breadth_snapshots"):
        op.create_table(
            "market_breadth_snapshots",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "pipeline_run_id",
                sa.Integer(),
                sa.ForeignKey("pipeline_runs.id"),
                nullable=False,
            ),
            sa.Column("universe", sa.String(), nullable=False),
            sa.Column("date", sa.Date(), nullable=False),
            sa.Column("member_count", sa.Integer(), nullable=False),
            sa.Column("price_count", sa.Integer(), nullable=False),
            sa.Column("return_count", sa.Integer(), nullable=False),
            sa.Column("advances", sa.Integer(), nullable=False),
            sa.Column("declines", sa.Integer(), nullable=False),
            sa.Column("unchanged", sa.Integer(), nullable=False),
            sa.Column("ma20_eligible", sa.Integer(), nullable=False),
            sa.Column("above_ma20", sa.Integer(), nullable=False),
            sa.Column("ma50_eligible", sa.Integer(), nullable=False),
            sa.Column("above_ma50", sa.Integer(), nullable=False),
            sa.Column("ma200_eligible", sa.Integer(), nullable=False),
            sa.Column("above_ma200", sa.Integer(), nullable=False),
            sa.Column("high_low_eligible", sa.Integer(), nullable=False),
            sa.Column("new_high_count", sa.Integer(), nullable=False),
            sa.Column("new_low_count", sa.Integer(), nullable=False),
            sa.Column("dispersion_1d", sa.Float(), nullable=True),
        )
        op.create_index(
            "ix_market_breadth_snapshots_run_universe_date",
            "market_breadth_snapshots",
            ["pipeline_run_id", "universe", "date"],
            unique=True,
        )

    inspector = sa.inspect(op.get_bind())
    membership_indexes = {
        index["name"] for index in inspector.get_indexes("universe_membership")
    }
    if "ix_universe_membership_universe_interval" not in membership_indexes:
        op.create_index(
            "ix_universe_membership_universe_interval",
            "universe_membership",
            ["universe", "effective_from", "effective_to", "ticker"],
            unique=False,
        )

    op.execute("PRAGMA optimize")


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("universe_membership"):
        membership_indexes = {
            index["name"] for index in inspector.get_indexes("universe_membership")
        }
        if "ix_universe_membership_universe_interval" in membership_indexes:
            op.drop_index(
                "ix_universe_membership_universe_interval",
                table_name="universe_membership",
            )
    if inspector.has_table("market_breadth_snapshots"):
        op.drop_table("market_breadth_snapshots")
