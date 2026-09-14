"""Persist cross-asset evidence alongside daily reports."""
from alembic import op
import sqlalchemy as sa

revision = "0020_daily_report_market_context"
down_revision = "0019_valuation_forecasts"
branch_labels = None
depends_on = None


def upgrade():
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("daily_report_runs")}
    if "market_context" not in columns:
        op.add_column("daily_report_runs", sa.Column("market_context", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("daily_report_runs", "market_context")
