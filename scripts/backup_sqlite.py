"""Create consistent online SQLite backups with retention."""

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.append(str(Path(__file__).resolve().parents[1]))

from core.config import settings


def sqlite_path_from_url(database_url: str) -> Path:
    if not database_url.startswith("sqlite"):
        raise ValueError("SQLite backup can only be used with a SQLite DATABASE_URL")
    raw_path = database_url.split("///", 1)[-1].split("?", 1)[0]
    return Path(raw_path).resolve()


def create_backup(
    source_path: Optional[Path] = None,
    backup_dir: Optional[Path] = None,
    retention: int = 14,
) -> Path:
    source = (source_path or sqlite_path_from_url(settings.DATABASE_URL)).resolve()
    destination_dir = (backup_dir or Path(settings.BACKUP_DIR)).resolve()
    if not source.exists():
        raise FileNotFoundError(f"Database not found: {source}")
    destination_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = destination_dir / f"{source.stem}-{timestamp}.db"

    with sqlite3.connect(str(source)) as source_conn, sqlite3.connect(str(destination)) as destination_conn:
        source_conn.backup(destination_conn)
        # A backup should be one portable file. The source uses WAL for live
        # concurrency, but retaining that journal mode makes even a read-only
        # inspection create empty -wal/-shm sidecars next to the backup.
        destination_conn.execute("PRAGMA journal_mode=DELETE")
        result = destination_conn.execute("PRAGMA quick_check").fetchone()
        if not result or result[0] != "ok":
            raise RuntimeError(f"Backup integrity check failed: {result}")

    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{destination}{suffix}")
        if sidecar.exists():
            sidecar.unlink()

    backups = sorted(destination_dir.glob(f"{source.stem}-*.db"), key=lambda path: path.stat().st_mtime, reverse=True)
    for expired in backups[max(1, retention):]:
        expired.unlink()
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{expired}{suffix}")
            if sidecar.exists():
                sidecar.unlink()
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create an online SQLite backup")
    parser.add_argument("--retention", type=int, default=14)
    args = parser.parse_args()
    print(create_backup(retention=args.retention))
