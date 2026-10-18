"""Record delivered stale-data alerts so each dataset is reported once per session.

Revision ID: 0026_data_freshness_alerts
Revises: 0025_expectation_signals
"""

from alembic import op
import sqlalchemy as sa


revision = "0026_data_freshness_alerts"
down_revision = "0025_expectation_signals"
branch_labels = None
depends_on = None

TABLE = "data_freshness_alerts"


def upgrade() -> None:
    # Fresh installs already get the table from 0001's create_all.
    if sa.inspect(op.get_bind()).has_table(TABLE):
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("dataset", sa.String(), nullable=False),
        sa.Column("expected_date", sa.Date(), nullable=False),
        sa.Column("published_date", sa.Date(), nullable=True),
        sa.Column("notified_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("dataset", "expected_date", name="uix_data_freshness_alert_dataset_date"),
    )
    op.create_index("ix_data_freshness_alerts_dataset", TABLE, ["dataset"])
    op.create_index("ix_data_freshness_alerts_expected_date", TABLE, ["expected_date"])


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table(TABLE):
        op.drop_table(TABLE)
