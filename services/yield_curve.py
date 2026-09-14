"""Fetch and cache official U.S. Treasury par yield-curve observations."""

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
from typing import Any, Literal, Optional

import httpx

from core.config import settings
from core.trading_calendar import latest_completed_us_session


logger = logging.getLogger(__name__)
TREASURY_SOURCE_NAME = "U.S. Department of the Treasury"
TREASURY_SOURCE_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "TextView?type=daily_treasury_yield_curve"
)
TREASURY_CSV_BASE_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "daily-treasury-rates.csv"
)
TREASURY_CACHE_SCHEMA_VERSION = 1
PERIOD_YEARS: dict[str, int] = {"1y": 1, "3y": 3, "5y": 5}

MATURITIES: tuple[dict[str, Any], ...] = (
    {"key": "1m", "label": "1M", "years": 1 / 12, "csv_column": "1 Mo"},
    {"key": "6w", "label": "6W", "years": 1.5 / 12, "csv_column": "1.5 Month"},
    {"key": "2m", "label": "2M", "years": 2 / 12, "csv_column": "2 Mo"},
    {"key": "3m", "label": "3M", "years": 3 / 12, "csv_column": "3 Mo"},
    {"key": "4m", "label": "4M", "years": 4 / 12, "csv_column": "4 Mo"},
    {"key": "6m", "label": "6M", "years": 6 / 12, "csv_column": "6 Mo"},
    {"key": "1y", "label": "1Y", "years": 1.0, "csv_column": "1 Yr"},
    {"key": "2y", "label": "2Y", "years": 2.0, "csv_column": "2 Yr"},
    {"key": "3y", "label": "3Y", "years": 3.0, "csv_column": "3 Yr"},
    {"key": "5y", "label": "5Y", "years": 5.0, "csv_column": "5 Yr"},
    {"key": "7y", "label": "7Y", "years": 7.0, "csv_column": "7 Yr"},
    {"key": "10y", "label": "10Y", "years": 10.0, "csv_column": "10 Yr"},
    {"key": "20y", "label": "20Y", "years": 20.0, "csv_column": "20 Yr"},
    {"key": "30y", "label": "30Y", "years": 30.0, "csv_column": "30 Yr"},
)

_refresh_lock = asyncio.Lock()

EODHD_TENOR_KEYS = {
    "1M": "1m",
    "1.5M": "6w",
    "2M": "2m",
    "3M": "3m",
    "4M": "4m",
    "6M": "6m",
    "1Y": "1y",
    "2Y": "2y",
    "3Y": "3y",
    "5Y": "5y",
    "7Y": "7y",
    "10Y": "10y",
    "20Y": "20y",
    "30Y": "30y",
}


class YieldCurveUnavailable(RuntimeError):
    """Raised when neither Treasury nor a usable local cache can supply data."""


def _cache_path() -> Path:
    return Path(settings.DATA_DIR) / "treasury_yield_curve_cache.json"


def _parse_number(value: Any) -> Optional[float]:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return round(parsed, 4) if math.isfinite(parsed) else None


def parse_treasury_csv(content: str) -> list[dict[str, Any]]:
    """Normalize one official annual CSV into ascending daily observations."""
    observations: list[dict[str, Any]] = []
    for row in csv.DictReader(io.StringIO(content.lstrip("\ufeff"))):
        try:
            observation_date = datetime.strptime(
                str(row.get("Date") or "").strip(), "%m/%d/%Y"
            ).date()
        except ValueError:
            continue
        yields = {
            maturity["key"]: _parse_number(row.get(maturity["csv_column"]))
            for maturity in MATURITIES
        }
        if sum(value is not None for value in yields.values()) < 4:
            continue
        observations.append({"date": observation_date.isoformat(), "yields": yields})
    observations.sort(key=lambda item: item["date"])
    return observations


async def _fetch_year(year: int, client: httpx.AsyncClient) -> list[dict[str, Any]]:
    response = await client.get(
        f"{TREASURY_CSV_BASE_URL}/{year}/all",
        params={
            "type": "daily_treasury_yield_curve",
            "field_tdr_date_value": str(year),
            "page": "",
            "_format": "csv",
        },
        headers={
            "Accept": "text/csv,*/*;q=0.8",
            "User-Agent": "FinBrain/1.0 (+https://finbrain.icu)",
        },
    )
    response.raise_for_status()
    observations = parse_treasury_csv(response.text)
    if not observations:
        raise YieldCurveUnavailable(f"Treasury returned no yield observations for {year}")
    return observations


