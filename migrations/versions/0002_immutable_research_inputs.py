"""Keep factor vintages and signal snapshots immutable.

Revision ID: 0002_immutable_research_inputs
Revises: 0001_initial
"""

from alembic import op
import sqlalchemy as sa


revision = "0002_immutable_research_inputs"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def _unique_names(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {
        constraint["name"]
        for constraint in inspector.get_unique_constraints(table)
        if constraint.get("name")
    }


def _column_names(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table)}


def _index_names(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {
        index["name"]
        for index in inspector.get_indexes(table)
        if index.get("name")
    }


def upgrade() -> None:
    factor_constraints = _unique_names("factor_values")
    if "uix_factor_value_run" not in factor_constraints:
        with op.batch_alter_table("factor_values") as batch:
            if "uix_factor_value_version" in factor_constraints:
                batch.drop_constraint("uix_factor_value_version", type_="unique")
            batch.create_unique_constraint(
                "uix_factor_value_run",
                ["ticker", "as_of_date", "factor_name", "version", "source_run_id"],
            )

    signal_columns = _column_names("signal_snapshots")
    signal_constraints = _unique_names("signal_snapshots")
    if "backtest_run_id" not in signal_columns:
        with op.batch_alter_table("signal_snapshots") as batch:
            batch.add_column(sa.Column("backtest_run_id", sa.Integer(), nullable=True))
            batch.create_foreign_key(
                "fk_signal_snapshot_backtest_run",
                "backtest_runs",
                ["backtest_run_id"],
                ["id"],
            )
            batch.create_index(
                "ix_signal_snapshots_backtest_run_id",
                ["backtest_run_id"],
                unique=False,
            )
    if "uix_backtest_signal_date" not in signal_constraints:
        with op.batch_alter_table("signal_snapshots") as batch:
            if "uix_strategy_signal_date" in signal_constraints:
                batch.drop_constraint("uix_strategy_signal_date", type_="unique")
            batch.create_unique_constraint(
                "uix_backtest_signal_date",
                ["backtest_run_id", "ticker", "as_of_date"],
            )


def downgrade() -> None:
    signal_constraints = _unique_names("signal_snapshots")
    signal_columns = _column_names("signal_snapshots")
    if (
        "uix_strategy_signal_date" not in signal_constraints
        or "backtest_run_id" in signal_columns
    ):
        # The legacy schema cannot represent multiple runs of one strategy.
        # Retain the most recently inserted snapshot for each legacy key.
        op.execute(sa.text("""
            DELETE FROM signal_snapshots
            WHERE id NOT IN (
                SELECT MAX(id)
                FROM signal_snapshots
                GROUP BY strategy_id, ticker, as_of_date
            )
        """))
        foreign_keys = sa.inspect(op.get_bind()).get_foreign_keys("signal_snapshots")
        backtest_foreign_key = next(
            (
                foreign_key
                for foreign_key in foreign_keys
                if foreign_key.get("constrained_columns") == ["backtest_run_id"]
            ),
            None,
        )
        naming_convention = {
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        }
        with op.batch_alter_table(
            "signal_snapshots",
            naming_convention=naming_convention,
        ) as batch:
            if "uix_backtest_signal_date" in signal_constraints:
                batch.drop_constraint("uix_backtest_signal_date", type_="unique")
            if "uix_strategy_signal_date" not in signal_constraints:
                batch.create_unique_constraint(
                    "uix_strategy_signal_date",
                    ["strategy_id", "ticker", "as_of_date"],
                )
            if "backtest_run_id" in signal_columns:
                if "ix_signal_snapshots_backtest_run_id" in _index_names("signal_snapshots"):
                    batch.drop_index("ix_signal_snapshots_backtest_run_id")
                if backtest_foreign_key is not None:
                    foreign_key_name = backtest_foreign_key.get("name") or (
                        "fk_signal_snapshots_backtest_run_id_backtest_runs"
                    )
                    batch.drop_constraint(foreign_key_name, type_="foreignkey")
                batch.drop_column("backtest_run_id")

    factor_constraints = _unique_names("factor_values")
    if "uix_factor_value_version" not in factor_constraints:
        # Likewise, collapse append-only factor vintages to the newest row
        # before restoring the legacy uniqueness rule.
        op.execute(sa.text("""
            DELETE FROM factor_values
            WHERE id NOT IN (
                SELECT MAX(id)
                FROM factor_values
                GROUP BY ticker, as_of_date, factor_name, version
            )
        """))
        with op.batch_alter_table("factor_values") as batch:
            if "uix_factor_value_run" in factor_constraints:
                batch.drop_constraint("uix_factor_value_run", type_="unique")
            batch.create_unique_constraint(
                "uix_factor_value_version",
                ["ticker", "as_of_date", "factor_name", "version"],
            )
