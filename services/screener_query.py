"""Validated metadata-driven screener queries."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
import math
from typing import Any

from sqlalchemy import and_, asc, case, desc, exists, func, or_, select
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
from services.screener_normalization import (
    HIGH_DISTANCE_FIELDS,
    LOW_DISTANCE_FIELDS,
    NONNEGATIVE_VALUE_FIELDS,
    NON_PRIMARY_EXCHANGES,
    NON_PRIMARY_EXCHANGE_PREFIXES,
    POSITIVE_MULTIPLE_FIELDS,
    POSITIVE_VALUE_FIELDS,
    PROVIDER_MULTIPLE_SENTINEL,
    RETURN_FIELDS,
    SENTINEL_CAPPED_MULTIPLE_FIELDS,
)
from services.universe import (
    LIVE_UNIVERSE_SOURCE,
    SCREENER_INDEX_OPTIONS,
    SCREENER_MEMBERSHIP_UNIVERSES,
    SCREENER_UNIVERSE,
    RUSSELL3000_UNIVERSE,
)


def _primary_listing_condition() -> Any:
    exchange = func.upper(func.trim(StockScreenerSnapshot.exchange))
    return and_(
        exchange.is_not(None),
        exchange != "",
        exchange.not_in(NON_PRIMARY_EXCHANGES),
        *(~exchange.like(f"{prefix}%") for prefix in NON_PRIMARY_EXCHANGE_PREFIXES),
    )


def _live_index_membership_condition(selected_date: date) -> Any:
    """Limit the public screener to the live indexes named by the product UI."""
    return exists(
        select(UniverseMembership.id).where(
            UniverseMembership.ticker == StockScreenerSnapshot.ticker,
            UniverseMembership.universe.in_(SCREENER_MEMBERSHIP_UNIVERSES),
            UniverseMembership.source == LIVE_UNIVERSE_SOURCE,
            UniverseMembership.effective_from <= selected_date,
            or_(
                UniverseMembership.effective_to.is_(None),
                UniverseMembership.effective_to >= selected_date,
            ),
        )
    )


async def _has_live_index_membership(db: AsyncSession, selected_date: date) -> bool:
    """Keep pre-membership legacy snapshots queryable while gating new snapshots."""
    result = await db.execute(
        select(UniverseMembership.id)
        .where(
            UniverseMembership.universe.in_(SCREENER_MEMBERSHIP_UNIVERSES),
            UniverseMembership.source == LIVE_UNIVERSE_SOURCE,
            UniverseMembership.effective_from <= selected_date,
            or_(
                UniverseMembership.effective_to.is_(None),
                UniverseMembership.effective_to >= selected_date,
            ),
        )
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


def _canonical_field_expression(field_id: str, column: Any) -> Any:
    conditions = []
    if field_id in POSITIVE_MULTIPLE_FIELDS or field_id in POSITIVE_VALUE_FIELDS:
        conditions.append(column > 0)
    if field_id in SENTINEL_CAPPED_MULTIPLE_FIELDS:
        conditions.append(column < PROVIDER_MULTIPLE_SENTINEL)
    if field_id in NONNEGATIVE_VALUE_FIELDS or field_id in LOW_DISTANCE_FIELDS:
        conditions.append(column >= 0)
    if field_id in RETURN_FIELDS:
        conditions.append(column >= -1)
    if field_id in HIGH_DISTANCE_FIELDS:
        conditions.extend((column >= -1, column <= 0))
    if field_id == "analyst_recommendation":
        conditions.extend((column >= 1, column <= 5))
    if field_id == "rsi_14":
        conditions.extend((column >= 0, column <= 100))
    if field_id == "roe":
        conditions.append(
            or_(
                StockScreenerSnapshot.pb_ratio.is_(None),
                StockScreenerSnapshot.pb_ratio > 0,
            )
        )
    if field_id == "payout_ratio":
        conditions.append(
            or_(
                StockScreenerSnapshot.pe_ratio.is_(None),
                StockScreenerSnapshot.pe_ratio > 0,
            )
        )
    if field_id in {"gross_margin", "operating_margin", "net_profit_margin"}:
        conditions.append(
            or_(
                StockScreenerSnapshot.ps_ratio.is_(None),
                and_(
                    StockScreenerSnapshot.ps_ratio > 0,
                    StockScreenerSnapshot.ps_ratio < PROVIDER_MULTIPLE_SENTINEL,
                ),
            )
        )
    return case((and_(*conditions), column), else_=None) if conditions else column


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
    served_universe = RUSSELL3000_UNIVERSE
    coverage: dict[str, float] = {}
    enum_options: dict[str, list[dict[str, str]]] = {}
    if selected_date is not None:
        restrict_to_live_index = await _has_live_index_membership(db, selected_date)
        snapshot_conditions = [
            StockScreenerSnapshot.date == selected_date,
            _primary_listing_condition(),
        ]
        if restrict_to_live_index:
            snapshot_conditions.append(_live_index_membership_condition(selected_date))
        aggregate_expressions = [func.count(StockScreenerSnapshot.id).label("_total")]
        for definition in FIELD_DEFINITIONS:
            column = MODEL_FIELD_MAP.get(definition.id)
            if column is not None:
                aggregate_expressions.append(
                    func.count(
                        _canonical_field_expression(definition.id, column)
                    ).label(definition.id)
                )
            elif definition.id.startswith("price_vs_ma"):
                close_expression, ma_expression = _price_vs_ma_expressions(
                    definition.id
                )
                aggregate_expressions.append(
                    func.count(case((
                        and_(
                            close_expression.is_not(None),
                            ma_expression.is_not(None),
                        ),
                        1,
                    ))).label(definition.id)
                )
        aggregate_result = await db.execute(
            select(*aggregate_expressions).where(*snapshot_conditions)
        )
        aggregate_row = aggregate_result.one()._mapping
        total = aggregate_row["_total"] or 0
        if total:
            for definition in FIELD_DEFINITIONS:
                if definition.id in aggregate_row:
                    coverage[definition.id] = (aggregate_row[definition.id] or 0) / total
                elif definition.id != "index":
                    coverage[definition.id] = 0.0
            membership_filter = (
                UniverseMembership.universe.in_(SCREENER_MEMBERSHIP_UNIVERSES),
                UniverseMembership.source == LIVE_UNIVERSE_SOURCE,
                UniverseMembership.effective_from <= selected_date,
                or_(
                    UniverseMembership.effective_to.is_(None),
                    UniverseMembership.effective_to >= selected_date,
                ),
                UniverseMembership.ticker.in_(
                    select(StockScreenerSnapshot.ticker).where(
                        StockScreenerSnapshot.date == selected_date,
                        _primary_listing_condition(),
                    )
                ),
            )
            membership_counts_result = await db.execute(
                select(
                    UniverseMembership.universe,
                    func.count(func.distinct(UniverseMembership.ticker)),
                )
                .where(*membership_filter)
                .group_by(UniverseMembership.universe)
            )
            membership_counts = dict(membership_counts_result.all())
            if membership_counts.get("NASDAQ100", 0) > 0:
                served_universe = SCREENER_UNIVERSE
            index_ticker_count_result = await db.execute(
                select(func.count(func.distinct(UniverseMembership.ticker))).where(*membership_filter)
            )
            coverage["index"] = (index_ticker_count_result.scalar_one() or 0) / total
            enum_options["index"] = [
                {"value": universe, "label": label}
                for universe, label in SCREENER_INDEX_OPTIONS
                if membership_counts.get(universe, 0) > 0
            ]
            for field_id in ("exchange", "sector", "industry", "country", "candlestick"):
                column = MODEL_FIELD_MAP.get(field_id)
                if column is None:
                    continue
                values_result = await db.execute(
                    select(column)
                    .where(
                        *snapshot_conditions,
                        column.is_not(None),
                    )
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
        "universe": served_universe,
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


def _coerce_filter_value(field_type: str, operator: str, value: Any) -> Any:
    if value is None:
        raise ValueError("filter value cannot be null")

    if field_type == "enum":
        values = value if operator == "in" else [value]
        if operator == "in" and (not isinstance(value, list) or not value):
            raise ValueError("in operator requires a non-empty list")
        if operator == "in" and len(value) > 100:
            raise ValueError("in operator accepts at most 100 values")
        if operator != "in" and isinstance(value, list):
            raise ValueError(f"{operator} operator requires a scalar value")
        if any(not isinstance(item, str) or not item for item in values):
            raise ValueError("enum filters require non-empty string values")
        return value

    if field_type == "number":
        values = value if operator == "between" else [value]
        if operator == "between" and (not isinstance(value, list) or len(value) != 2):
            raise ValueError("between operator requires [minimum, maximum]")
        if operator != "between" and isinstance(value, (list, dict)):
            raise ValueError(f"{operator} operator requires a scalar value")

        def parse_number(item: Any) -> float:
            if isinstance(item, bool):
                raise ValueError("numeric filters do not accept booleans")
            try:
                parsed = float(item)
            except (TypeError, ValueError) as exc:
                raise ValueError("numeric filters require finite numbers") from exc
            if not math.isfinite(parsed):
                raise ValueError("numeric filters require finite numbers")
            return parsed

        parsed_values = [parse_number(item) for item in values]
        return parsed_values if operator == "between" else parsed_values[0]

    if field_type != "date":
        raise ValueError(f"unsupported field type: {field_type}")

    def parse_date(item: Any) -> date:
        if isinstance(item, date):
            return item
        if not isinstance(item, str):
            raise ValueError("date filters require ISO date values")
        try:
            return date.fromisoformat(item)
        except ValueError as exc:
            raise ValueError(f"invalid ISO date: {item}") from exc

    if operator == "between":
        if not isinstance(value, list) or len(value) != 2:
            raise ValueError("between operator requires [minimum, maximum]")
        return [parse_date(item) for item in value]
    if isinstance(value, (list, dict)):
        raise ValueError(f"{operator} operator requires a scalar value")
    return parse_date(value)


def _index_condition(selected_date: date, operator: str, value: Any) -> Any:
    universes = value if operator == "in" else [value]
    if operator not in {"eq", "in"}:
        raise ValueError("index only supports eq/in")
    if not universes or any(universe not in SCREENER_MEMBERSHIP_UNIVERSES for universe in universes):
        raise ValueError("unsupported index value")
    return exists(
        select(UniverseMembership.id).where(
            UniverseMembership.ticker == StockScreenerSnapshot.ticker,
            UniverseMembership.universe.in_(universes),
            UniverseMembership.source == LIVE_UNIVERSE_SOURCE,
            UniverseMembership.effective_from <= selected_date,
            or_(
                UniverseMembership.effective_to.is_(None),
                UniverseMembership.effective_to >= selected_date,
            ),
        )
    )


def _price_vs_ma_expressions(field_id: str) -> tuple[Any, Any]:
    ma_field_id = field_id.removeprefix("price_vs_")
    return (
        _canonical_field_expression("close", StockScreenerSnapshot.close),
        _canonical_field_expression(
            ma_field_id,
            getattr(StockScreenerSnapshot, ma_field_id),
        ),
    )


def _price_vs_ma_condition(field_id: str, operator: str, value: Any) -> Any:
    values = value if operator == "in" else [value]
    if operator not in {"eq", "in"}:
        raise ValueError("price-versus-SMA filters only support eq/in")
    if not values or any(item not in {"above", "below"} for item in values):
        raise ValueError("unsupported price-versus-SMA value")
    close_expression, ma_expression = _price_vs_ma_expressions(field_id)
    comparisons = [
        close_expression > ma_expression if item == "above"
        else close_expression < ma_expression
        for item in values
    ]
    return or_(*comparisons)


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
        if latest != selected_date:
            return {
                "total": 0,
                "items": [],
                "limit": request_data.get("limit", 50),
                "offset": request_data.get("offset", 0),
                "as_of_date": selected_date.isoformat(),
                "freshness": None,
            }

    requested_columns = (
        request_data["columns"]
        if "columns" in request_data
        else DEFAULT_COLUMNS
    )
    optional_columns = [
        column
        for column in dict.fromkeys(requested_columns)
        if column not in {"ticker", "name"}
    ]
    if len(optional_columns) > 30:
        raise ValueError("at most 30 optional result columns are allowed")
    columns = list(dict.fromkeys(["ticker", "name", *requested_columns]))
    selectable = {
        "ticker": StockScreenerSnapshot.ticker,
        "name": StockScreenerSnapshot.name,
        **{
            field_id: _canonical_field_expression(field_id, column)
            for field_id, column in MODEL_FIELD_MAP.items()
        },
    }
    invalid_columns = [column for column in columns if column not in selectable]
    if invalid_columns:
        raise ValueError("unsupported result columns: " + ", ".join(invalid_columns))

    conditions = [
        StockScreenerSnapshot.date == selected_date,
        _primary_listing_condition(),
    ]
    if await _has_live_index_membership(db, selected_date):
        conditions.append(_live_index_membership_condition(selected_date))
    for clause in request_data.get("filters") or []:
        field_id = clause["field"]
        operator = clause["operator"]
        value = clause.get("value")
        definition = FIELD_MAP.get(field_id)
        if definition is None or operator not in definition.operators:
            raise ValueError(f"unsupported filter: {field_id}/{operator}")
        value = _coerce_filter_value(definition.type, operator, value)
        if field_id == "index":
            conditions.append(_index_condition(selected_date, operator, value))
            continue
        if field_id.startswith("price_vs_ma"):
            conditions.append(_price_vs_ma_condition(field_id, operator, value))
            continue
        column = selectable.get(field_id)
        if column is None:
            raise ValueError(f"field is not queryable: {field_id}")
        conditions.append(
            _condition_for(
                column,
                operator,
                value,
            )
        )

    count_stmt = select(func.count(StockScreenerSnapshot.id)).where(and_(*conditions))
    total_result = await db.execute(count_stmt)
    total = total_result.scalar_one() or 0

    sort_request = request_data.get("sort") or {"field": "market_cap", "direction": "desc"}
    sort_field = sort_request.get("field", "market_cap")
    sort_column = selectable.get(sort_field)
    if sort_column is None:
        raise ValueError(f"unsupported sort field: {sort_field}")
    order = (
        desc(sort_column).nulls_last()
        if sort_request.get("direction", "desc") == "desc"
        else asc(sort_column).nulls_last()
    )
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
