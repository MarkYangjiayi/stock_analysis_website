"""Persist sourced operating forecasts alongside saved DCF scenarios."""
from alembic import op
import sqlalchemy as sa

revision = "0019_valuation_forecasts"
down_revision = "0018_add_provider_beta"
branch_labels = None
depends_on = None


def upgrade():
    # Early migrations create tables from current metadata on fresh installs.
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("ticker_valuation_scenarios")}
    if "forecast_inputs" not in columns:
        op.add_column("ticker_valuation_scenarios", sa.Column("forecast_inputs", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("ticker_valuation_scenarios", "forecast_inputs")
