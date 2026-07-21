import asyncio
import gzip
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.time_utils import utc_now
from models import RawDataSnapshot


@dataclass(frozen=True)
class StoredPayload:
    checksum: str
    path: Path
    size_bytes: int


def store_payload(
    source: str,
    dataset: str,
    payload: Any,
    fetched_at: Optional[datetime] = None,
    identity: Optional[dict] = None,
) -> StoredPayload:
    """Store an immutable canonical JSON payload as gzip."""
    fetched = fetched_at or utc_now()
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    identity_bytes = json.dumps(identity or {}, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    checksum = hashlib.sha256(source.encode() + b"\0" + dataset.encode() + b"\0" + identity_bytes + b"\0" + encoded).hexdigest()
    root = Path(settings.RAW_DATA_DIR)
    target_dir = root / source.lower() / dataset / fetched.strftime("%Y/%m/%d")
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{checksum}.json.gz"
    if not target.exists():
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(dir=target_dir, suffix=".json.gz.tmp", delete=False) as temporary:
                temporary_path = Path(temporary.name)
            with gzip.open(temporary_path, "wb") as handle:
                handle.write(encoded)
            os.replace(temporary_path, target)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
    return StoredPayload(checksum=checksum, path=target.resolve(), size_bytes=len(encoded))


async def persist_snapshot(
    db: AsyncSession,
    source: str,
    dataset: str,
    payload: Any,
    as_of_date: Optional[date] = None,
    details: Optional[dict] = None,
) -> RawDataSnapshot:
    stored = await asyncio.to_thread(store_payload, source, dataset, payload, None, details)
    stmt = insert(RawDataSnapshot).values(
        source=source,
        dataset=dataset,
        as_of_date=as_of_date,
        checksum=stored.checksum,
        storage_path=str(stored.path),
        status="stored",
        details={**(details or {}), "size_bytes": stored.size_bytes},
    ).on_conflict_do_nothing(index_elements=["checksum"])
    await db.execute(stmt)
    result = await db.execute(select(RawDataSnapshot).where(RawDataSnapshot.checksum == stored.checksum))
    return result.scalar_one()
