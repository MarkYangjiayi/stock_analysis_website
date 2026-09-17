import os
from pathlib import Path
import sqlite3
import subprocess
import sys


def test_existing_report_survives_market_context_migration(tmp_path):
    path = tmp_path / "old-reports.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)")
        db.execute("INSERT INTO alembic_version VALUES ('0019_valuation_forecasts')")
        db.execute("""CREATE TABLE daily_report_runs (
            id INTEGER PRIMARY KEY, report_type VARCHAR, renderer_version VARCHAR,
            status VARCHAR, source_results JSON, content TEXT,
            notification_delivered BOOLEAN, error_message TEXT,
            created_at DATETIME, finished_at DATETIME
        )""")
        db.execute("""INSERT INTO daily_report_runs
            (id, report_type, renderer_version, status, source_results, content, notification_delivered)
            VALUES (1, 'morning_briefing', 'evidence-v1', 'delivered', '[]', 'original content', 1)
        """)
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{path}", "ENVIRONMENT": "test"}
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                   cwd=Path(__file__).resolve().parents[1], env=env, check=True, capture_output=True)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT content, source_results, market_context FROM daily_report_runs").fetchone() == ("original content", "[]", None)
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0021_index_valuation_snapshots"
