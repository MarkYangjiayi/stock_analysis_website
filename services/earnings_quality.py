from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import math
from collections import Counter
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from models import (
    EarningsQualityAnalysisRun,
    FinancialStatement,
    RawDataSnapshot,
    Ticker,
)
from services.earnings_quality_validation import (
    FILING_EARNINGS_QUALITY_SCHEMA_VERSION,
)
from services.security_master import canonicalize_ticker


MATERIALITY_WARNING_RATIO = 0.10
MATERIALITY_HIGH_RATIO = 0.25
ANNUAL_RECURRENCE_COUNT = 2
QUARTERLY_RECURRENCE_COUNT = 4
EARNINGS_QUALITY_PROMPT_VERSION = "earnings-quality-v3"
EARNINGS_QUALITY_SCHEMA_VERSION = FILING_EARNINGS_QUALITY_SCHEMA_VERSION

_CANDIDATE_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("nonRecurring", "non_recurring", "Vendor-labelled non-recurring item"),
    ("extraordinaryItems", "extraordinary_item", "Extraordinary item"),
    ("discontinuedOperations", "discontinued_operations", "Discontinued operations"),
)
_FINANCIAL_SECTOR_TERMS = (
    "financial",
    "bank",
    "insurance",
    "capital markets",
    "credit services",
    "asset management",
    "broker",
)


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _first_number(mapping: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _number(mapping.get(key))
        if value is not None:
            return value
    return None


def is_financial_company(sector: str | None, industry: str | None) -> bool:
    classification = f"{sector or ''} {industry or ''}".lower()
    return any(term in classification for term in _FINANCIAL_SECTOR_TERMS)


def statement_fingerprint(
    record: FinancialStatement,
    *,
    currency: str | None = None,
) -> str:
    payload = {
        "ticker": record.ticker,
        "period_end": record.fiscal_date.isoformat(),
        "period": record.period,
        "currency": currency,
        "revenue": str(record.revenue) if record.revenue is not None else None,
        "net_income": str(record.net_income) if record.net_income is not None else None,
        "income_statement": record.income_statement or {},
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _severity(amount: float, materiality_base: float) -> tuple[str | None, float | None]:
    if materiality_base <= 0:
        return None, None
    ratio = abs(amount) / materiality_base
    if ratio >= MATERIALITY_HIGH_RATIO:
        return "high", ratio
    if ratio >= MATERIALITY_WARNING_RATIO:
        return "warning", ratio
    return None, ratio


def _candidate(
    *,
    category: str,
    label: str,
    amount: float,
    materiality_base: float,
    source_field: str,
    detail: str,
) -> dict[str, Any] | None:
    severity, ratio = _severity(amount, materiality_base)
    if severity is None:
        return None
    return {
        "category": category,
        "label": label,
        "amount": amount,
        "materiality_ratio": ratio,
        "severity": severity,
        "source": "structured_financial_data",
        "source_field": source_field,
        "detail": detail,
        "treatment": "flag_only",
        "recurring_adjustment": False,
    }


def evaluate_statement(
    record: FinancialStatement,
    *,
    previous_same_frequency: FinancialStatement | None = None,
    financial_company: bool = False,
    currency: str | None = None,
) -> dict[str, Any]:
    income = record.income_statement or {}
    revenue = _first_number(income, "totalRevenue")
    if revenue is None:
        revenue = _number(record.revenue)
    net_income = _first_number(income, "netIncome")
    if net_income is None:
        net_income = _number(record.net_income)
    pretax_income = _first_number(income, "incomeBeforeTax", "incomeBeforeTaxExpense")
    continuing_income = _first_number(
        income,
        "netIncomeFromContinuingOps",
        "netIncomeFromContinuingOperations",
    )
    tax_expense = _first_number(income, "incomeTaxExpense", "taxProvision")

    base_candidates = [abs(pretax_income) if pretax_income is not None else 0.0]
    if revenue is not None:
        base_candidates.append(0.01 * abs(revenue))
    materiality_base = max(base_candidates) if base_candidates else 0.0
    data_quality_warnings: list[dict[str, str]] = []
    flags: list[dict[str, Any]] = []
    candidate_amounts = {
        field: _number(income.get(field))
        for field, _, _ in _CANDIDATE_FIELDS
    }

    if materiality_base <= 0:
        data_quality_warnings.append({
            "code": "materiality_base_unavailable",
            "message": "Pre-tax income and revenue are unavailable or zero; materiality cannot be assessed.",
        })

    for field, category, label in _CANDIDATE_FIELDS:
        amount = candidate_amounts[field]
        if amount is None or materiality_base <= 0:
            continue
        item = _candidate(
            category=category,
            label=label,
            amount=amount,
            materiality_base=materiality_base,
            source_field=field,
            detail=(
                "This provider-labelled amount is a candidate only. It does not change "
                "reported earnings unless a filing-level reconciliation is verified."
            ),
        )
        if item:
            flags.append(item)

    # Non-operating income is assessed as a change, not an absolute balance.
    # It is intentionally disabled for financial companies because it often
    # represents ordinary operations for banks, insurers, and brokers.
    non_operating = _number(income.get("nonOperatingIncomeNetOther"))
    previous_non_operating = None
    if previous_same_frequency is not None:
        previous_non_operating = _number(
            (previous_same_frequency.income_statement or {}).get(
                "nonOperatingIncomeNetOther"
            )
        )
    if (
        not financial_company
        and materiality_base > 0
        and non_operating is not None
        and previous_non_operating is not None
    ):
        swing = non_operating - previous_non_operating
        item = _candidate(
            category="non_operating_swing",
            label="Non-operating income swing",
            amount=swing,
            materiality_base=materiality_base,
            source_field="nonOperatingIncomeNetOther",
            detail=(
                "Change versus the preceding comparable-frequency statement; "
                "classification requires filing evidence."
            ),
        )
        if item:
            item["reported_amount"] = non_operating
            item["comparison_amount"] = previous_non_operating
            flags.append(item)

    # A reported/continuing-operations gap is only classified when disclosed
    # line items reconcile. Otherwise it is explicitly a data-quality warning.
    if net_income is not None and continuing_income is not None and materiality_base > 0:
        gap = net_income - continuing_income
        gap_severity, gap_ratio = _severity(gap, materiality_base)
        if gap_severity is not None:
            reconciling_values = [
                _number(income.get("discontinuedOperations")),
                _number(income.get("extraordinaryItems")),
            ]
            disclosed = [value for value in reconciling_values if value is not None]
            disclosed_total = sum(disclosed) if disclosed else None
            tolerance = max(abs(gap) * 0.01, materiality_base * 0.01, 1.0)
            if (
                disclosed_total is not None
                and abs(gap - disclosed_total) <= tolerance
            ):
                flags.append({
                    "category": "continuing_operations_reconciliation",
                    "label": "Reported vs continuing-operations gap",
                    "amount": gap,
                    "materiality_ratio": gap_ratio,
                    "severity": gap_severity,
                    "source": "structured_financial_data",
                    "source_field": "netIncomeFromContinuingOps",
                    "detail": "The gap reconciles to disclosed extraordinary/discontinued-operation lines.",
                    "treatment": "flag_only",
                    "recurring_adjustment": False,
                })
            else:
                data_quality_warnings.append({
                    "code": "continuing_operations_gap_unreconciled",
                    "message": (
                        "Net income differs materially from continuing-operations income, "
                        "but available statement lines do not reconcile the difference."
                    ),
                })

    present_fields = sum(
        value is not None
        for value in (revenue, net_income, pretax_income, continuing_income, tax_expense)
    )
    candidate_field_count = sum(
        amount is not None
        for amount in candidate_amounts.values()
    )
    screening_coverage_complete = (
        revenue is not None
        and net_income is not None
        and pretax_income is not None
        and continuing_income is not None
        and tax_expense is not None
        and candidate_field_count == len(_CANDIDATE_FIELDS)
        and (financial_company or non_operating is not None)
    )
    if not screening_coverage_complete:
        data_quality_warnings.append({
            "code": "earnings_quality_fields_incomplete",
            "message": (
                "One or more earnings-quality screening fields are missing; "
                "the period cannot receive a clean conclusion."
            ),
        })

    if materiality_base <= 0 or present_fields < 2:
        assessment = "unavailable"
    elif flags:
        assessment = "material_candidates"
    elif not screening_coverage_complete:
        assessment = "unavailable"
    elif data_quality_warnings:
        assessment = "data_quality_warning"
    else:
        assessment = "no_material_candidates"

    return {
        "period_end": record.fiscal_date.isoformat(),
        "period_type": "annual" if record.period.lower() == "yearly" else "quarterly",
        "reported": {
            "revenue": revenue,
            "net_income": net_income,
            "income_before_tax": pretax_income,
            "net_income_from_continuing_operations": continuing_income,
            "income_tax_expense": tax_expense,
            "non_recurring": candidate_amounts["nonRecurring"],
            "extraordinary_items": candidate_amounts["extraordinaryItems"],
            "discontinued_operations": candidate_amounts["discontinuedOperations"],
            "non_operating_income_net_other": non_operating,
        },
        "materiality_base": materiality_base if materiality_base > 0 else None,
        "thresholds": {
            "warning": MATERIALITY_WARNING_RATIO,
            "high": MATERIALITY_HIGH_RATIO,
        },
        "flags": flags,
        "data_quality_warnings": data_quality_warnings,
        "assessment": assessment,
        "statement_fingerprint": statement_fingerprint(record, currency=currency),
    }


def evaluate_statement_series(
    records: Sequence[FinancialStatement],
    *,
    financial_company: bool = False,
    currency: str | None = None,
) -> list[dict[str, Any]]:
    ordered = sorted(records, key=lambda row: row.fiscal_date, reverse=True)
    evaluations = [
        evaluate_statement(
            record,
            previous_same_frequency=(ordered[index + 1] if index + 1 < len(ordered) else None),
            financial_company=financial_company,
            currency=currency,
        )
        for index, record in enumerate(ordered)
    ]
    recurrence_threshold = (
        ANNUAL_RECURRENCE_COUNT
        if ordered and ordered[0].period.lower() == "yearly"
        else QUARTERLY_RECURRENCE_COUNT
    )
    category_counts = Counter(
        flag["category"]
        for evaluation in evaluations
        for flag in evaluation["flags"]
    )
    recurring_categories = {
        category
        for category, count in category_counts.items()
        if count >= recurrence_threshold
    }
    for evaluation in evaluations:
        for flag in evaluation["flags"]:
            if flag["category"] in recurring_categories:
                flag["recurring_adjustment"] = True
                flag["treatment"] = "recurring_flag_only"
                flag["detail"] += (
                    " This category recurs too frequently to be treated as a one-time adjustment."
                )
    return evaluations


@lru_cache(maxsize=512)
def _load_gzip_general(path: str, checksum: str) -> dict[str, Any]:
    try:
        with gzip.open(Path(path), "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    general = payload.get("General")
    return general if isinstance(general, dict) else {}


async def _local_sec_support(
    db: AsyncSession,
    ticker: str,
) -> dict[str, Any]:
    snapshot_result = await db.execute(
        select(RawDataSnapshot)
        .where(
            RawDataSnapshot.source == "EODHD",
            RawDataSnapshot.dataset == "fundamentals",
            RawDataSnapshot.details["ticker"].as_string() == ticker,
        )
        .order_by(RawDataSnapshot.fetched_at.desc())
        .limit(1)
    )
    snapshot = snapshot_result.scalar_one_or_none()
    general = (
        await asyncio.to_thread(
            _load_gzip_general,
            snapshot.storage_path,
            snapshot.checksum,
        )
        if snapshot is not None
        else {}
    )
    cik = str(general.get("CIK") or "").strip().lstrip("0")
    asset_type = str(general.get("Type") or "").strip()
    country = str(
        general.get("CountryISO")
        or general.get("CountryName")
        or ""
    ).upper()

    reason = None
    if not ticker.endswith(".US"):
        reason = "Only U.S. SEC issuers are supported in the first release."
    elif "etf" in asset_type.lower() or "fund" in asset_type.lower():
        reason = "ETFs and funds are not supported."
    elif not cik:
        reason = "No CIK is available in the local company profile."
    elif country and country not in {"USA", "US", "UNITED STATES"}:
        reason = "Foreign private issuers (20-F/6-K) are not supported yet."

    return {
        "supported": reason is None,
        "cik": cik or None,
        "reason": reason,
        "supported_forms": ["10-K", "10-Q", "8-K Item 2.02", "Exhibit 99.1"],
        "unsupported_forms": ["20-F", "6-K"],
    }


def serialize_analysis_run(run: EarningsQualityAnalysisRun) -> dict[str, Any]:
    def iso(value: Any) -> str | None:
        return value.isoformat() if value is not None else None

    return {
        "id": run.id,
        "ticker": run.ticker,
        "period_end": iso(run.period_end),
        "period_type": run.period_type,
        "status": run.status,
        "stage": run.stage,
        "model": run.model,
        "prompt_version": run.prompt_version,
        "source_accession": run.sec_accession,
        "source_snapshots": run.source_snapshots or [],
        "result": run.result,
        "validation_report": run.validation_report,
        "error_message": run.error_message,
        "retryable": run.status in {"failed", "waiting_for_filing"},
        "created_at": iso(run.created_at),
        "started_at": iso(run.started_at),
        "finished_at": iso(run.finished_at),
    }


def _analysis_by_period(
    runs: Iterable[EarningsQualityAnalysisRun],
) -> dict[tuple[str, str], EarningsQualityAnalysisRun]:
    selected: dict[tuple[str, str], EarningsQualityAnalysisRun] = {}
    for run in runs:
        key = (run.period_end.isoformat(), run.period_type)
        if key not in selected:
            selected[key] = run
    return selected


async def get_earnings_quality(
    ticker: str,
    db: AsyncSession,
) -> dict[str, Any]:
    """Build the public response exclusively from local database/raw snapshots."""
    canonical_ticker = canonicalize_ticker(ticker)
    profile_result = await db.execute(
        select(Ticker).where(Ticker.ticker == canonical_ticker)
    )
    profile = profile_result.scalar_one_or_none()

    statement_result = await db.execute(
        select(FinancialStatement)
        .where(FinancialStatement.ticker == canonical_ticker)
        .order_by(FinancialStatement.fiscal_date.desc())
    )
    statements = list(statement_result.scalars().all())
    annual_records = [row for row in statements if row.period == "Yearly"][:3]
    quarterly_records = [row for row in statements if row.period == "Quarterly"][:8]
    financial_company = is_financial_company(
        profile.sector if profile else None,
        profile.industry if profile else None,
    )
    annual = evaluate_statement_series(
        annual_records,
        financial_company=financial_company,
        currency=profile.currency if profile else None,
    )
    quarterly = evaluate_statement_series(
        quarterly_records,
        financial_company=financial_company,
        currency=profile.currency if profile else None,
    )

    run_result = await db.execute(
        select(EarningsQualityAnalysisRun)
        .where(
            EarningsQualityAnalysisRun.ticker == canonical_ticker,
            EarningsQualityAnalysisRun.model == settings.DEEPSEEK_MODEL,
            EarningsQualityAnalysisRun.prompt_version
            == EARNINGS_QUALITY_PROMPT_VERSION,
            EarningsQualityAnalysisRun.schema_version
            == EARNINGS_QUALITY_SCHEMA_VERSION,
        )
        .order_by(
            EarningsQualityAnalysisRun.period_end.desc(),
            EarningsQualityAnalysisRun.id.desc(),
        )
        .limit(200)
    )
    latest_runs = _analysis_by_period(run_result.scalars().all())
    for period in [*annual, *quarterly]:
        run = latest_runs.get((period["period_end"], period["period_type"]))
        if run is not None and run.statement_fingerprint != period["statement_fingerprint"]:
            # A restated local statement invalidates the old cache naturally.
            run = None
        period["analysis"] = serialize_analysis_run(run) if run else None
        verified = (
            run.result
            if run is not None
            and run.status == "completed"
            and isinstance(run.result, dict)
            and run.result.get("verification_status") == "verified"
            else None
        )
        period["verified_normalized"] = (
            {
                "net_income": verified.get("normalized_net_income"),
                "adjusted_eps": verified.get("adjusted_eps"),
            }
            if verified
            else None
        )

    all_periods = [*annual, *quarterly]
    evaluable = [period for period in all_periods if period["assessment"] != "unavailable"]
    material_periods = [period for period in all_periods if period["flags"]]
    quality_periods = [period for period in all_periods if period["data_quality_warnings"]]
    if not evaluable:
        verdict = "unavailable"
    elif material_periods:
        verdict = "flags_present"
    elif quality_periods:
        verdict = "data_quality_warning"
    else:
        verdict = "no_material_candidates_on_available_data"

    return {
        "ticker": canonical_ticker,
        "currency": profile.currency if profile else None,
        "methodology": {
            "materiality_base": "max(abs(income before tax), 1% of abs(revenue))",
            "warning_threshold": MATERIALITY_WARNING_RATIO,
            "high_threshold": MATERIALITY_HIGH_RATIO,
            "reported_remains_primary": True,
            "structured_flags_are_adjustments": False,
        },
        "summary": {
            "verdict": verdict,
            "evaluated_periods": len(evaluable),
            "flagged_periods": len(material_periods),
            "data_quality_periods": len(quality_periods),
            "financial_industry_exemption": financial_company,
            "message": (
                "Potential earnings-quality issues are screening signals, not allegations of fraud."
            ),
        },
        "annual": annual,
        "quarterly": quarterly,
        "sec_analysis": await _local_sec_support(db, canonical_ticker),
    }


async def get_statement_for_period(
    db: AsyncSession,
    ticker: str,
    period_end: date,
    period_type: str,
) -> FinancialStatement | None:
    database_period = "Yearly" if period_type == "annual" else "Quarterly"
    result = await db.execute(
        select(FinancialStatement).where(
            FinancialStatement.ticker == canonicalize_ticker(ticker),
            FinancialStatement.fiscal_date == period_end,
            FinancialStatement.period == database_period,
        )
    )
    return result.scalar_one_or_none()
