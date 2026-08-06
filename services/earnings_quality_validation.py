from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date
from typing import Any, Literal, Optional, get_args

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
INHERENTLY_RECURRING_CATEGORIES = {
    "investment_fair_value",
    "derivative_fair_value",
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
    "investment_fair_value",
    "derivative_fair_value",
    "other",
]
ALLOWED_ADJUSTMENT_CATEGORIES = frozenset(get_args(AdjustmentCategory))


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
    period_end: date
    period_scope: Literal["quarter", "annual"]


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


_DERIVATIVE_FAIR_VALUE_TERMS = re.compile(
    r"(derivative liabilit|escrowed shares?|warrant.{0,80}fair value|"
    r"fair value.{0,80}(?:derivative|warrant)|"
    r"mark[- ]to[- ]market.{0,80}(?:derivative|warrant|escrowed shares?))",
    re.IGNORECASE,
)
_INVESTMENT_FAIR_VALUE_TERMS = re.compile(
    r"(unrealized (?:gain|loss).{0,80}(?:investment|securit)|"
    r"(?:investment|securit).{0,80}unrealized (?:gain|loss)|"
    r"(?:gain|loss).{0,80}(?:equity|debt) securit|"
    r"(?:equity securit|equity investment|strategic investment|debt and equity "
    r"securit|measurement alternative|investment).{0,80}(?:fair value|remeasur|revaluat)|"
    r"(?:fair value|remeasur|revaluat).{0,80}(?:equity securit|equity investment|"
    r"strategic investment|investment))",
    re.IGNORECASE,
)


def canonical_adjustment_category(
    category: str,
    label: str,
    excerpt: str = "",
) -> str:
    """Apply non-AI category overrides for volatile fair-value items."""
    evidence = _normalized_text(f"{label}\n{excerpt}")
    if _DERIVATIVE_FAIR_VALUE_TERMS.search(evidence):
        return "derivative_fair_value"
    if _INVESTMENT_FAIR_VALUE_TERMS.search(evidence):
        return "investment_fair_value"
    return category


def adjustment_has_usable_quantification(item: Any) -> bool:
    """Return whether a raw model candidate has finite, non-zero effect amounts."""
    citation = item.get("citation") if isinstance(item, dict) else None
    try:
        after_tax_effect = float(item.get("earnings_effect_after_tax"))
        source_amount = float(citation.get("source_amount"))
    except (AttributeError, TypeError, ValueError):
        return False
    return (
        math.isfinite(after_tax_effect)
        and after_tax_effect != 0
        and math.isfinite(source_amount)
        and source_amount != 0
    )


def unquantified_adjustment_candidates(
    ai_payload: Any,
) -> list[dict[str, Any]]:
    """Extract safe metadata for raw candidates omitted from amount validation."""
    adjustments = ai_payload.get("adjustments") if isinstance(ai_payload, dict) else None
    if not isinstance(adjustments, list):
        return []

    candidates: list[dict[str, Any]] = []
    for item in adjustments[:100]:
        if adjustment_has_usable_quantification(item):
            continue
        if isinstance(item, dict):
            label = str(item.get("label") or "unnamed candidate")
            raw_category = item.get("category")
            model_category = (
                raw_category if isinstance(raw_category, str) else "other"
            )
            citation = item.get("citation")
            excerpt = (
                citation.get("excerpt", "")
                if isinstance(citation, dict)
                else ""
            )
        else:
            label = "unnamed candidate"
            model_category = "other"
            excerpt = ""
        schema_category = (
            model_category
            if model_category in ALLOWED_ADJUSTMENT_CATEGORIES
            else "other"
        )
        candidates.append({
            "label": label,
            "model_category": model_category,
            "policy_category": canonical_adjustment_category(
                schema_category,
                label,
                str(excerpt),
            ),
            "failure_codes": ["unquantified_candidate"],
        })
    return candidates


def _fair_value_earnings_direction(
    category: str,
    label: str,
    excerpt: str,
) -> int | None:
    if category not in INHERENTLY_RECURRING_CATEGORIES:
        return None
    evidence = _normalized_text(f"{label}\n{excerpt}")
    if re.search(
        r"(?:increased|raised|boosted).{0,100}(?:net income|earnings)",
        evidence,
    ):
        return 1
    if re.search(
        r"(?:decreased|reduced|lowered).{0,100}(?:net income|earnings)",
        evidence,
    ):
        return -1
    normalized_label = _normalized_text(label)
    has_gain = bool(re.search(r"\bgains?\b", normalized_label))
    has_loss = bool(re.search(r"\bloss(?:es)?\b", normalized_label))
    if has_gain != has_loss:
        return 1 if has_gain else -1
    return None


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


