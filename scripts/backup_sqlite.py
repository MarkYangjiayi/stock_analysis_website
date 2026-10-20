"""Create consistent online SQLite backups with retention.

Each verified copy is stored zstd-compressed (``.db.zst``). Restore one with
``zstd -d <backup>.db.zst -o <target>.db``.
"""

import argparse
import hashlib
import logging
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import zstandard

sys.path.append(str(Path(__file__).resolve().parents[1]))

from core.config import settings

logger = logging.getLogger(__name__)

# Level 9 shrinks the production database to about a tenth in roughly a minute
# on one core; higher levels cost several times longer for little more.
COMPRESSION_LEVEL = 9
CHUNK_BYTES = 1024 * 1024


class _HashingReader:
    def __init__(self, handle):
        self._handle = handle
        self.digest = hashlib.sha256()
        self.size = 0

    def read(self, size=-1):
        chunk = self._handle.read(size)
        self.digest.update(chunk)
        self.size += len(chunk)
        return chunk


class _HashingWriter:
    def __init__(self):
        self.digest = hashlib.sha256()
        self.size = 0

    def write(self, data):
        self.digest.update(data)
        self.size += len(data)
        return len(data)


def sqlite_path_from_url(database_url: str) -> Path:
    if not database_url.startswith("sqlite"):
        raise ValueError("SQLite backup can only be used with a SQLite DATABASE_URL")
    raw_path = database_url.split("///", 1)[-1].split("?", 1)[0]
    return Path(raw_path).resolve()


def compress_backup(path: Path) -> Path:
    """Replace a verified backup with a zstd copy that restores to the same bytes."""
    compressed = path.with_name(f"{path.name}.zst")
    temporary = path.with_name(f"{compressed.name}.tmp")
    try:
        with path.open("rb") as source_handle, temporary.open("wb") as target_handle:
            original = _HashingReader(source_handle)
            zstandard.ZstdCompressor(level=COMPRESSION_LEVEL, write_checksum=True).copy_stream(
                original,
                target_handle,
                size=path.stat().st_size,
                read_size=CHUNK_BYTES,
                write_size=CHUNK_BYTES,
            )
            target_handle.flush()
            os.fsync(target_handle.fileno())
        restored = _HashingWriter()
        with temporary.open("rb") as handle:
            zstandard.ZstdDecompressor().copy_stream(
                handle,
                restored,
                read_size=CHUNK_BYTES,
                write_size=CHUNK_BYTES,
            )
        if (restored.size, restored.digest.digest()) != (original.size, original.digest.digest()):
            raise RuntimeError(f"Compressed backup does not restore to the verified copy: {temporary}")
        os.replace(temporary, compressed)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    path.unlink()
    return compressed


def create_backup(
    source_path: Optional[Path] = None,
    backup_dir: Optional[Path] = None,
    retention: int = 2,
) -> Path:
    source = (source_path or sqlite_path_from_url(settings.DATABASE_URL)).resolve()
    destination_dir = (backup_dir or Path(settings.BACKUP_DIR)).resolve()
    if not source.exists():
        raise FileNotFoundError(f"Database not found: {source}")
    destination_dir.mkdir(parents=True, exist_ok=True)
    source_bytes = source.stat().st_size
    # The verified copy and its compressed form briefly coexist. zstd shrinks
    # this database to about a tenth, so a quarter is a safe allowance.
    required_bytes = source_bytes + source_bytes // 4 + 512 * 1024 * 1024
    available_bytes = shutil.disk_usage(destination_dir).free
    if available_bytes < required_bytes:
        raise RuntimeError(
            f"Not enough free space for SQLite backup: need at least {required_bytes} "
            f"bytes, have {available_bytes} bytes"
        )
    # Left behind only by an interrupted run; each holds hundreds of megabytes.
    for stale in destination_dir.glob(f"{source.stem}-*.db.zst.tmp"):
        stale.unlink(missing_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = destination_dir / f"{source.stem}-{timestamp}.db"

    try:
        with sqlite3.connect(str(source)) as source_conn, sqlite3.connect(str(destination)) as destination_conn:
            source_conn.backup(destination_conn)
            # A backup should be one portable file. The source uses WAL for live
            # concurrency, but retaining that journal mode makes even a read-only
            # inspection create empty -wal/-shm sidecars next to the backup.
            destination_conn.execute("PRAGMA journal_mode=DELETE")
            result = destination_conn.execute("PRAGMA quick_check").fetchone()
            if not result or result[0] != "ok":
                raise RuntimeError(f"Backup integrity check failed: {result}")
        source_conn.close()
        destination_conn.close()
    except Exception:
        # A failed copy is not a recovery point and can otherwise fill the disk
        # before the next attempt gets a chance to run.
        for suffix in ("", "-journal", "-wal", "-shm"):
            Path(f"{destination}{suffix}").unlink(missing_ok=True)
        raise

    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{destination}{suffix}")
        if sidecar.exists():
            sidecar.unlink()

    try:
        backup = compress_backup(destination)
    except Exception:
        # The uncompressed copy already passed its integrity check, so it stays
        # the recovery point instead of failing the deploy or the weekly job.
        logger.exception("Could not compress SQLite backup %s; keeping it uncompressed", destination)
        backup = destination

    # Retention counts copies made before compression was introduced as well.
    older = sorted(
        (
            path
            for pattern in (f"{source.stem}-*.db", f"{source.stem}-*.db.zst")
            for path in destination_dir.glob(pattern)
            if path != backup
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for expired in older[max(1, retention) - 1:]:
        expired.unlink()
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{expired}{suffix}")
            if sidecar.exists():
                sidecar.unlink()
    return backup


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create an online SQLite backup")
    parser.add_argument("--retention", type=int, default=2)
    args = parser.parse_args()
    print(create_backup(retention=args.retention))
