from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Optional

from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from models import CorporateAction


def _decimal(value: Any) -> Optional[Decimal]:
    try:
        return Decimal(str(value)) if value not in (None, "") else None
    except (InvalidOperation, ValueError):
        return None


def _parse_date(value: Any):
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def _parse_split_factor(value: Any) -> Optional[Decimal]:
    if value in (None, ""):
        return None
    text = str(value)
    if "/" in text:
        numerator, denominator = text.split("/", 1)
        numerator_value = _decimal(numerator)
        denominator_value = _decimal(denominator)
        if (
            numerator_value is None
            or denominator_value is None
            or numerator_value <= 0
            or denominator_value <= 0
        ):
            return None
        return numerator_value / denominator_value
    factor = _decimal(value)
    return factor if factor is not None and factor > 0 else None


async def upsert_corporate_actions(
    db: AsyncSession,
    ticker: str,
    splits: Iterable[dict],
    dividends: Iterable[dict],
) -> int:
    rows = []
    for item in splits or []:
        try:
            ex_date = _parse_date(item.get("date"))
        except (TypeError, ValueError):
            continue
        split_factor = _parse_split_factor(item.get("split") or item.get("split_factor"))
        if split_factor is None:
            continue
        rows.append({
            "ticker": ticker,
            "ex_date": ex_date,
            "action_type": "split",
            "split_factor": split_factor,
            "cash_amount": None,
            "currency": None,
            "available_at": datetime.combine(ex_date, datetime.min.time()),
            "source": "EODHD",
            "source_id": str(item.get("id") or f"split-{ticker}-{ex_date}"),
        })
    for item in dividends or []:
        try:
            ex_date = _parse_date(item.get("date"))
        except (TypeError, ValueError):
            continue
        rows.append({
            "ticker": ticker,
            "ex_date": ex_date,
            "action_type": "dividend",
            "split_factor": None,
            "cash_amount": _decimal(item.get("value") or item.get("dividend")),
            "currency": item.get("currency"),
            "available_at": datetime.combine(ex_date, datetime.min.time()),
            "source": "EODHD",
            "source_id": str(item.get("id") or f"dividend-{ticker}-{ex_date}"),
        })
    if not rows:
        return 0
    stmt = insert(CorporateAction).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["ticker", "ex_date", "action_type", "source_id"],
        set_={
            "split_factor": stmt.excluded.split_factor,
            "cash_amount": stmt.excluded.cash_amount,
            "currency": stmt.excluded.currency,
            "available_at": stmt.excluded.available_at,
        },
    )
    await db.execute(stmt)
    return len(rows)