def _excerpt_explicitly_discloses_zero_tax(excerpt: str) -> bool:
    normalized = _normalized_text(excerpt)
    return bool(re.search(
        r"(?:(?:no|zero) (?:income )?tax (?:effect|expense|benefit|impact)|"
        r"tax (?:effect|expense|benefit|impact)(?: was| of|:) ?\$?0(?:\.0+)?)",
        normalized,
    ))


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
    if not excerpt or excerpt not in source:
        return False
    return _excerpt_contains_amount(excerpt, citation.source_amount)


def _citation_period_is_located(
    citation: FilingCitation,
    *,
    expected_period_end: date,
    expected_period_type: str,
) -> bool:
    expected_scope = "annual" if expected_period_type == "annual" else "quarter"
    if (
        citation.period_end != expected_period_end
        or citation.period_scope != expected_scope
    ):
        return False

    excerpt_lines = [
        _normalized_text(line)
        for line in citation.excerpt.splitlines()
        if line.strip()
    ]
    excerpt = _normalized_text(citation.excerpt)
    marker = (
        f"selected_period_end={expected_period_end.isoformat()} "
        f"scope={expected_scope}"
    )
    if "[period_table " in excerpt:
        return (
            len(excerpt_lines) == 1
            and excerpt_lines[0].startswith("[period_table ")
            and marker in excerpt_lines[0]
        )

    year = str(expected_period_end.year)
    if year not in excerpt:
        return False
    cited_years = set(re.findall(r"\b(?:19|20)\d{2}\b", excerpt))
    if len(cited_years) > 1:
        # Comparative tables are ambiguous without a deterministic selected-
        # period marker, even when the expected year appears somewhere in them.
        return False
    if expected_scope == "annual":
        return any(
            phrase in excerpt
            for phrase in ("year ended", "years ended", "twelve months ended")
        )
    if "six months ended" in excerpt or "nine months ended" in excerpt:
        # An excerpt spanning both quarter and YTD columns cannot bind a value
        # to the quarter without the pre-extracted PERIOD_TABLE marker.
        return False
    return any(
        phrase in excerpt
        for phrase in (
            "quarter ended",
            "quarterly period ended",
            "three months ended",
            "first quarter",
            "second quarter",
            "third quarter",
            "fourth quarter",
        )
    )


