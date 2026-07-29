"""Validated metadata-driven screener queries."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, asc, desc, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.trading_calendar import is_us_market_session, latest_completed_us_session
from models import DataPublication, PipelineRun, StockScreenerSnapshot, UniverseMembership
from services.screener_fields import (
    DEFAULT_COLUMNS,
    FIELD_DEFINITIONS,
    FIELD_MAP,
    MODEL_FIELD_MAP,
    SUPPORTED_FINVIZ_FIELDS,
)


def _serialize(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


def _freshness(as_of_date: date) -> dict[str, Any]:
    latest_session = latest_completed_us_session(date.today())
    cursor = as_of_date
    lag_sessions = 0
    while cursor < latest_session:
        cursor += timedelta(days=1)
        if is_us_market_session(cursor):
            lag_sessions += 1
    return {
        "status": "current" if lag_sessions <= 1 else "stale",
        "lag_sessions": lag_sessions,
        "latest_completed_session": latest_session.isoformat(),
    }


async def latest_published_screener_date(db: AsyncSession) -> date | None:
    result = await db.execute(
        select(DataPublication.as_of_date)
        .join(PipelineRun, PipelineRun.id == DataPublication.pipeline_run_id)
        .where(
            DataPublication.dataset == "screener",
            DataPublication.status == "published",
            PipelineRun.status == "published",
        )
        .order_by(DataPublication.as_of_date.desc())
        .limit(1)
    )
    published = result.scalar_one_or_none()
    if published is not None:
        return published
    fallback = await db.execute(select(func.max(StockScreenerSnapshot.date)))
    return fallback.scalar_one_or_none()


async def get_screener_metadata(db: AsyncSession) -> dict[str, Any]:
    selected_date = await latest_published_screener_date(db)
    total = 0
    coverage: dict[str, float] = {}
    enum_options: dict[str, list[dict[str, str]]] = {}
    if selected_date is not None:
        total_result = await db.execute(
            select(func.count(StockScreenerSnapshot.id)).where(
                StockScreenerSnapshot.date == selected_date
            )
        )
        total = total_result.scalar_one() or 0
        if total:
            for definition in FIELD_DEFINITIONS:
                column = MODEL_FIELD_MAP.get(definition.id)
                if column is not None:
                    count_result = await db.execute(
                        select(func.count(column)).where(StockScreenerSnapshot.date == selected_date)
                    )
                    coverage[definition.id] = (count_result.scalar_one() or 0) / total
                else:
                    coverage[definition.id] = 1.0 if definition.id == "index" else 0.0
            for field_id in ("exchange", "sector", "industry", "country", "candlestick"):
                column = MODEL_FIELD_MAP.get(field_id)
                if column is None:
                    continue
                values_result = await db.execute(
                    select(column)
                    .where(StockScreenerSnapshot.date == selected_date, column.is_not(None))
                    .distinct()
                    .order_by(column.asc())
                )
                enum_options[field_id] = [
                    {"value": str(value), "label": str(value)}
                    for value in values_result.scalars().all()
                    if value not in (None, "")
                ]

    fields = []
    for definition in FIELD_DEFINITIONS:
        metadata = definition.metadata()
        if definition.id in enum_options:
            metadata["options"] = enum_options[definition.id]
        metadata["coverage"] = coverage.get(definition.id, 0.0)
        metadata["available"] = metadata["coverage"] > 0
        fields.append(metadata)
    return {
        "as_of_date": selected_date.isoformat() if selected_date else None,
        "freshness": _freshness(selected_date) if selected_date else None,
        "universe": "SP500_RUSSELL2000",
        "record_count": total,
        "supported_finviz_fields": SUPPORTED_FINVIZ_FIELDS,
        "fields": fields,
        "default_columns": DEFAULT_COLUMNS,
    }


def _condition_for(column: Any, operator: str, value: Any) -> Any:
    if operator == "eq":
        return column == value
    if operator == "in":
        if not isinstance(value, list) or not value:
            raise ValueError("in operator requires a non-empty list")
        return column.in_(value)
    if operator == "lt":
        return column < value
    if operator == "lte":
        return column <= value
    if operator == "gt":
        return column > value
    if operator == "gte":
        return column >= value
    if operator == "between":
        if not isinstance(value, list) or len(value) != 2:
            raise ValueError("between operator requires [minimum, maximum]")
        return column.between(value[0], value[1])
    raise ValueError(f"unsupported operator: {operator}")


def _index_condition(selected_date: date, operator: str, value: Any) -> Any:
    universes = value if operator == "in" else [value]
    if operator not in {"eq", "in"}:
        raise ValueError("index only supports eq/in")
    if not universes or any(universe not in {"SP500", "RUSSELL2000"} for universe in universes):
        raise ValueError("unsupported index value")
    return exists(
        select(UniverseMembership.id).where(
            UniverseMembership.ticker == StockScreenerSnapshot.ticker,
            UniverseMembership.universe.in_(universes),
            UniverseMembership.effective_from <= selected_date,
            or_(
                UniverseMembership.effective_to.is_(None),
                UniverseMembership.effective_to >= selected_date,
            ),
        )
    )


async def query_screener(request_data: dict[str, Any], db: AsyncSession) -> dict[str, Any]:
    requested_date = request_data.get("as_of_date")
    selected_date = requested_date or await latest_published_screener_date(db)
    if selected_date is None:
        return {
            "total": 0,
            "items": [],
            "limit": request_data.get("limit", 50),
            "offset": request_data.get("offset", 0),
            "as_of_date": None,
            "freshness": None,
        }
    if isinstance(selected_date, str):
        selected_date = date.fromisoformat(selected_date)

    publication_result = await db.execute(
        select(DataPublication.id)
        .join(PipelineRun, PipelineRun.id == DataPublication.pipeline_run_id)
        .where(
            DataPublication.dataset == "screener",
            DataPublication.as_of_date == selected_date,
            DataPublication.status == "published",
            PipelineRun.status == "published",
        )
    )
    if publication_result.scalar_one_or_none() is None:
        # Preserve the legacy fallback only for pre-publication-tracking latest data.
        latest = await latest_published_screener_date(db)
        if requested_date is not None or latest != selected_date:
            return {
                "total": 0,
                "items": [],
                "limit": request_data.get("limit", 50),
                "offset": request_data.get("offset", 0),
                "as_of_date": selected_date.isoformat(),
                "freshness": _freshness(selected_date),
            }

    requested_columns = request_data.get("columns") or DEFAULT_COLUMNS
    columns = list(dict.fromkeys(["ticker", "name", *requested_columns]))
    if len(columns) > 32:
        raise ValueError("at most 32 result columns are allowed")
    selectable = {
        "ticker": StockScreenerSnapshot.ticker,
        "name": StockScreenerSnapshot.name,
        **MODEL_FIELD_MAP,
    }
    invalid_columns = [column for column in columns if column not in selectable]
    if invalid_columns:
        raise ValueError("unsupported result columns: " + ", ".join(invalid_columns))

    conditions = [StockScreenerSnapshot.date == selected_date]
    for clause in request_data.get("filters") or []:
        field_id = clause["field"]
        operator = clause["operator"]
        value = clause.get("value")
        definition = FIELD_MAP.get(field_id)
        if definition is None or operator not in definition.operators:
            raise ValueError(f"unsupported filter: {field_id}/{operator}")
        if field_id == "index":
            conditions.append(_index_condition(selected_date, operator, value))
            continue
        column = MODEL_FIELD_MAP.get(field_id)
        if column is None:
            raise ValueError(f"field is not queryable: {field_id}")
        conditions.append(_condition_for(column, operator, value))

    count_stmt = select(func.count(StockScreenerSnapshot.id)).where(and_(*conditions))
    total_result = await db.execute(count_stmt)
    total = total_result.scalar_one() or 0

    sort_request = request_data.get("sort") or {"field": "market_cap", "direction": "desc"}
    sort_field = sort_request.get("field", "market_cap")
    sort_column = selectable.get(sort_field)
    if sort_column is None:
        raise ValueError(f"unsupported sort field: {sort_field}")
    order = desc(sort_column).nulls_last() if sort_request.get("direction") == "desc" else asc(sort_column).nulls_last()
    selected_expressions = [selectable[column].label(column) for column in columns]
    stmt = (
        select(*selected_expressions)
        .where(and_(*conditions))
        .order_by(order, StockScreenerSnapshot.ticker.asc())
        .limit(request_data.get("limit", 50))
        .offset(request_data.get("offset", 0))
    )
    result = await db.execute(stmt)
    items = [
        {key: _serialize(value) for key, value in row._mapping.items()}
        for row in result.all()
    ]
    return {
        "total": total,
        "items": items,
        "limit": request_data.get("limit", 50),
        "offset": request_data.get("offset", 0),
        "as_of_date": selected_date.isoformat(),
        "freshness": _freshness(selected_date),
    }
