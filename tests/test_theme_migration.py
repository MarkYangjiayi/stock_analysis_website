import importlib.util
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine

from models import Base


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = PROJECT_ROOT / "migrations" / "versions" / "0023_theme_trends.py"


def _theme_tables() -> tuple[str, ...]:
    spec = importlib.util.spec_from_file_location("theme_migration_0023", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.THEME_TABLES


def _alembic(path: Path, *args: str) -> None:
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{path}", "ENVIRONMENT": "test"}
    subprocess.run([sys.executable, "-m", "alembic", *args], cwd=PROJECT_ROOT, env=env,
                   check=True, capture_output=True, text=True)


def _schema(path: Path, tables: tuple[str, ...]) -> dict:
    """Columns (name, type, notnull, pk), FKs, named indexes and unique column sets per table."""
    schema = {}
    with sqlite3.connect(path) as db:
        for table in tables:
            columns = [tuple(row[1:4]) + (row[5],) for row in db.execute(f"PRAGMA table_info({table})")]
            fks = sorted((row[3], row[2], row[4]) for row in db.execute(f"PRAGMA foreign_key_list({table})"))
            named, unique = {}, set()
            for _, name, is_unique, origin, _ in db.execute(f"PRAGMA index_list({table})"):
                cols = tuple(row[2] for row in db.execute(f"PRAGMA index_info({name})"))
                if origin == "c":
                    named[name] = (cols, bool(is_unique))
                elif is_unique:
                    unique.add(cols)
            schema[table] = {"columns": columns, "fks": fks, "indexes": named, "unique": unique}
    return schema


def test_theme_migration_matches_models_and_downgrades(tmp_path):
    tables = _theme_tables()
    assert set(tables) == {
        name for name, table in Base.metadata.tables.items()
        if name.startswith(("theme", "news_", "etf_holdings", "ticker_attention", "company_trend"))
    }

    # 0001 builds every current model table, so drop the theme tables before
    # upgrading to prove that 0023's own DDL creates them.
    migrated = tmp_path / "migrated.db"
    _alembic(migrated, "upgrade", "0022_index_valuation_backfill_checkpoints")
    with sqlite3.connect(migrated) as db:
        for table in reversed(tables):
            db.execute(f"DROP TABLE IF EXISTS {table}")
    _alembic(migrated, "upgrade", "head")

    reference = tmp_path / "metadata.db"
    engine = create_engine(f"sqlite:///{reference}")
    Base.metadata.create_all(engine, tables=[Base.metadata.tables[name] for name in tables])
    engine.dispose()

    assert _schema(migrated, tables) == _schema(reference, tables)

    _alembic(migrated, "downgrade", "0022_index_valuation_backfill_checkpoints")
    with sqlite3.connect(migrated) as db:
        remaining = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert remaining.isdisjoint(tables)

    _alembic(migrated, "upgrade", "head")
    with sqlite3.connect(migrated) as db:
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0029_core_tier_theses"
        present = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert set(tables) <= present