def validate_filing_extraction(
    extraction: FilingEarningsQualityExtraction,
    *,
    expected_period_end: date,
    expected_period_type: str = "annual",
    expected_currency: str | None,
    reported_net_income: float,
    source_documents: dict[str, str],
    source_metadata: dict[str, dict[str, str]] | None = None,
    recurring_categories: set[str] | None = None,
    prevalidation_failures: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate model output and expose adjusted values only after every gate passes."""
    failures: list[dict[str, Any]] = [
        dict(failure) for failure in (prevalidation_failures or [])
    ]
    checks: list[dict[str, Any]] = []
    normalized_source_documents = {
        source_id: _normalized_text(source)
        for source_id, source in source_documents.items()
    }

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
    public_adjustments: list[dict[str, Any]] = []
    rejected_adjustments: list[dict[str, Any]] = []
    for index, adjustment in enumerate(extraction.adjustments):
        canonical_category = canonical_adjustment_category(
            adjustment.category,
            adjustment.label,
            adjustment.citation.excerpt,
        )
        category_unchanged = canonical_category == adjustment.category
        checks.append({
            "check": "category_policy_matches",
            "adjustment_index": index,
            "passed": category_unchanged,
        })
        if not category_unchanged:
            failures.append(_reason(
                "category_policy_override",
                "The model category conflicted with deterministic fair-value policy and was overridden.",
                index,
            ))

        policy_pretax_effect = adjustment.pretax_earnings_effect
        policy_tax_effect = adjustment.tax_effect
        policy_after_tax_effect = adjustment.earnings_effect_after_tax
        policy_source_amount = adjustment.citation.source_amount
        earnings_direction = _fair_value_earnings_direction(
            canonical_category,
            adjustment.label,
            adjustment.citation.excerpt,
        )
        effect_sign_policy_unchanged = True
        citation_sign_policy_unchanged = True
        if earnings_direction is not None:
            effect_sign_policy_unchanged = all(
                value is None
                or value == 0
                or (1 if value > 0 else -1) == earnings_direction
                for value in (policy_pretax_effect, policy_after_tax_effect)
            )
            citation_sign_policy_unchanged = (
                policy_source_amount == 0
                or (1 if policy_source_amount > 0 else -1) == earnings_direction
            )
            policy_source_amount = earnings_direction * abs(policy_source_amount)
        if earnings_direction is not None and not effect_sign_policy_unchanged:
            if policy_pretax_effect is not None:
                policy_pretax_effect = earnings_direction * abs(policy_pretax_effect)
            policy_after_tax_effect = earnings_direction * abs(policy_after_tax_effect)
            if policy_pretax_effect is not None and policy_tax_effect is not None:
                policy_tax_effect = policy_after_tax_effect - policy_pretax_effect
        sign_policy_unchanged = (
            effect_sign_policy_unchanged and citation_sign_policy_unchanged
        )
        checks.append({
            "check": "earnings_effect_sign_policy_matches",
            "adjustment_index": index,
            "passed": sign_policy_unchanged,
        })
        if not sign_policy_unchanged:
            failures.append(_reason(
                "earnings_effect_sign_override",
                "The extracted fair-value gain/loss sign conflicted with the cited earnings direction and was corrected.",
                index,
            ))

        citation_located = _citation_is_located(
            adjustment.citation,
            normalized_source_documents,
            source_metadata,
        )
        checks.append({
            "check": "citation_located",
            "adjustment_index": index,
            "passed": citation_located,
        })
        if not citation_located:
            failures.append(_reason("citation_not_located", "The cited excerpt could not be located in its saved SEC source.", index))

        citation_period_located = _citation_period_is_located(
            adjustment.citation,
            expected_period_end=expected_period_end,
            expected_period_type=expected_period_type,
        )
        checks.append({
            "check": "citation_period_matches",
            "adjustment_index": index,
            "passed": citation_period_located,
        })
        if not citation_period_located:
            failures.append(_reason(
                "citation_period_mismatch",
                "The cited amount is not bound to the selected quarter or year.",
                index,
            ))

        cited_amount = policy_source_amount * adjustment.citation.source_unit_scale
        comparable_amount = (
            policy_pretax_effect
            if policy_pretax_effect is not None
            else policy_after_tax_effect
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
        if policy_tax_effect is None:
            tax_amount_located = True
        elif policy_tax_effect == 0:
            tax_amount_located = _excerpt_explicitly_discloses_zero_tax(
                adjustment.citation.excerpt
            )
        else:
            tax_amount_located = _excerpt_contains_amount(
                adjustment.citation.excerpt,
                policy_tax_effect / source_scale,
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
            policy_after_tax_effect / source_scale,
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

        if canonical_category == "discrete_tax":
            tax_complete = (
                policy_pretax_effect is None
                and policy_tax_effect is not None
                and _close(
                    policy_tax_effect,
                    policy_after_tax_effect,
                    max(abs(policy_after_tax_effect) * 0.01, 1.0),
                )
            )
        else:
            tax_complete = (
                policy_pretax_effect is not None
                and policy_tax_effect is not None
                and _close(
                    policy_pretax_effect + policy_tax_effect,
                    policy_after_tax_effect,
                    max(abs(policy_after_tax_effect) * 0.01, 1.0),
                )
            )
        checks.append({
            "check": "after_tax_reconciliation",
            "adjustment_index": index,
            "passed": tax_complete,
        })
        if not tax_complete:
            failures.append(_reason("tax_reconciliation_incomplete", "The adjustment lacks a complete pre-tax to after-tax reconciliation.", index))

        deterministically_recurring = (
            canonical_category in (recurring_categories or set())
            or canonical_category in INHERENTLY_RECURRING_CATEGORIES
        )
        allowed = (
            canonical_category in ELIGIBLE_NORMALIZATION_CATEGORIES
            and not adjustment.recurring
            and not deterministically_recurring
        )
        if adjustment.include_in_normalized and not allowed:
            failures.append(_reason("category_not_normalizable", "This category is recurring or outside the conservative normalization policy.", index))
        sanitized_include = adjustment.include_in_normalized and allowed
        if sanitized_include:
            included_effects.append(policy_after_tax_effect)

        sanitized = adjustment.model_dump(mode="json")
        sanitized["category"] = canonical_category
        sanitized["pretax_earnings_effect"] = policy_pretax_effect
        sanitized["tax_effect"] = policy_tax_effect
        sanitized["earnings_effect_after_tax"] = policy_after_tax_effect
        sanitized["citation"]["source_amount"] = policy_source_amount
        sanitized["include_in_normalized"] = sanitized_include
        sanitized["recurring"] = bool(
            adjustment.recurring or deterministically_recurring
        )
        if citation_located and citation_period_located and amount_located:
            public_adjustments.append(sanitized)
        else:
            rejected_adjustments.append({
                "adjustment_index": index,
                "label": adjustment.label,
                "model_category": adjustment.category,
                "policy_category": canonical_category,
                "failure_codes": [
                    failure["code"]
                    for failure in failures
                    if failure.get("adjustment_index") == index
                ],
            })

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
                normalized_source_documents,
                source_metadata,
            )
            and _citation_period_is_located(
                net_income_citation,
                expected_period_end=expected_period_end,
                expected_period_type=expected_period_type,
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
                normalized_source_documents,
                source_metadata,
            )
            and _citation_period_is_located(
                extraction.company_adjusted.diluted_eps_citation,
                expected_period_end=expected_period_end,
                expected_period_type=expected_period_type,
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
        "rejected_adjustments": rejected_adjustments,
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
        "adjustments": public_adjustments,
        "notes": extraction.notes,
    }
    return result, validation_report