async def _fetch_eodhd_year(
    year: int, client: httpx.AsyncClient
) -> list[dict[str, Any]]:
    response = await client.get(
        f"{settings.EODHD_BASE_URL}/ust/yield-rates",
        params={
            "api_token": settings.EODHD_API_KEY,
            "filter[year]": str(year),
            "fmt": "json",
        },
    )
    response.raise_for_status()
    body = response.json()
    rows = body.get("data") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        raise YieldCurveUnavailable(f"Yield provider returned invalid data for {year}")

    by_date: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        date_key = str(row.get("date") or "")
        try:
            date.fromisoformat(date_key)
        except ValueError:
            continue
        maturity_key = EODHD_TENOR_KEYS.get(str(row.get("tenor") or "").upper())
        if maturity_key is None:
            continue
        observation = by_date.setdefault(
            date_key,
            {
                "date": date_key,
                "yields": {maturity["key"]: None for maturity in MATURITIES},
            },
        )
        observation["yields"][maturity_key] = _parse_number(row.get("rate"))
    observations = [by_date[key] for key in sorted(by_date)]
    observations = [
        item
        for item in observations
        if sum(value is not None for value in item["yields"].values()) >= 4
    ]
    if not observations:
        raise YieldCurveUnavailable(f"Yield provider returned no observations for {year}")
    return observations


