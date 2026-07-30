"""Add ownership leases to anomaly scan runs.

Revision ID: 0006_add_anomaly_scan_leases
Revises: 0005_add_anomaly_scan_runs
"""

from alembic import op
import sqlalchemy as sa


revision = "0006_add_anomaly_scan_leases"
down_revision = "0005_add_anomaly_scan_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {
        column["name"]
        for column in inspector.get_columns("anomaly_scan_runs")
    }
    indexes = {
        index["name"]
        for index in inspector.get_indexes("anomaly_scan_runs")
    }
    with op.batch_alter_table("anomaly_scan_runs") as batch_op:
        if "owner_token" not in columns:
            batch_op.add_column(
                sa.Column("owner_token", sa.String(), nullable=True)
            )
        if "lease_expires_at" not in columns:
            batch_op.add_column(
                sa.Column("lease_expires_at", sa.DateTime(), nullable=True)
            )
        if "ix_anomaly_scan_runs_owner_token" not in indexes:
            batch_op.create_index(
                "ix_anomaly_scan_runs_owner_token",
                ["owner_token"],
            )
        if "ix_anomaly_scan_runs_lease_expires_at" not in indexes:
            batch_op.create_index(
                "ix_anomaly_scan_runs_lease_expires_at",
                ["lease_expires_at"],
            )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {
        column["name"]
        for column in inspector.get_columns("anomaly_scan_runs")
    }
    indexes = {
        index["name"]
        for index in inspector.get_indexes("anomaly_scan_runs")
    }
    with op.batch_alter_table("anomaly_scan_runs") as batch_op:
        if "ix_anomaly_scan_runs_lease_expires_at" in indexes:
            batch_op.drop_index("ix_anomaly_scan_runs_lease_expires_at")
        if "ix_anomaly_scan_runs_owner_token" in indexes:
            batch_op.drop_index("ix_anomaly_scan_runs_owner_token")
        if "lease_expires_at" in columns:
            batch_op.drop_column("lease_expires_at")
        if "owner_token" in columns:
            batch_op.drop_column("owner_token")
