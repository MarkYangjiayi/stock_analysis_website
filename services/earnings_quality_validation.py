from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


ELIGIBLE_NORMALIZATION_CATEGORIES = {
    "discontinued_operations",
    "asset_or_business_disposal",
    "debt_extinguishment",
    "isolated_legal_settlement",
    "insurance_or_catastrophe",
    "impairment",
    "discrete_tax",
}
AdjustmentCategory = Literal[
    "discontinued_operations",
    "asset_or_business_disposal",
    "debt_extinguishment",
    "isolated_legal_settlement",
    "insurance_or_catastrophe",
    "impairment",
    "discrete_tax",
    "stock_based_compensation",
    "foreign_exchange",
    "acquired_intangible_amortization",
    "routine_tax",
    "restructuring_or_integration",
    "other",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class FilingCitation(StrictModel):
    source_id: str
    accession: str
    document_name: str
    section: str
    excerpt: str = Field(min_length=1, max_length=1200)
    source_amount: float
    source_unit_scale: float = Field(gt=0)


class EarningsAdjustment(StrictModel):
    category: AdjustmentCategory
    label: str
    pretax_earnings_effect: Optional[float] = None
    tax_effect: Optional[float] = None
    earnings_effect_after_tax: float
    include_in_normalized: bool
    recurring: bool
    cash_effect: Literal["cash", "non_cash", "mixed", "unknown"]
    citation: FilingCitation


class CompanyAdjustedMetric(StrictModel):
    label: str
    adjusted_net_income: Optional[float] = None
    adjusted_diluted_eps: Optional[float] = None
    net_income_citation: Optional[FilingCitation] = None
    diluted_eps_citation: Optional[FilingCitation] = None


class FilingEarningsQualityExtraction(StrictModel):
    period_end: date
    currency: str
    unit_scale: float = Field(gt=0)
    reported_net_income: float
    adjustments: list[EarningsAdjustment] = Field(default_factory=list, max_length=100)
    company_adjusted: Optional[CompanyAdjustedMetric] = None
    disclosed_adjusted_net_income: Optional[float] = None
    disclosed_adjusted_diluted_eps: Optional[float] = None
    notes: list[str] = Field(default_factory=list, max_length=50)


FILING_EARNINGS_QUALITY_SCHEMA_VERSION = "sha256:" + hashlib.sha256(
    json.dumps(
        FilingEarningsQualityExtraction.model_json_schema(),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()


def _normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


def _close(left: float, right: float, tolerance: float) -> bool:
    return math.isfinite(left) and math.isfinite(right) and abs(left - right) <= tolerance


def _reason(
    code: str,
    message: str,
    adjustment_index: int | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {"code": code, "message": message}
    if adjustment_index is not None:
        value["adjustment_index"] = adjustment_index
    return value


def _excerpt_contains_amount(excerpt: str, amount: float) -> bool:
    normalized_excerpt = _normalized_text(excerpt).replace(",", "")
    magnitude = abs(amount)
    candidates = {
        f"{magnitude:g}",
        f"{magnitude:.1f}",
        f"{magnitude:.2f}",
    }
    return any(
        re.search(
            rf"(?<![\d.]){re.escape(candidate)}(?!\d)(?!\.\d)",
            normalized_excerpt,
        )
        for candidate in candidates
    )


def _citation_is_located(
    citation: FilingCitation,
    source_documents: dict[str, str],
    source_metadata: dict[str, dict[str, str]] | None = None,
) -> bool:
    source = source_documents.get(citation.source_id)
    if not source:
        return False
    if source_metadata is not None:
        metadata = source_metadata.get(citation.source_id)
        if (
            metadata is None
            or citation.accession != metadata.get("accession")
            or citation.document_name != metadata.get("document_name")
        ):
            return False
    excerpt = _normalized_text(citation.excerpt)
    if not excerpt or excerpt not in _normalized_text(source):
        return False
    return _excerpt_contains_amount(excerpt, citation.source_amount)


def validate_filing_extraction(
    extraction: FilingEarningsQualityExtraction,
    *,
    expected_period_end: date,
    expected_currency: str | None,
    reported_net_income: float,
    source_documents: dict[str, str],
    source_metadata: dict[str, dict[str, str]] | None = None,
    recurring_categories: set[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate model output and expose adjusted values only after every gate passes."""
    failures: list[dict[str, Any]] = []
    checks: list[dict[str, Any]] = []

    period_matches = extraction.period_end == expected_period_end
    checks.append({"check": "period_matches", "passed": period_matches})
    if not period_matches:
        failures.append(_reason("period_mismatch", "The extracted filing period does not match the selected statement."))

    normalized_currency = extraction.currency.strip().upper()
    currency_matches = bool(expected_currency) and (
        normalized_currency == expected_currency.strip().upper()
    )
    checks.append({"check": "currency_matches", "passed": currency_matches})
    if not currency_matches:
        failures.append(_reason(
            "currency_mismatch",
            "The local statement currency is unavailable or does not match the filing currency.",
        ))

    base_unit = extraction.unit_scale == 1
    checks.append({"check": "base_unit_normalized", "passed": base_unit})
    if not base_unit:
        failures.append(_reason("unit_mismatch", "Model amounts were not normalized to the local statement's base units."))

    reporting_tolerance = max(abs(reported_net_income) * 0.01, 1.0)
    reported_matches = _close(
        extraction.reported_net_income,
        reported_net_income,
        reporting_tolerance,
    )
    checks.append({"check": "reported_net_income_matches", "passed": reported_matches})
    if not reported_matches:
        failures.append(_reason("reported_net_income_mismatch", "Extracted reported net income does not match the local statement."))

    included_effects: list[float] = []
    for index, adjustment in enumerate(extraction.adjustments):
        citation_located = _citation_is_located(
            adjustment.citation,
            source_documents,
            source_metadata,
        )
        checks.append({
            "check": "citation_located",
            "adjustment_index": index,
            "passed": citation_located,
        })
        if not citation_located:
            failures.append(_reason("citation_not_located", "The cited excerpt could not be located in its saved SEC source.", index))

        cited_amount = adjustment.citation.source_amount * adjustment.citation.source_unit_scale
        comparable_amount = (
            adjustment.pretax_earnings_effect
            if adjustment.pretax_earnings_effect is not None
            else adjustment.earnings_effect_after_tax
        )
        amount_tolerance = max(abs(comparable_amount) * 0.01, adjustment.citation.source_unit_scale)
        amount_located = _close(cited_amount, comparable_amount, amount_tolerance)
        checks.append({
            "check": "cited_amount_matches",
            "adjustment_index": index,
            "passed": amount_located,
        })
        if not amount_located:
            failures.append(_reason("cited_amount_mismatch", "The cited filing amount does not match the extracted adjustment.", index))

        source_scale = adjustment.citation.source_unit_scale
        tax_amount_located = (
            adjustment.tax_effect is None
            or adjustment.tax_effect == 0
            or _excerpt_contains_amount(
                adjustment.citation.excerpt,
                adjustment.tax_effect / source_scale,
            )
        )
        checks.append({
            "check": "tax_amount_located",
            "adjustment_index": index,
            "passed": tax_amount_located,
        })
        if not tax_amount_located:
            failures.append(_reason(
                "tax_amount_not_located",
                "The adjustment's tax effect could not be located in its cited SEC excerpt.",
                index,
            ))

        after_tax_amount_located = _excerpt_contains_amount(
            adjustment.citation.excerpt,
            adjustment.earnings_effect_after_tax / source_scale,
        )
        checks.append({
            "check": "after_tax_amount_located",
            "adjustment_index": index,
            "passed": after_tax_amount_located,
        })
        if not after_tax_amount_located:
            failures.append(_reason(
                "after_tax_amount_not_located",
                "The adjustment's after-tax earnings effect could not be located in its cited SEC excerpt.",
                index,
            ))

        if adjustment.category == "discrete_tax":
            tax_complete = (
                adjustment.pretax_earnings_effect is None
                and adjustment.tax_effect is not None
                and _close(
                    adjustment.tax_effect,
                    adjustment.earnings_effect_after_tax,
                    max(abs(adjustment.earnings_effect_after_tax) * 0.01, 1.0),
                )
            )
        else:
            tax_complete = (
                adjustment.pretax_earnings_effect is not None
                and adjustment.tax_effect is not None
                and _close(
                    adjustment.pretax_earnings_effect + adjustment.tax_effect,
                    adjustment.earnings_effect_after_tax,
                    max(abs(adjustment.earnings_effect_after_tax) * 0.01, 1.0),
                )
            )
        checks.append({
            "check": "after_tax_reconciliation",
            "adjustment_index": index,
            "passed": tax_complete,
        })
        if not tax_complete:
            failures.append(_reason("tax_reconciliation_incomplete", "The adjustment lacks a complete pre-tax to after-tax reconciliation.", index))

        deterministically_recurring = adjustment.category in (recurring_categories or set())
        allowed = (
            adjustment.category in ELIGIBLE_NORMALIZATION_CATEGORIES
            and not adjustment.recurring
            and not deterministically_recurring
        )
        if adjustment.include_in_normalized and not allowed:
            failures.append(_reason("category_not_normalizable", "This category is recurring or outside the conservative normalization policy.", index))
        if adjustment.include_in_normalized:
            included_effects.append(adjustment.earnings_effect_after_tax)

    normalized_net_income = reported_net_income - sum(included_effects)
    disclosed = extraction.disclosed_adjusted_net_income
    reconciliation_complete = disclosed is not None
    disclosed_amount_cited = False
    disclosed_unit = 1.0
    if disclosed is not None:
        net_income_citation = (
            extraction.company_adjusted.net_income_citation
            if extraction.company_adjusted is not None
            else None
        )
        disclosed_unit = (
            net_income_citation.source_unit_scale
            if net_income_citation is not None
            else 1.0
        )
        disclosed_tolerance = max(abs(disclosed) * 0.01, disclosed_unit)
        reconciliation_complete = _close(
            normalized_net_income,
            disclosed,
            disclosed_tolerance,
        )
        disclosed_amount_cited = (
            extraction.company_adjusted is not None
            and extraction.company_adjusted.adjusted_net_income is not None
            and net_income_citation is not None
            and _citation_is_located(
                net_income_citation,
                source_documents,
                source_metadata,
            )
            and _close(
                extraction.company_adjusted.adjusted_net_income,
                disclosed,
                disclosed_tolerance,
            )
            and _close(
                net_income_citation.source_amount
                * net_income_citation.source_unit_scale,
                disclosed,
                disclosed_tolerance,
            )
        )
    checks.append({"check": "disclosed_reconciliation_matches", "passed": reconciliation_complete})
    if not reconciliation_complete:
        failures.append(_reason("disclosed_reconciliation_mismatch", "The normalized result does not reconcile to a filing-disclosed adjusted result within tolerance."))
    checks.append({"check": "disclosed_adjusted_net_income_cited", "passed": disclosed_amount_cited})
    if not disclosed_amount_cited:
        failures.append(_reason("disclosed_adjusted_net_income_unverified", "The filing-disclosed adjusted net income could not be located and matched to its SEC citation."))

    net_income_verified = not failures
    adjusted_eps = extraction.disclosed_adjusted_diluted_eps
    eps_verified = False
    eps_failures: list[dict[str, Any]] = []
    if adjusted_eps is not None:
        eps_excerpt = (
            extraction.company_adjusted.diluted_eps_citation.excerpt.lower()
            if extraction.company_adjusted is not None
            and extraction.company_adjusted.diluted_eps_citation is not None
            else ""
        )
        eps_citation_ok = (
            extraction.company_adjusted is not None
            and extraction.company_adjusted.adjusted_diluted_eps is not None
            and extraction.company_adjusted.diluted_eps_citation is not None
            and _citation_is_located(
                extraction.company_adjusted.diluted_eps_citation,
                source_documents,
                source_metadata,
            )
            and _close(
                extraction.company_adjusted.adjusted_diluted_eps,
                adjusted_eps,
                max(abs(adjusted_eps) * 0.01, 0.01),
            )
            and _close(
                extraction.company_adjusted.diluted_eps_citation.source_amount
                * extraction.company_adjusted.diluted_eps_citation.source_unit_scale,
                adjusted_eps,
                max(abs(adjusted_eps) * 0.01, 0.01),
            )
            and "diluted" in eps_excerpt
            and ("eps" in eps_excerpt or "earnings per share" in eps_excerpt)
        )
        eps_verified = eps_citation_ok
        checks.append({"check": "adjusted_eps_disclosed_and_cited", "passed": eps_citation_ok})
        if not eps_citation_ok:
            eps_failures.append(_reason("adjusted_eps_unverified", "Adjusted diluted EPS is not both explicitly disclosed and source-reconciled."))

    validation_report = {
        "verified": net_income_verified,
        "eps_verified": eps_verified,
        "checks": checks,
        "failures": failures,
        "eps_failures": eps_failures,
        "sign_convention": (
            "Positive earnings_effect_after_tax raised reported earnings; "
            "normalized net income equals reported net income minus included effects."
        ),
        "gains_and_charges_treated_symmetrically": True,
    }
    result = {
        "verification_status": "verified" if net_income_verified else "flag_only",
        "reported_net_income": reported_net_income,
        "normalized_net_income": normalized_net_income if net_income_verified else None,
        "adjusted_eps": (
            adjusted_eps
            if net_income_verified and eps_verified
            else None
        ),
        "company_adjusted": (
            extraction.company_adjusted.model_dump(mode="json")
            if extraction.company_adjusted
            else None
        ),
        "adjustments": [
            adjustment.model_dump(mode="json")
            for adjustment in extraction.adjustments
        ],
        "notes": extraction.notes,
    }
    return result, validation_report
