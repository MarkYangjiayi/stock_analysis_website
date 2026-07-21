from datetime import date
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from models import SecurityMaster, SymbolHistory
from core.time_utils import utc_now


US_EXCHANGES = {"NASDAQ", "NYSE", "NYSE ARCA", "AMEX", "BATS", "US"}


def canonicalize_ticker(symbol: str, exchange: Optional[str] = None) -> str:
    value = symbol.strip().upper()
    if not value:
        raise ValueError("Ticker cannot be empty")
    if "." in value:
        return value
    if not exchange or exchange.strip().upper() in US_EXCHANGES:
        return f"{value}.US"
    return f"{value}.{exchange.strip().upper().replace(' ', '_')}"


async def upsert_security(
    db: AsyncSession,
    symbol: str,
    exchange: Optional[str] = None,
    name: Optional[str] = None,
    asset_type: Optional[str] = None,
    currency: Optional[str] = None,
    observed_date: Optional[date] = None,
) -> SecurityMaster:
    canonical = canonicalize_ticker(symbol, exchange)
    now = utc_now()
    stmt = insert(SecurityMaster).values(
        canonical_ticker=canonical,
        name=name,
        exchange=exchange,
        asset_type=asset_type,
        currency=currency,
        is_active=True,
        first_seen_at=now,
        last_seen_at=now,
    ).on_conflict_do_update(
        index_elements=["canonical_ticker"],
        set_={
            "name": name,
            "exchange": exchange,
            "asset_type": asset_type,
            "currency": currency,
            "is_active": True,
            "last_seen_at": now,
        },
    )
    await db.execute(stmt)
    result = await db.execute(select(SecurityMaster).where(SecurityMaster.canonical_ticker == canonical))
    security = result.scalar_one()

    history_stmt = insert(SymbolHistory).values(
        security_id=security.id,
        symbol=symbol.strip().upper(),
        exchange=exchange,
        valid_from=observed_date or date.today(),
        source="EODHD",
    ).on_conflict_do_nothing(
        index_elements=["security_id", "symbol", "valid_from"],
    )
    await db.execute(history_stmt)
    return security


async def bulk_upsert_securities(db: AsyncSession, records: Iterable[dict], observed_date: date) -> None:
    values_by_canonical = {}
    symbol_rows = []
    now = utc_now()
    for record in records:
        ticker = record.get("ticker")
        if not ticker:
            continue
        canonical = canonicalize_ticker(ticker, record.get("exchange"))
        values_by_canonical[canonical] = {
            "canonical_ticker": canonical,
            "name": record.get("name"),
            "exchange": record.get("exchange"),
            "asset_type": record.get("asset_type") or "Common Stock",
            "currency": record.get("currency") or "USD",
            "is_active": True,
            "first_seen_at": now,
            "last_seen_at": now,
        }
        symbol_rows.append((canonical, ticker.strip().upper(), record.get("exchange")))
    values = list(values_by_canonical.values())
    if not values:
        return
    stmt = insert(SecurityMaster).values(values)
    stmt = stmt.on_conflict_do_update(
        index_elements=["canonical_ticker"],
        set_={
            "name": stmt.excluded.name,
            "exchange": stmt.excluded.exchange,
            "asset_type": stmt.excluded.asset_type,
            "currency": stmt.excluded.currency,
            "is_active": True,
            "last_seen_at": now,
        },
    )
    await db.execute(stmt)

    canonicals = [value["canonical_ticker"] for value in values]
    result = await db.execute(
        select(SecurityMaster.id, SecurityMaster.canonical_ticker).where(SecurityMaster.canonical_ticker.in_(canonicals))
    )
    id_map = {canonical: security_id for security_id, canonical in result.all()}
    history_values = [
        {
            "security_id": id_map[canonical],
            "symbol": symbol,
            "exchange": exchange,
            "valid_from": observed_date,
            "source": "EODHD",
        }
        for canonical, symbol, exchange in symbol_rows
        if canonical in id_map
    ]
    if history_values:
        await db.execute(
            insert(SymbolHistory).values(history_values).on_conflict_do_nothing(
                index_elements=["security_id", "symbol", "valid_from"]
            )
        )