async def fetch_treasury_observations(
    through: date,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> tuple[list[dict[str, Any]], str]:
    """Fetch six years of official rates, preferring the configured fast mirror."""
    years = range(through.year - PERIOD_YEARS["5y"], through.year + 1)

    def normalize(annual: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
        by_date = {
            observation["date"]: observation
            for observations in annual
            for observation in observations
            if observation["date"] <= through.isoformat()
        }
        return [by_date[key] for key in sorted(by_date)]

    async def execute(http_client: httpx.AsyncClient) -> tuple[list[dict[str, Any]], str]:
        if settings.EODHD_API_KEY and settings.EODHD_API_KEY != "demo":
            try:
                annual = await asyncio.gather(
                    *(_fetch_eodhd_year(year, http_client) for year in years)
                )
                return normalize(annual), "EODHD"
            except (httpx.HTTPError, ValueError, YieldCurveUnavailable) as exc:
                logger.warning(
                    "EODHD Treasury mirror is unavailable; using Treasury CSV fallback (%s)",
                    type(exc).__name__,
                )
        annual = await asyncio.gather(*(_fetch_year(year, http_client) for year in years))
        return normalize(annual), "U.S. Treasury"

    if client is not None:
        return await execute(client)
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as owned_client:
        return await execute(owned_client)


def _read_cache() -> Optional[dict[str, Any]]:
    path = _cache_path()
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if (
        payload.get("schema_version") != TREASURY_CACHE_SCHEMA_VERSION
        or not isinstance(payload.get("observations"), list)
        or not payload["observations"]
    ):
        return None
    return payload


def _write_cache(payload: dict[str, Any]) -> None:
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            suffix=".json.tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(payload, temporary, separators=(",", ":"), ensure_ascii=False)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _parsed_fetched_at(cache: Optional[dict[str, Any]]) -> Optional[datetime]:
    if not cache:
        return None
    try:
        parsed = datetime.fromisoformat(str(cache.get("fetched_at")))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _cache_is_fresh(cache: Optional[dict[str, Any]], now: datetime) -> bool:
    fetched_at = _parsed_fetched_at(cache)
    if fetched_at is None:
        return False
    return now - fetched_at <= timedelta(hours=settings.TREASURY_YIELD_CACHE_HOURS)


def _subtract_years(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year - years)
    except ValueError:
        return value.replace(year=value.year - years, month=2, day=28)


def _nearest_on_or_before(
    observations: list[dict[str, Any]], target: date
) -> Optional[dict[str, Any]]:
    target_key = target.isoformat()
    return next(
        (item for item in reversed(observations) if item["date"] <= target_key),
        None,
    )


def build_yield_curve_payload(
    observations: list[dict[str, Any]],
    period: Literal["1y", "3y", "5y"],
    *,
    now: datetime,
    fetched_at: datetime,
    provider_name: str = "U.S. Treasury",
    refresh_failed: bool = False,
) -> dict[str, Any]:
    if not observations:
        raise YieldCurveUnavailable("No Treasury yield-curve observations are available")

    observations = sorted(observations, key=lambda item: item["date"])
    latest = observations[-1]
    latest_date = date.fromisoformat(latest["date"])
    start_date = _subtract_years(now.date(), PERIOD_YEARS[period])
    selected = [item for item in observations if item["date"] >= start_date.isoformat()]
    if not selected:
        raise YieldCurveUnavailable(f"No Treasury observations are available for {period}")

    comparison_targets = (
        ("latest", "Latest", latest_date),
        ("1m", "1M ago", latest_date - timedelta(days=30)),
        ("3m", "3M ago", latest_date - timedelta(days=90)),
        ("1y", "1Y ago", latest_date - timedelta(days=365)),
    )
    snapshots = []
    for key, label, target in comparison_targets:
        observation = latest if key == "latest" else _nearest_on_or_before(observations, target)
        if observation is not None:
            snapshots.append({"key": key, "label": label, **observation})

    warnings: list[str] = []
    expected_date = latest_completed_us_session(now.date())
    provider_stale = latest_date < expected_date
    if provider_stale:
        warnings.append(
            f"Latest Treasury observation is {latest_date.isoformat()}; "
            f"{expected_date.isoformat()} was expected."
        )
    if refresh_failed:
        warnings.append("Treasury refresh failed; showing the last cached observations.")

    return {
        "meta": {
            "period": period,
            "as_of_date": latest["date"],
            "fetched_at": fetched_at.isoformat(),
            "source_name": TREASURY_SOURCE_NAME,
            "provider_name": provider_name,
            "source_url": TREASURY_SOURCE_URL,
            "stale": provider_stale or refresh_failed,
            "warnings": warnings,
        },
        "maturities": [
            {key: value for key, value in maturity.items() if key != "csv_column"}
            for maturity in MATURITIES
        ],
        "latest": latest,
        "snapshots": snapshots,
        "observations": selected,
    }


async def get_yield_curve(
    period: Literal["1y", "3y", "5y"] = "1y",
    *,
    now: Optional[datetime] = None,
    client: Optional[httpx.AsyncClient] = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Return official curve snapshots and history, with a resilient disk cache."""
    if period not in PERIOD_YEARS:
        raise ValueError(f"Unsupported yield-curve period: {period}")
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)

    cache = await asyncio.to_thread(_read_cache)
    refresh_failed = False
    if force_refresh or not _cache_is_fresh(cache, current_time):
        async with _refresh_lock:
            cache = await asyncio.to_thread(_read_cache)
            if force_refresh or not _cache_is_fresh(cache, current_time):
                try:
                    observations, provider_name = await fetch_treasury_observations(
                        current_time.date(), client=client
                    )
                    cache = {
                        "schema_version": TREASURY_CACHE_SCHEMA_VERSION,
                        "fetched_at": current_time.isoformat(),
                        "provider_name": provider_name,
                        "observations": observations,
                    }
                    try:
                        await asyncio.to_thread(_write_cache, cache)
                    except OSError:
                        logger.warning("Could not persist the Treasury yield-curve cache")
                except (httpx.HTTPError, YieldCurveUnavailable):
                    if cache is None:
                        raise YieldCurveUnavailable(
                            "U.S. Treasury yield-curve data is temporarily unavailable"
                        )
                    refresh_failed = True

    if cache is None:
        raise YieldCurveUnavailable("U.S. Treasury yield-curve data is unavailable")
    fetched_at = _parsed_fetched_at(cache) or current_time
    return build_yield_curve_payload(
        cache["observations"],
        period,
        now=current_time,
        fetched_at=fetched_at,
        provider_name=str(cache.get("provider_name") or "U.S. Treasury"),
        refresh_failed=refresh_failed,
    )
