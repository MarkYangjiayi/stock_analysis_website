"""Track initialization of intentionally empty personal data.

Revision ID: 0010_personal_workspace_state
Revises: 0009_personal_decision_support
"""

from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa


revision = "0010_personal_workspace_state"
down_revision = "0009_personal_decision_support"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("personal_workspace_state"):
        return

    op.create_table(
        "personal_workspace_state",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("watchlist_initialized", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    state_table = sa.table(
        "personal_workspace_state",
        sa.column("id", sa.Integer()),
        sa.column("watchlist_initialized", sa.Boolean()),
        sa.column("updated_at", sa.DateTime()),
    )
    has_watchlist = op.get_bind().execute(
        sa.text("SELECT 1 FROM personal_watchlist_items LIMIT 1")
    ).first() is not None
    op.bulk_insert(
        state_table,
        [{
            "id": 1,
            "watchlist_initialized": has_watchlist,
            "updated_at": datetime.now(timezone.utc).replace(tzinfo=None),
        }],
    )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("personal_workspace_state"):
        op.drop_table("personal_workspace_state")
