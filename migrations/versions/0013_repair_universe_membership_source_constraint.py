"""Repair source-aware universe membership uniqueness.

Revision ID: 0013_repair_membership_source
Revises: 0012_normalize_screener_values
"""

from alembic import op
import sqlalchemy as sa


revision = "0013_repair_membership_source"
down_revision = "0012_normalize_screener_values"
branch_labels = None
depends_on = None


LEGACY_COLUMNS = ["universe", "ticker", "effective_from"]
SOURCE_COLUMNS = [*LEGACY_COLUMNS, "source"]
LEGACY_NAME = "uix_universe_membership_period"
SOURCE_NAME = "uix_universe_membership_period_source"
NAMING_CONVENTION = {
    "uq": "uq_%(table_name)s_%(column_0_name)s_%(column_1_name)s_%(column_2_name)s",
}


def _unique_constraints() -> list[dict]:
    return sa.inspect(op.get_bind()).get_unique_constraints("universe_membership")


def upgrade() -> None:
    constraints = _unique_constraints()
    legacy_names = [
        constraint.get("name")
        or "uq_universe_membership_universe_ticker_effective_from"
        for constraint in constraints
        if constraint.get("column_names") == LEGACY_COLUMNS
    ]
    has_source_constraint = any(
        constraint.get("column_names") == SOURCE_COLUMNS
        for constraint in constraints
    )
    if not legacy_names and has_source_constraint:
        return

    with op.batch_alter_table(
        "universe_membership",
        naming_convention=NAMING_CONVENTION,
    ) as batch_op:
        for name in legacy_names:
            batch_op.drop_constraint(name, type_="unique")
        if not has_source_constraint:
            batch_op.create_unique_constraint(SOURCE_NAME, SOURCE_COLUMNS)


def downgrade() -> None:
    constraints = _unique_constraints()
    source_names = [
        constraint["name"]
        for constraint in constraints
        if constraint.get("name")
        and constraint.get("column_names") == SOURCE_COLUMNS
    ]
    if not source_names:
        return

    # A legacy constraint cannot represent source-specific observations.
    op.execute(
        """
        DELETE FROM universe_membership
        WHERE id NOT IN (
            SELECT MIN(id)
            FROM universe_membership
            GROUP BY universe, ticker, effective_from
        )
        """
    )
    with op.batch_alter_table(
        "universe_membership",
        naming_convention=NAMING_CONVENTION,
    ) as batch_op:
        for name in source_names:
            batch_op.drop_constraint(name, type_="unique")
        batch_op.create_unique_constraint(LEGACY_NAME, LEGACY_COLUMNS)
