from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
import uuid
from datetime import date, datetime, timedelta
from io import StringIO
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.time_utils import utc_now
from database import async_session_maker
from models import FinancialFlowRun, FinancialStatement, Ticker
from services.earnings_quality import (
    get_earnings_quality,
    get_statement_for_period,
    is_financial_company,
    statement_fingerprint,
)
from services.filing_analysis import _fetch_sec_documents_sync
from services.raw_store import persist_snapshot
from services.security_master import canonicalize_ticker


logger = logging.getLogger(__name__)
FINANCIAL_FLOW_SCHEMA_VERSION = "financial-flow-v1"
ACTIVE_STATUSES = ("queued", "running")
GLOBAL_SLOT = "financial-flow-sec"
_OWNER_TOKEN = uuid.uuid4().hex
_tasks: dict[int, asyncio.Task[None]] = {}
_recovery_task: asyncio.Task[None] | None = None
_semaphore = asyncio.Semaphore(max(1, settings.FINANCIAL_FLOW_MAX_CONCURRENCY))


class FinancialFlowError(RuntimeError):
    pass


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _first(mapping: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _number(mapping.get(key))
        if value is not None:
            return value
    return None


def _source_node(
    node_id: str,
    label: str,
    value: float,
    kind: str,
    *,
    source_id: str,
    evidence_type: str = "fact_provider_standardized",
    original_label: str | None = None,
) -> dict[str, Any]:
    return {
        "id": node_id,
        "label": label,
        "value": value,
        "kind": kind,
        "source_id": source_id,
        "evidence_type": evidence_type,
        "confidence": "high" if evidence_type == "fact_source_reported" else "medium",
        "original_label": original_label or label,
    }


def _pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return current / previous - 1


def _reconciles(total: float | None, parts: Iterable[float | None]) -> bool:
    values = list(parts)
    if total is None or any(value is None for value in values):
        return False
    tolerance = max(1.0, abs(total) * 0.005)
    return abs(total - sum(value for value in values if value is not None)) <= tolerance


def _summary_card(
    key: str,
    label: str,
    value: float | None,
    previous: float | None,
    revenue: float | None,
    *,
    note: str | None = None,
) -> dict[str, Any]:
    margin = value / revenue if value is not None and revenue not in (None, 0) else None
    yoy = _pct_change(value, previous)
    if note is None and yoy is not None:
        note = f"{abs(yoy) * 100:.1f}% {'increase' if yoy >= 0 else 'decrease'} year over year"
    return {
        "key": key,
        "label": label,
        "value": value,
        "yoy_change": yoy,
        "margin": margin,
        "note": note,
    }


def _statement_values(statement: FinancialStatement) -> dict[str, float | None]:
    income = statement.income_statement or {}
    revenue = _first(income, "totalRevenue")
    if revenue is None:
        revenue = _number(statement.revenue)
    gross_profit = _first(income, "grossProfit")
    cost_of_revenue = _first(income, "costOfRevenue", "costOfSales")
    if gross_profit is None and revenue is not None and cost_of_revenue is not None:
        gross_profit = revenue - cost_of_revenue
    if cost_of_revenue is None and revenue is not None and gross_profit is not None:
        cost_of_revenue = revenue - gross_profit
    operating_income = _first(income, "operatingIncome", "incomeFromOperations")
    pretax = _first(income, "incomeBeforeTax", "incomeBeforeTaxExpense")
    tax = _first(income, "incomeTaxExpense", "taxProvision")
    net_income = _first(income, "netIncome")
    if net_income is None:
        net_income = _number(statement.net_income)
    return {
        "revenue": revenue,
        "cost_of_revenue": cost_of_revenue,
        "gross_profit": gross_profit,
        "operating_income": operating_income,
        "operating_expenses": (
            gross_profit - operating_income
            if gross_profit is not None and operating_income is not None
            else None
        ),
        "interest_income": _first(income, "interestIncome"),
        "interest_expense": _first(income, "interestExpense"),
        "pretax_income": pretax,
        "income_tax": tax,
        "net_income": net_income,
        "net_non_operating": (
            pretax - operating_income
            if pretax is not None and operating_income is not None
            else _first(income, "totalOtherIncomeExpenseNet", "nonOperatingIncomeNetOther")
        ),
    }


def build_consolidated_flow(
    statement: FinancialStatement,
    *,
    previous_statement: FinancialStatement | None = None,
    currency: str | None = None,
) -> dict[str, Any]:
    values = _statement_values(statement)
    income = statement.income_statement or {}
    gross_profit_derived = _first(income, "grossProfit") is None and values["gross_profit"] is not None
    cost_of_revenue_derived = _first(income, "costOfRevenue", "costOfSales") is None and values["cost_of_revenue"] is not None
    previous = _statement_values(previous_statement) if previous_statement else {}
    source_id = f"EODHD:{statement.ticker}:{statement.period}:{statement.fiscal_date.isoformat()}"
    warnings: list[dict[str, str]] = []
    required_keys = (
        "revenue", "cost_of_revenue", "gross_profit", "operating_expenses",
        "operating_income", "pretax_income", "income_tax", "net_income",
    )
    missing = [key for key in required_keys if values[key] is None]
    if missing:
        warnings.append({
            "code": "missing_required_source",
            "message": "Missing required reported fields: " + ", ".join(missing),
        })

    required = [values[key] for key in required_keys]
    nonop = values["net_non_operating"]
    reconciliations = {
        "gross_profit": _reconciles(values["revenue"], [values["cost_of_revenue"], values["gross_profit"]]),
        "operating_income": _reconciles(values["gross_profit"], [values["operating_expenses"], values["operating_income"]]),
        "pretax_income": (
            _reconciles(values["pretax_income"], [values["operating_income"], nonop])
            if nonop is not None and nonop >= 0
            else _reconciles(values["operating_income"], [values["pretax_income"], abs(nonop) if nonop is not None else None])
        ),
        "net_income": _reconciles(values["pretax_income"], [values["income_tax"], values["net_income"]]),
    }
    chart_available = all(value is not None and value >= 0 for value in required) and all(reconciliations.values())
    if not chart_available and not missing:
        if any(value is not None and value < 0 for value in required):
            warnings.append({
                "code": "negative_stage_not_supported",
                "message": "One or more profit stages are negative; the reported values remain available in the table.",
            })
        if not all(reconciliations.values()):
            warnings.append({
                "code": "reconciliation_failed",
                "message": "Reported stages do not reconcile within the greater of one reporting unit or 0.5%; the Sankey chart is hidden.",
            })

    nodes: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    node_ids: set[str] = set()

    def add(node_id: str, label: str, value: float | None, kind: str, *, derived: bool = False) -> None:
        if value is None or node_id in node_ids:
            return
        node_ids.add(node_id)
        nodes.append(_source_node(
            node_id,
            label,
            value,
            kind,
            source_id=source_id,
            evidence_type="derived_calculation" if derived else "fact_provider_standardized",
        ))

    add("revenue", "Total revenue", values["revenue"], "income")
    add("cost_of_revenue", "Cost of revenue", values["cost_of_revenue"], "expense", derived=cost_of_revenue_derived)
    add("gross_profit", "Gross profit", values["gross_profit"], "profit", derived=gross_profit_derived)
    add("operating_expenses", "Operating expenses", values["operating_expenses"], "expense", derived=True)
    add("operating_income", "Operating income", values["operating_income"], "profit")
    if nonop is not None:
        add(
            "non_operating_income" if nonop >= 0 else "non_operating_expense",
            "Net non-operating income" if nonop >= 0 else "Net non-operating expense",
            abs(nonop),
            "income" if nonop >= 0 else "expense",
            derived=True,
        )
    add("pretax_income", "Income before taxes", values["pretax_income"], "profit")
    tax = values["income_tax"]
    add("income_tax", "Income taxes" if tax is None or tax >= 0 else "Income tax benefit", abs(tax) if tax is not None else None, "expense" if tax is None or tax >= 0 else "income")
    add("net_income", "Net income", values["net_income"], "profit")

    if chart_available:
        def link(source: str, target: str, value: float | None, kind: str) -> None:
            if value is not None and value > 0 and source in node_ids and target in node_ids:
                links.append({"source": source, "target": target, "value": value, "kind": kind})

        link("revenue", "cost_of_revenue", values["cost_of_revenue"], "expense")
        link("revenue", "gross_profit", values["gross_profit"], "profit")
        link("gross_profit", "operating_expenses", values["operating_expenses"], "expense")
        link("gross_profit", "operating_income", values["operating_income"], "profit")
        if nonop is not None and nonop >= 0:
            link("operating_income", "pretax_income", values["operating_income"], "profit")
            link("non_operating_income", "pretax_income", nonop, "income")
        elif nonop is not None:
            link("operating_income", "non_operating_expense", abs(nonop), "expense")
            link("operating_income", "pretax_income", values["pretax_income"], "profit")
        link("pretax_income", "income_tax", tax, "expense")
        link("pretax_income", "net_income", values["net_income"], "profit")

    revenue = values["revenue"]
    nonop_note = None
    if nonop is not None and values["pretax_income"] not in (None, 0):
        ratio = abs(nonop) / abs(values["pretax_income"])
        if ratio >= 0.25:
            nonop_note = f"Net non-operating items equal {ratio * 100:.1f}% of pre-tax income."
    cards = [
        _summary_card("revenue", "Total revenue", revenue, previous.get("revenue"), revenue),
        _summary_card(
            "operating_income", "Operating income", values["operating_income"],
            previous.get("operating_income"), revenue,
        ),
        _summary_card(
            "net_income", "Net income", values["net_income"], previous.get("net_income"),
            revenue, note=nonop_note,
        ),
    ]
    insights = []
    if nonop_note:
        insights.append({"code": "material_non_operating", "severity": "warning", "message": nonop_note})

    return {
        "currency": currency,
        "status": "partial",
        "coverage_level": "consolidated",
        "chart_available": chart_available,
        "summary_cards": cards,
        "nodes": nodes,
        "links": links,
        "insights": insights,
        "validation": {
            "reconciled": not missing and all(reconciliations.values()),
            "reconciliations": reconciliations,
            "missing_fields": missing,
            "warnings": warnings,
        },
        "sources": [{
            "source_id": source_id,
            "document_type": "EODHD fundamentals",
            "filing_date": (statement.income_statement or {}).get("filing_date"),
            "url": None,
        }],
    }


def _cell(value: Any) -> str:
    text = str("" if value is None else value).strip()
    return "" if text.lower() in {"nan", "none", "<na>"} else re.sub(r"\s+", " ", text)


def _amount(value: Any) -> float | None:
    text = _cell(value)
    if not text or "%" in text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    normalized = re.sub(r"[^0-9.\-]", "", text)
    if normalized in {"", "-", "."}:
        return None
    try:
        parsed = float(normalized)
    except ValueError:
        return None
    return -abs(parsed) if negative else parsed


_EXPENSE_LABELS = (
    ("cost_of_revenue", re.compile(r"^(cost of (?:sales|revenue)|cost of goods sold)$", re.I), "Cost of revenue"),
    ("fulfillment", re.compile(r"^fulfillment$", re.I), "Fulfillment"),
    ("technology", re.compile(r"^(technology(?: and| &) infrastructure|research(?: and| &) development|r&d)$", re.I), "Technology & infrastructure"),
    ("marketing", re.compile(r"^(sales(?: and| &) marketing|selling and marketing|marketing)$", re.I), "Sales & marketing"),
    ("general_admin", re.compile(r"^(general(?: and| &) administrative|g&a)$", re.I), "General & administrative"),
    ("other_operating", re.compile(r"^other operating expense.*", re.I), "Other operating expense, net"),
)


def _row_label(row: Iterable[Any]) -> str:
    for value in row:
        text = _cell(value)
        if text and re.search(r"[A-Za-z]", text) and text.upper() not in {"USD", "EUR", "GBP", "CAD", "JPY", "CNY"}:
            return text
    return ""


def _row_amounts(row: Iterable[Any], columns: set[int] | None = None) -> list[float]:
    return [
        amount
        for index, value in enumerate(row)
        if (columns is None or index in columns) and (amount := _amount(value)) is not None
    ]


def _preferred_columns(table: Any, period_type: str) -> set[int] | None:
    header_rows = min(6, len(table.index))
    preferred: set[int] = set()
    for column in range(len(table.columns)):
        header = " ".join([
            _cell(table.columns[column]),
            *(_cell(table.iloc[row_index, column]) for row_index in range(header_rows)),
        ]).lower()
        if period_type == "Quarterly" and "three months" in header and not any(term in header for term in ("six months", "nine months", "twelve months")):
            preferred.add(column)
        if period_type == "Yearly" and re.search(r"year(?:s)? ended|twelve months", header) and "three months" not in header:
            preferred.add(column)
    return preferred or None


def _scale_for_table(table: Any, columns: set[int] | None, expected_revenue: float | None) -> float:
    if expected_revenue in (None, 0):
        return 1.0
    for _, series in table.iterrows():
        row = list(series)
        label = _row_label(row)
        if not re.fullmatch(r"(?:total )?(?:net sales|revenue)", label, re.I):
            continue
        amounts = _row_amounts(row, columns) or _row_amounts(row)
        if not amounts:
            continue
        raw = amounts[0]
        scale = min((1.0, 1_000.0, 1_000_000.0, 1_000_000_000.0), key=lambda candidate: abs(raw * candidate - expected_revenue))
        if abs(raw * scale - expected_revenue) <= max(1.0, abs(expected_revenue) * 0.05):
            return scale
    return 1.0


def _closest_amount(amounts: list[float], expected: float | None) -> float | None:
    if not amounts:
        return None
    if expected is None:
        return amounts[-1]
    return min(amounts, key=lambda value: abs(abs(value) - abs(expected)))


def extract_reported_detail(
    documents: list[dict[str, Any]],
    statement: FinancialStatement,
) -> dict[str, Any]:
    """Extract only table rows that reconcile to the selected local statement."""
    try:
        import pandas as pd
    except ImportError:
        return {"revenue_segments": [], "operating_expenses": [], "sources": []}

    expected = _statement_values(statement)
    expense_rows: dict[str, dict[str, Any]] = {}
    revenue_candidates: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    known_non_segments = re.compile(
        r"total|growth|margin|income|expense|cost|compensation|cash flow|employees|"
        r"shares|earnings|tax|assets|liabilities|three months|six months|twelve months",
        re.I,
    )
    for document in documents:
        html = str(document.get("html") or "")
        if not html:
            continue
        try:
            tables = pd.read_html(StringIO(html), header=None, keep_default_na=False)
        except ValueError:
            continue
        source_id = str(document.get("source_id") or "SEC:UNSPECIFIED")
        sources.append({
            "source_id": source_id,
            "document_type": document.get("form") or "SEC filing",
            "filing_date": document.get("filing_date"),
            "url": document.get("source_url"),
        })
        for table in tables:
            table_text = " ".join(_cell(value) for value in table.to_numpy().flatten())
            looks_like_sales_mix = bool(re.search(r"net sales|revenue", table_text, re.I))
            preferred_columns = _preferred_columns(table, statement.period)
            table_scale = _scale_for_table(table, preferred_columns, expected["revenue"])
            for _, series in table.iterrows():
                row = list(series)
                label = _row_label(row)
                amounts = _row_amounts(row, preferred_columns) or _row_amounts(row)
                amounts = [amount * table_scale for amount in amounts]
                if not label or not amounts:
                    continue
                for key, pattern, canonical_label in _EXPENSE_LABELS:
                    if pattern.match(label):
                        expected_value = (
                            expected["cost_of_revenue"] if key == "cost_of_revenue"
                            else None
                        )
                        value = _closest_amount(amounts, expected_value)
                        if value is not None and value >= 0:
                            expense_rows[key] = {
                                "id": f"expense_{key}",
                                "label": canonical_label,
                                "value": value,
                                "source_id": source_id,
                                "original_label": label,
                                "disclosure_unit": table_scale,
                            }
                        break
                else:
                    if (
                        looks_like_sales_mix
                        and not known_non_segments.search(label)
                        and not label.lower().startswith(("q1 ", "q2 ", "q3 ", "q4 "))
                    ):
                        # SEC tables conventionally place the current reported period first.
                        # Preferred columns exclude YTD groups for quarterly statements.
                        value = amounts[0]
                        if value > 0 and expected["revenue"] and value <= expected["revenue"]:
                            revenue_candidates.append({
                                "label": label,
                                "value": value,
                                "source_id": source_id,
                                "original_label": label,
                                "disclosure_unit": table_scale,
                            })

    # Deduplicate repeated filing and earnings-release rows.
    deduped: dict[str, dict[str, Any]] = {}
    for row in revenue_candidates:
        deduped.setdefault(re.sub(r"[^a-z0-9]", "", row["label"].lower()), row)
    candidates = list(deduped.values())
    revenue = expected["revenue"]
    segments: list[dict[str, Any]] = []
    if revenue and len(candidates) >= 2:
        # Retain the largest self-consistent collection. This deliberately
        # rejects incomplete tables instead of guessing an undisclosed mix.
        candidates = sorted(candidates, key=lambda row: row["value"], reverse=True)[:16]
        best: list[dict[str, Any]] = []
        for mask in range(1, 1 << len(candidates)):
            subset = [candidates[index] for index in range(len(candidates)) if mask & (1 << index)]
            total = sum(row["value"] for row in subset)
            disclosure_unit = max((row.get("disclosure_unit", 1.0) for row in subset), default=1.0)
            tolerance = max(disclosure_unit, abs(revenue) * 0.005)
            gap = revenue - total
            if abs(gap) <= tolerance or 0 <= gap <= revenue * 0.05:
                if len(subset) > len(best):
                    best = subset
        if len(best) >= 2:
            segments = best
            gap = revenue - sum(row["value"] for row in segments)
            disclosure_unit = max((row.get("disclosure_unit", 1.0) for row in segments), default=1.0)
            if gap > max(disclosure_unit, revenue * 0.005):
                segments.append({
                    "label": "Other / reconciliation",
                    "value": gap,
                    "source_id": segments[0]["source_id"],
                    "original_label": "Derived reconciliation",
                    "derived": True,
                })

    return {
        "revenue_segments": segments,
        "operating_expenses": list(expense_rows.values()),
        "sources": list({source["source_id"]: source for source in sources}.values()),
    }


def merge_reported_detail(
    base: dict[str, Any],
    detail: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    result = json.loads(json.dumps(base))
    nodes = [node for node in result["nodes"] if node["id"] not in {"operating_expenses"}]
    links = [
        link for link in result["links"]
        if link["target"] != "operating_expenses"
    ]
    revenue_segments = detail.get("revenue_segments") or []
    expenses = [row for row in detail.get("operating_expenses") or [] if row["id"] != "expense_cost_of_revenue"]
    revenue_node = next((node for node in nodes if node["id"] == "revenue"), None)
    gross_node = next((node for node in nodes if node["id"] == "gross_profit"), None)
    operating_node = next((node for node in nodes if node["id"] == "operating_income"), None)

    if revenue_segments and revenue_node:
        for index, row in enumerate(revenue_segments):
            node_id = f"revenue_segment_{index}"
            nodes.append(_source_node(
                node_id, row["label"], row["value"], "income",
                source_id=row["source_id"],
                evidence_type="derived_calculation" if row.get("derived") else "fact_source_reported",
                original_label=row.get("original_label"),
            ))
            links.append({"source": node_id, "target": "revenue", "value": row["value"], "kind": "income"})

    expense_sum = sum(row["value"] for row in expenses)
    expected_expenses = (
        gross_node["value"] - operating_node["value"]
        if gross_node and operating_node else None
    )
    expenses_reconcile = False
    if expected_expenses is not None and expenses:
        gap = expected_expenses - expense_sum
        disclosure_unit = max((row.get("disclosure_unit", 1.0) for row in expenses), default=1.0)
        tolerance = max(disclosure_unit, abs(expected_expenses) * 0.005)
        if abs(gap) <= tolerance:
            expenses_reconcile = True
    if expenses_reconcile:
        for row in expenses:
            nodes.append(_source_node(
                row["id"], row["label"], row["value"], "expense",
                source_id=row["source_id"],
                evidence_type="derived_calculation" if row.get("derived") else "fact_source_reported",
                original_label=row.get("original_label"),
            ))
            links.append({"source": "gross_profit", "target": row["id"], "value": row["value"], "kind": "expense"})
    else:
        original_operating = next((node for node in base["nodes"] if node["id"] == "operating_expenses"), None)
        if original_operating:
            nodes.append(original_operating)
            links.extend(
                link for link in base["links"] if link["target"] == "operating_expenses"
            )

    full = bool(revenue_segments and expenses_reconcile)
    result.update({
        "status": "ready" if full else "partial",
        "coverage_level": "full" if full else "consolidated",
        "nodes": nodes,
        "links": links,
        "sources": list({
            source["source_id"]: source
            for source in [*result.get("sources", []), *detail.get("sources", [])]
        }.values()),
    })
    validation = dict(result.get("validation") or {})
    validation["revenue_segments_reconciled"] = bool(revenue_segments)
    validation["operating_expenses_reconciled"] = expenses_reconcile
    result["validation"] = validation
    return result, validation


def _cache_identity(statement: FinancialStatement, currency: str | None) -> tuple[str, str]:
    fingerprint = statement_fingerprint(statement, currency=currency)
    raw = "\0".join((statement.ticker, statement.fiscal_date.isoformat(), statement.period, fingerprint, FINANCIAL_FLOW_SCHEMA_VERSION))
    return fingerprint, hashlib.sha256(raw.encode()).hexdigest()


async def _previous_statement(db: AsyncSession, statement: FinancialStatement) -> FinancialStatement | None:
    query = select(FinancialStatement).where(
        FinancialStatement.ticker == statement.ticker,
        FinancialStatement.period == statement.period,
        FinancialStatement.fiscal_date < statement.fiscal_date,
    )
    if statement.period == "Quarterly":
        result = await db.execute(
            query.where(
                FinancialStatement.fiscal_date >= statement.fiscal_date - timedelta(days=460),
                FinancialStatement.fiscal_date <= statement.fiscal_date - timedelta(days=270),
            )
        )
        candidates = list(result.scalars().all())
        return min(
            candidates,
            key=lambda record: abs((statement.fiscal_date - record.fiscal_date).days - 365),
            default=None,
        )
    result = await db.execute(query.order_by(FinancialStatement.fiscal_date.desc()).limit(1))
    return result.scalar_one_or_none()


async def _latest_run(
    db: AsyncSession,
    statement: FinancialStatement,
    currency: str | None,
) -> FinancialFlowRun | None:
    _, identity = _cache_identity(statement, currency)
    result = await db.execute(
        select(FinancialFlowRun)
        .where(
            FinancialFlowRun.ticker == statement.ticker,
            FinancialFlowRun.period_end == statement.fiscal_date,
            FinancialFlowRun.period_type == ("annual" if statement.period == "Yearly" else "quarterly"),
            FinancialFlowRun.cache_identity == identity,
        )
        .order_by(FinancialFlowRun.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def enqueue_financial_flow(
    db: AsyncSession,
    statement: FinancialStatement,
    currency: str | None,
) -> tuple[FinancialFlowRun, bool]:
    period_type = "annual" if statement.period == "Yearly" else "quarterly"
    fingerprint, identity = _cache_identity(statement, currency)
    existing = await _latest_run(db, statement, currency)
    if existing is not None:
        if existing.status != "failed" or existing.created_at.date() == utc_now().date():
            return existing, False
        existing.status = "queued"
        existing.stage = "retry_queued"
        existing.active_key = f"{statement.ticker}:{period_type}:{statement.fiscal_date.isoformat()}"
        existing.attempt_count = 0
        existing.error_message = None
        existing.finished_at = None
        await db.commit()
        return existing, True
    active_key = f"{statement.ticker}:{period_type}:{statement.fiscal_date.isoformat()}"
    run = FinancialFlowRun(
        ticker=statement.ticker,
        period_end=statement.fiscal_date,
        period_type=period_type,
        input_fingerprint=fingerprint,
        cache_identity=identity,
        schema_version=FINANCIAL_FLOW_SCHEMA_VERSION,
        status="queued",
        stage="queued",
        coverage_level="consolidated",
        active_key=active_key,
        source_snapshots=[],
        attempt_count=0,
    )
    db.add(run)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        result = await db.execute(
            select(FinancialFlowRun).where(FinancialFlowRun.active_key == active_key).limit(1)
        )
        concurrent = result.scalar_one_or_none()
        if concurrent is None:
            raise
        return concurrent, False
    return run, True


async def _resolve_cik(db: AsyncSession, ticker: str) -> str:
    support = (await get_earnings_quality(ticker, db))["sec_analysis"]
    if not support["supported"]:
        raise FinancialFlowError(support["reason"] or "SEC enrichment is unsupported")
    return str(support["cik"])


async def execute_financial_flow(run_id: int) -> None:
    async with _semaphore:
        async with async_session_maker() as db:
            run = await db.get(FinancialFlowRun, run_id)
            if run is None or run.status not in ACTIVE_STATUSES:
                return
            run.status = "running"
            run.stage = "fetching_sec"
            run.owner_token = _OWNER_TOKEN
            run.global_slot = GLOBAL_SLOT
            run.started_at = run.started_at or utc_now()
            run.lease_expires_at = utc_now() + timedelta(seconds=settings.FINANCIAL_FLOW_LEASE_SECONDS)
            try:
                await db.commit()
            except IntegrityError:
                await db.rollback()
                return

            try:
                statement = await get_statement_for_period(db, run.ticker, run.period_end, run.period_type)
                profile = await db.get(Ticker, run.ticker)
                if statement is None:
                    raise FinancialFlowError("The local financial statement is no longer available")
                cik = await _resolve_cik(db, run.ticker)
                documents: list[dict[str, Any]] | None = None
                last_error: Exception | None = None
                for attempt in range(run.attempt_count, 3):
                    run.attempt_count = attempt + 1
                    run.lease_expires_at = utc_now() + timedelta(seconds=settings.FINANCIAL_FLOW_LEASE_SECONDS)
                    await db.commit()
                    try:
                        documents = await asyncio.wait_for(
                            asyncio.to_thread(
                                _fetch_sec_documents_sync,
                                cik=cik,
                                period_end=run.period_end,
                                period_type=run.period_type,
                            ),
                            timeout=settings.FINANCIAL_FLOW_TIMEOUT_SECONDS,
                        )
                        break
                    except Exception as exc:
                        last_error = exc
                        if attempt < 2:
                            await asyncio.sleep(2 ** attempt)
                if documents is None:
                    raise last_error or FinancialFlowError("SEC documents are unavailable")
                snapshot_ids = []
                for document in documents:
                    snapshot = await persist_snapshot(
                        db,
                        "SEC",
                        "financial_flow_document",
                        document,
                        as_of_date=run.period_end,
                        details={
                            "ticker": run.ticker,
                            "period_type": run.period_type,
                            "source_id": document.get("source_id"),
                        },
                    )
                    snapshot_ids.append(snapshot.id)
                run.stage = "normalizing"
                previous = await _previous_statement(db, statement)
                base = build_consolidated_flow(statement, previous_statement=previous, currency=profile.currency if profile else None)
                detail = extract_reported_detail(documents, statement)
                result, validation = merge_reported_detail(base, detail)
                run.result = result
                run.validation_report = validation
                run.source_snapshots = snapshot_ids
                run.coverage_level = result["coverage_level"]
                run.status = "completed"
                run.stage = "completed"
                run.error_message = None
            except Exception as exc:
                logger.warning("Financial-flow enrichment failed for run %s: %s", run_id, exc)
                run.status = "failed"
                run.stage = "failed"
                run.error_message = str(exc)[:2000]
            finally:
                run.active_key = None
                run.global_slot = None
                run.owner_token = None
                run.lease_expires_at = None
                run.finished_at = utc_now()
                await db.commit()


def schedule_financial_flow(run_id: int) -> None:
    task = _tasks.get(run_id)
    if task is not None and not task.done():
        return
    task = asyncio.create_task(execute_financial_flow(run_id))
    _tasks[run_id] = task
    task.add_done_callback(lambda _: _tasks.pop(run_id, None))


async def ensure_latest_financial_flow_jobs(db: AsyncSession, ticker: str) -> None:
    if not settings.FINANCIAL_FLOW_ENRICHMENT_ENABLED or not settings.SEC_USER_AGENT.strip():
        return
    profile = await db.get(Ticker, ticker)
    if profile is None or is_financial_company(profile.sector, profile.industry):
        return
    for database_period in ("Quarterly", "Yearly"):
        result = await db.execute(
            select(FinancialStatement)
            .where(FinancialStatement.ticker == ticker, FinancialStatement.period == database_period)
            .order_by(FinancialStatement.fiscal_date.desc())
            .limit(1)
        )
        statement = result.scalar_one_or_none()
        if statement is None:
            continue
        run, created = await enqueue_financial_flow(db, statement, profile.currency)
        if created:
            schedule_financial_flow(run.id)


async def get_financial_flow(
    ticker: str,
    db: AsyncSession,
    *,
    period_type: str,
    period_end: date | None = None,
) -> dict[str, Any]:
    if period_type not in {"annual", "quarterly"}:
        raise ValueError("period_type must be annual or quarterly")
    canonical = canonicalize_ticker(ticker)
    profile = await db.get(Ticker, canonical)
    database_period = "Yearly" if period_type == "annual" else "Quarterly"
    statement_result = await db.execute(
        select(FinancialStatement)
        .where(
            FinancialStatement.ticker == canonical,
            FinancialStatement.period == database_period,
            *([FinancialStatement.fiscal_date == period_end] if period_end else []),
        )
        .order_by(FinancialStatement.fiscal_date.desc())
        .limit(1)
    )
    statement = statement_result.scalar_one_or_none()
    periods_result = await db.execute(
        select(FinancialStatement.fiscal_date)
        .where(FinancialStatement.ticker == canonical, FinancialStatement.period == database_period)
        .order_by(FinancialStatement.fiscal_date.desc())
    )
    available_periods = [value.isoformat() for value in periods_result.scalars().all()]
    if not settings.FINANCIAL_FLOW_ENABLED:
        return {
            "ticker": canonical,
            "currency": profile.currency if profile else None,
            "period_type": period_type,
            "period_end": statement.fiscal_date.isoformat() if statement else (period_end.isoformat() if period_end else None),
            "available_periods": available_periods,
            "status": "unavailable",
            "coverage_level": "none",
            "unsupported_reason": "Financial flow is disabled by configuration.",
            "chart_available": False,
            "summary_cards": [], "nodes": [], "links": [], "insights": [],
            "validation": {"reconciled": False, "missing_fields": [], "warnings": []},
            "sources": [],
            "enrichment": {"status": "disabled", "run_id": None, "last_error": None, "updated_at": None},
        }
    if statement is None:
        return {
            "ticker": canonical,
            "currency": profile.currency if profile else None,
            "period_type": period_type,
            "period_end": period_end.isoformat() if period_end else None,
            "available_periods": available_periods,
            "status": "unavailable",
            "coverage_level": "none",
            "unsupported_reason": "No local financial statement matches the requested period.",
            "chart_available": False,
            "summary_cards": [], "nodes": [], "links": [], "insights": [],
            "validation": {"reconciled": False, "missing_fields": ["statement"], "warnings": []},
            "sources": [],
            "enrichment": {"status": "unavailable", "run_id": None, "last_error": None, "updated_at": None},
        }
    previous = await _previous_statement(db, statement)
    result = build_consolidated_flow(statement, previous_statement=previous, currency=profile.currency if profile else None)
    unsupported_reason = None
    if profile and is_financial_company(profile.sector, profile.industry):
        unsupported_reason = "Financial companies require an industry-specific flow template and are not supported in this release."
        result.update({"status": "unsupported", "coverage_level": "none", "chart_available": False, "links": []})

    run = await _latest_run(db, statement, profile.currency if profile else None)
    if run and run.status == "completed" and isinstance(run.result, dict) and unsupported_reason is None:
        result = run.result
    enrichment_status = run.status if run else "not_requested"
    response = {
        "ticker": canonical,
        "currency": profile.currency if profile else result.get("currency"),
        "period_type": period_type,
        "period_end": statement.fiscal_date.isoformat(),
        "available_periods": available_periods,
        "unsupported_reason": unsupported_reason,
        **result,
        "enrichment": {
            "status": enrichment_status,
            "run_id": run.id if run else None,
            "last_error": run.error_message if run else None,
            "updated_at": run.updated_at.isoformat() if run and run.updated_at else None,
        },
    }
    if unsupported_reason is None:
        await ensure_latest_financial_flow_jobs(db, canonical)
        refreshed = await _latest_run(db, statement, profile.currency if profile else None)
        if refreshed is not None:
            response["enrichment"] = {
                "status": refreshed.status,
                "run_id": refreshed.id,
                "last_error": refreshed.error_message,
                "updated_at": refreshed.updated_at.isoformat() if refreshed.updated_at else None,
            }
    return response


async def recover_interrupted_financial_flows() -> None:
    async with async_session_maker() as db:
        result = await db.execute(
            select(FinancialFlowRun).where(
                FinancialFlowRun.status == "running",
                FinancialFlowRun.lease_expires_at.is_not(None),
                FinancialFlowRun.lease_expires_at < utc_now(),
            )
        )
        expired = list(result.scalars().all())
        for run in expired:
            run.status = "queued"
            run.stage = "recovered"
            run.global_slot = None
            run.owner_token = None
            run.lease_expires_at = None
        await db.commit()
        queued_result = await db.execute(
            select(FinancialFlowRun).where(FinancialFlowRun.status == "queued")
        )
        runs = list(queued_result.scalars().all())
        for run in runs:
            schedule_financial_flow(run.id)


async def _recovery_monitor() -> None:
    while True:
        await asyncio.sleep(max(30.0, settings.FINANCIAL_FLOW_LEASE_SECONDS / 2))
        try:
            await recover_interrupted_financial_flows()
        except Exception:
            logger.exception("Unable to recover expired financial-flow leases")


def start_financial_flow_recovery_monitor() -> None:
    global _recovery_task
    if _recovery_task is None or _recovery_task.done():
        _recovery_task = asyncio.create_task(_recovery_monitor())


async def shutdown_financial_flow_tasks() -> None:
    global _recovery_task
    if _recovery_task is not None:
        _recovery_task.cancel()
    tasks = [*_tasks.values(), *([_recovery_task] if _recovery_task else [])]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _tasks.clear()
    _recovery_task = None
