"""ICE BofA US High Yield option-adjusted spread (HY OAS) from FRED.

The spread is the extra yield high-yield corporate bonds pay over Treasuries;
it widens when credit stress rises. FRED's public CSV needs no API key. ICE
licensing limits FRED to about the last three years of this series, which is
enough for a one-month change and a three-year percentile. A disk cache keeps
the last observations available when FRED is briefly unreachable.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import math
import os
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

from core.config import settings

logger = logging.getLogger(__name__)
HY_OAS_SERIES = "BAMLH0A0HYM2"
FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
HY_OAS_SOURCE_URL = f"https://fred.stlouisfed.org/series/{HY_OAS_SERIES}"
CACHE_SCHEMA_VERSION = 1
CACHE_HOURS = 12
CHANGE_WINDOW_DAYS = 30

_refresh_lock = asyncio.Lock()


class CreditSpreadUnavailable(RuntimeError):
    """Raised when neither FRED nor a usable local cache can supply the series."""


def _cache_path() -> Path:
    return Path(settings.DATA_DIR) / "fred_hy_oas_cache.json"


def parse_fred_csv(content: str) -> list[list[Any]]:
    """[[iso_date, percent], ...]; FRED marks missing days with "."."""
    observations: list[list[Any]] = []
    reader = csv.reader(io.StringIO(content))
    header = next(reader, None)
    if not header or len(header) < 2:
        return observations
    for row in reader:
        if len(row) < 2:
            continue
        try:
            day = date.fromisoformat(row[0].strip())
            spread = float(row[1])
        except ValueError:
            continue
        if math.isfinite(spread):
            observations.append([day.isoformat(), spread])
    observations.sort(key=lambda item: item[0])
    return observations


async def fetch_hy_oas(client: Optional[httpx.AsyncClient] = None) -> list[list[Any]]:
    async def run(http: httpx.AsyncClient) -> list[list[Any]]:
        response = await http.get(
            FRED_CSV_URL,
            params={"id": HY_OAS_SERIES},
            headers={"Accept": "text/csv,*/*;q=0.8", "User-Agent": "FinBrain/1.0 (+https://finbrain.icu)"},
        )
        response.raise_for_status()
        observations = parse_fred_csv(response.text)
        if not observations:
            raise CreditSpreadUnavailable("FRED returned no high-yield spread observations")
        return observations

    if client is not None:
        return await run(client)
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as owned:
        return await run(owned)


def _read_cache() -> Optional[dict[str, Any]]:
    path = _cache_path()
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("schema_version") != CACHE_SCHEMA_VERSION or not payload.get("observations"):
        return None
    return payload


def _write_cache(payload: dict[str, Any]) -> None:
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, suffix=".json.tmp", delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(payload, temporary, separators=(",", ":"))
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _fetched_at(cache: Optional[dict[str, Any]]) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(str((cache or {}).get("fetched_at")))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def summarize_hy_oas(observations: list[list[Any]]) -> dict[str, Any]:
    """Latest level, change over about a month, and percentile in the available history."""
    as_of, latest = observations[-1]
    reference_day = (date.fromisoformat(as_of) - timedelta(days=CHANGE_WINDOW_DAYS)).isoformat()
    earlier = next((value for day, value in reversed(observations) if day <= reference_day), None)
    values = [value for _, value in observations]
    below = sum(value < latest for value in values)
    equal = sum(value == latest for value in values)
    return {
        "series": HY_OAS_SERIES,
        "source_url": HY_OAS_SOURCE_URL,
        "as_of": as_of,
        "value_pct": latest,
        "change_1m_pt": round(latest - earlier, 4) if earlier is not None else None,
        # Midrank percentile, so a level seen many times is not pushed to an extreme.
        "percentile": round(100.0 * (below + 0.5 * equal) / len(values), 1),
        "history_start": observations[0][0],
    }


async def get_hy_oas(
    *,
    now: Optional[datetime] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    cache = await asyncio.to_thread(_read_cache)
    fetched = _fetched_at(cache)
    refresh_failed = False
    if fetched is None or current - fetched > timedelta(hours=CACHE_HOURS):
        async with _refresh_lock:
            try:
                observations = await fetch_hy_oas(client)
                cache = {
                    "schema_version": CACHE_SCHEMA_VERSION,
                    "fetched_at": current.isoformat(),
                    "observations": observations,
                }
                try:
                    await asyncio.to_thread(_write_cache, cache)
                except OSError:
                    logger.warning("Could not persist the FRED high-yield spread cache")
            except (httpx.HTTPError, CreditSpreadUnavailable) as exc:
                if cache is None:
                    raise CreditSpreadUnavailable("FRED high-yield spread is unavailable") from exc
                logger.warning("FRED refresh failed (%s); using cached spreads", type(exc).__name__)
                refresh_failed = True
    if cache is None:
        raise CreditSpreadUnavailable("FRED high-yield spread is unavailable")
    return {**summarize_hy_oas(cache["observations"]), "from_cache_after_failure": refresh_failed}
