import asyncio
import hashlib
import json
import logging
import math
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from threading import Lock
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from models import DecisionBriefCache
from services.deepseek_client import (
    generate_deepseek_text,
)

logger = logging.getLogger(__name__)


class AttributionGenerationError(RuntimeError):
    """Raised when the anomaly attribution provider cannot complete."""


PROMPT_VERSION = "decision-evidence-v19"
REPORT_SECTIONS = ("Core View", "Valuation", "Peer Context", "Risks")
SECTION_HEADING_RE = re.compile(
    r"(?im)^(?:#{1,4}\s*|\*\*)?"
    r"(Core View|Valuation|Peer Context|Risks)"
    r"(?:\*\*)?\s*:?\s*$"
)
CITATION_RE = re.compile(r"\[(E\d+)\]")
CITATION_CLUSTER_JOIN_RE = re.compile(
    r"^[\s,;/&]*(?:and\s*)?$",
    re.IGNORECASE,
)
ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
NUMERIC_CLAIM_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?P<sign_before_currency>[+\-−–—])?\s*"
    r"(?P<currency>[$€£])?\s*"
    r"(?P<sign_after_currency>[+\-−–—])?"
    r"(?P<number>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][+\-]?\d+)?)"
    r"(?P<ordinal>st|nd|rd|th)?"
    r"(?P<percent>%)?"
    r"(?:\s*(?P<scale>thousand|million|billion|trillion)|(?P<compact_scale>mm|mn|bn|[kmbt]))?"
    r"(?:\s*(?P<basis_points>bps?|basis\s+points?))?"
    r"(?P<multiple>[x×])?"
    r"(?![A-Za-z0-9])",
    re.IGNORECASE,
)
STRING_NUMBER_RE = re.compile(r"(?<!\d)[+\-−–—]?\d+(?:\.\d+)?")
PERCENT_STRING_NUMBER_RE = re.compile(
    r"(?<!\d)([+\-−–—]?\d+(?:\.\d+)?)\s*%"
)
SCALE_DIVISORS = {
    "thousand": 1_000.0,
    "million": 1_000_000.0,
    "billion": 1_000_000_000.0,
    "trillion": 1_000_000_000_000.0,
}
COMPACT_SCALE_DIVISORS = {
    "k": 1_000.0,
    "m": 1_000_000.0,
    "mm": 1_000_000.0,
    "mn": 1_000_000.0,
    "b": 1_000_000_000.0,
    "bn": 1_000_000_000.0,
    "t": 1_000_000_000_000.0,
}
RATIO_VALUE_KEYS = {
    "fcf_growth_rate",
    "wacc",
    "perpetual_growth",
    "upside_downside",
    "gross_margin",
    "operating_margin",
    "net_profit_margin",
    "roe",
    "roa",
    "roic",
    "sales_growth_ttm",
    "sales_growth_3yr",
    "sales_growth_5yr",
    "eps_growth_ttm",
    "eps_growth_3yr",
    "eps_growth_5yr",
    "fcf_net_income_conversion",
}
RATIO_WARNING_METRICS = {"fcf_net_income_conversion", "margin_compression"}
CURRENCY_VALUE_KEYS = {
    "price",
    "current_price",
    "intrinsic_value_per_share",
    "revenue",
    "gross_profit",
    "operating_income",
    "net_income",
    "fcf",
    "free_cash_flow",
    "cash",
    "cash_and_short_term_investments",
    "debt",
    "total_debt",
    "equity",
    "stockholder_equity",
    "enterprise_value",
    "equity_value",
    "projected_fcf",
    "present_value_explicit_fcf",
    "present_value_terminal",
    "terminal_value",
}
MULTIPLE_VALUE_KEYS = {"debt_to_equity"}
CURRENCY_WARNING_METRICS = {
    "revenue_change",
    "fcf",
    "fcf_change",
    "cash_change",
}
MULTIPLE_WARNING_METRICS = {"debt_to_equity", "debt_to_equity_change"}
NEGATIVE_DIRECTION_BEFORE_RE = re.compile(
    r"\b(?:downside|decline|decrease|drop|loss)(?:\s+(?:of|is|was|by))?\s*$"
    r"|\b(?:fell|declined|decreased|dropped|lost|down)(?:\s+by)?\s*$"
    r"|(?<!not )(?<!non-)\bnegative(?:\s+[A-Za-z][A-Za-z/-]*){0,6}\s*$",
    re.IGNORECASE,
)
POSITIVE_DIRECTION_BEFORE_RE = re.compile(
    r"\b(?:upside|increase|gain|premium)(?:\s+(?:of|is|was|by))?\s*$"
    r"|\b(?:rose|increased|grew|gained|up)(?:\s+by)?\s*$"
    r"|(?<!not )(?<!non-)\bpositive(?:\s+[A-Za-z][A-Za-z/-]*){0,6}\s*$",
    re.IGNORECASE,
)
NEGATIVE_DIRECTION_AFTER_RE = re.compile(
    r"^\s*(?:downside|below|lower|negative|decline|decrease|drop|loss)\b",
    re.IGNORECASE,
)
POSITIVE_DIRECTION_AFTER_RE = re.compile(
    r"^\s*(?:upside|above|higher|positive|increase|gain|premium)\b",
    re.IGNORECASE,
)
NEGATED_NEGATIVE_BEFORE_RE = re.compile(
    r"\b(?:not(?:\s+[A-Za-z-]+){0,3}|never(?:\s+[A-Za-z-]+){0,3}|"
    r"no\s+longer(?:\s+[A-Za-z-]+){0,3})\s+negative"
    r"(?:\s+[A-Za-z][A-Za-z/-]*){0,6}\s*$",
    re.IGNORECASE,
)
NEGATED_POSITIVE_BEFORE_RE = re.compile(
    r"\b(?:not(?:\s+[A-Za-z-]+){0,3}|never(?:\s+[A-Za-z-]+){0,3}|"
    r"no\s+longer(?:\s+[A-Za-z-]+){0,3})\s+positive"
    r"(?:\s+[A-Za-z][A-Za-z/-]*){0,6}\s*$",
    re.IGNORECASE,
)
AFFIRMING_NEGATIVE_BEFORE_RE = re.compile(
    r"\bnot\s+(?:only|merely|just|simply)"
    r"(?:\s+(?!no\b|not\b|never\b)[A-Za-z-]+){0,3}\s+negative"
    r"(?:\s+[A-Za-z][A-Za-z/-]*){0,6}\s*$",
    re.IGNORECASE,
)
AFFIRMING_POSITIVE_BEFORE_RE = re.compile(
    r"\bnot\s+(?:only|merely|just|simply)"
    r"(?:\s+(?!no\b|not\b|never\b)[A-Za-z-]+){0,3}\s+positive"
    r"(?:\s+[A-Za-z][A-Za-z/-]*){0,6}\s*$",
    re.IGNORECASE,
)
IDENTITY_VALUE_PREDICATE_RE = re.compile(
    r"^\s+(?:is|was|were|equals?|equaled|represented|reached|totaled)\s+"
    r"(?:the\s+)?(?:published\s+)?(?:current\s+)?"
    r"(?:assets|book|cash|debt|earnings|ebitda|enterprise\s+value|equity|"
    r"fcf|free\s+cash\s+flow|multiple|price|revenue|sales|shares?|turnover)\b",
    re.IGNORECASE,
)
IDENTITY_SUBJECT_FOLLOW_RE = re.compile(
    r"^\s*(?:['’]s\b|[,;:]|(?:is|was|has|had|does|did|reports?|reported|"
    r"shows?|showed|faces?|faced|remains?|remained|trades?|traded|covers?|"
    r"covered)\b)",
    re.IGNORECASE,
)
IDENTITY_SECURITY_FOLLOW_RE = re.compile(
    r"^\s+(?:shares?\s+(?:trades?|traded|are\s+(?:trading|priced)|"
    r"closed|opened|rose|fell)|stock\s+(?:trades?|traded|is\s+(?:trading|"
    r"priced)|price|closed|opened|rose|fell))\b",
    re.IGNORECASE,
)
LEGAL_COMPANY_SUFFIX_RE = re.compile(
    r"(?:,\s*|\s+)(?:incorporated|inc\.?|corporation|corp\.?|company|co\.?|"
    r"limited|ltd\.?|llc|plc|n\.?v\.?|s\.?a\.?|a\.?g\.?|s\.?e\.?)\.?$",
    re.IGNORECASE,
)
IDENTITY_PREFIX_RE = re.compile(
    r"(?:^|\b(?:at|for|from|by|about|with|within|inside|regarding|company|"
    r"issuer|business|peer)\s+)$",
    re.IGNORECASE,
)
CLAUSE_BREAK_RE = re.compile(r"(?:[;]|\b(?:while|whereas|but)\b)", re.IGNORECASE)
SEMANTIC_VALUE_KEYS = {
    "revenue": "revenue",
    "sales": "revenue",
    "gross_profit": "gross_profit",
    "operating_income": "operating_income",
    "net_income": "net_income",
    "fcf": "fcf",
    "free_cash_flow": "fcf",
    "cash": "cash",
    "cash_and_short_term_investments": "cash",
    "debt": "debt",
    "total_debt": "debt",
    "shares": "shares",
    "shares_outstanding": "shares",
    "stockholder_equity": "stockholder_equity",
    "equity": "stockholder_equity",
    "gross_margin": "gross_margin",
    "operating_margin": "operating_margin",
    "net_profit_margin": "net_profit_margin",
    "fcf_net_income_conversion": "fcf_net_income_conversion",
    "debt_to_equity": "debt_to_equity",
    "price": "current_price",
    "current_price": "current_price",
    "intrinsic_value_per_share": "intrinsic_value_per_share",
    "enterprise_value": "enterprise_value",
    "equity_value": "equity_value",
    "projected_fcf": "projected_fcf",
    "present_value_explicit_fcf": "present_value_explicit_fcf",
    "present_value_terminal": "present_value_terminal",
    "terminal_value": "terminal_value",
    "statement_count": "statement_count",
    "fcf_growth_rate": "fcf_growth_rate",
    "wacc": "wacc",
    "perpetual_growth": "perpetual_growth",
    "upside_downside": "upside_downside",
    "sales_growth_ttm": "sales_growth_ttm",
    "sales_growth_3yr": "sales_growth_3yr",
    "sales_growth_5yr": "sales_growth_5yr",
    "eps_growth_ttm": "eps_growth_ttm",
    "eps_growth_3yr": "eps_growth_3yr",
    "eps_growth_5yr": "eps_growth_5yr",
    "roe": "roe",
    "roa": "roa",
    "roic": "roic",
    "pe_ratio": "pe_ratio",
    "forward_pe": "forward_pe",
    "peg_ratio": "peg_ratio",
    "price_to_sales": "price_to_sales",
    "ps_ratio": "price_to_sales",
    "price_to_book": "price_to_book",
    "pb_ratio": "price_to_book",
    "price_to_fcf": "price_to_fcf",
    "price_fcf": "price_to_fcf",
    "ev_to_ebitda": "ev_to_ebitda",
    "ev_ebitda": "ev_to_ebitda",
}
SEMANTIC_CLAIM_PATTERNS = {
    "fcf": re.compile(r"\b(?:free\s+cash\s+flow|fcf)\b", re.IGNORECASE),
    "projected_fcf": re.compile(r"\bprojected\s+(?:free\s+cash\s+flow|fcf)\b", re.IGNORECASE),
    "present_value_explicit_fcf": re.compile(r"\b(?:present\s+value\s+of\s+)?explicit(?:-period)?\s+(?:fcf|cash\s+flow)\b", re.IGNORECASE),
    "present_value_terminal": re.compile(r"\bpresent\s+value\s+of\s+(?:the\s+)?terminal(?:\s+value)?\b", re.IGNORECASE),
    "terminal_value": re.compile(r"\bterminal\s+value\b", re.IGNORECASE),
    "enterprise_value": re.compile(r"\benterprise\s+value\b", re.IGNORECASE),
    "equity_value": re.compile(r"\bequity\s+value\b", re.IGNORECASE),
    "intrinsic_value_per_share": re.compile(r"\b(?:intrinsic\s+value(?:\s+per\s+share)?|(?:bear|base|bull)(?:-case)?\s+value|(?:bear|base|bull)\s+case)\b", re.IGNORECASE),
    "upside_downside": re.compile(r"\b(?:upside|downside)\b|\b(?:above|below)\s+(?:the\s+)?current\s+price\b|\bvs\.?\s+(?:the\s+)?current\s+price\b", re.IGNORECASE),
    "current_price": re.compile(r"\b(?:current|share|stock)\s+price\b", re.IGNORECASE),
    "revenue": re.compile(r"\b(?:revenue|sales)\b", re.IGNORECASE),
    "gross_profit": re.compile(r"\bgross\s+profit\b", re.IGNORECASE),
    "operating_income": re.compile(r"\boperating\s+income\b", re.IGNORECASE),
    "net_income": re.compile(r"\bnet\s+income\b", re.IGNORECASE),
    "gross_margin": re.compile(r"\bgross\s+margin\b", re.IGNORECASE),
    "operating_margin": re.compile(r"\boperating\s+margin\b", re.IGNORECASE),
    "net_profit_margin": re.compile(r"\bnet(?:\s+profit)?\s+margin\b", re.IGNORECASE),
    "fcf_net_income_conversion": re.compile(r"\b(?:fcf|free\s+cash\s+flow)(?:-to-|\s+to\s+|\s*/\s*)net\s+income\s+conversion\b", re.IGNORECASE),
    "debt_to_equity": re.compile(r"\bdebt(?:-to-|\s+to\s+|\s*/\s*)equity\b", re.IGNORECASE),
    "cash": re.compile(r"\bcash(?:\s+and\s+short-term\s+investments)?\b", re.IGNORECASE),
    "debt": re.compile(r"\b(?:total\s+)?debt\b", re.IGNORECASE),
    "shares": re.compile(r"\b(?:share\s+count|shares?\s+outstanding|dilution)\b", re.IGNORECASE),
    "stockholder_equity": re.compile(r"\b(?:stockholders?'?|shareholders?'?)\s+equity\b", re.IGNORECASE),
    "statement_count": re.compile(r"\b(?:statement\s+count|statements?)\b", re.IGNORECASE),
    "fcf_growth_rate": re.compile(r"\b(?:fcf|free\s+cash\s+flow)\s+growth\b", re.IGNORECASE),
    "wacc": re.compile(r"\bwacc\b", re.IGNORECASE),
    "perpetual_growth": re.compile(r"\b(?:perpetual|terminal)\s+growth\b", re.IGNORECASE),
    "sales_growth_ttm": re.compile(r"\b(?:ttm\s+sales\s+growth|sales\s+growth\s*\(?ttm\)?)\b", re.IGNORECASE),
    "sales_growth_3yr": re.compile(r"\b(?:(?:3[- ]year|3y)\s+sales\s+growth|sales\s+growth\s*\(?(?:3y|3[- ]year)\)?)\b", re.IGNORECASE),
    "sales_growth_5yr": re.compile(r"\b(?:(?:5[- ]year|5y)\s+sales\s+growth|sales\s+growth\s*\(?(?:5y|5[- ]year)\)?)\b", re.IGNORECASE),
    "eps_growth_ttm": re.compile(r"\b(?:ttm\s+eps\s+growth|eps\s+growth\s*\(?ttm\)?)\b", re.IGNORECASE),
    "eps_growth_3yr": re.compile(r"\b(?:(?:3[- ]year|3y)\s+eps\s+growth|eps\s+growth\s*\(?(?:3y|3[- ]year)\)?)\b", re.IGNORECASE),
    "eps_growth_5yr": re.compile(r"\b(?:(?:5[- ]year|5y)\s+eps\s+growth|eps\s+growth\s*\(?(?:5y|5[- ]year)\)?)\b", re.IGNORECASE),
    "roe": re.compile(r"\b(?:roe|return\s+on\s+equity)\b", re.IGNORECASE),
    "roa": re.compile(r"\b(?:roa|return\s+on\s+assets)\b", re.IGNORECASE),
    "roic": re.compile(r"\b(?:roic|return\s+on\s+invested\s+capital)\b", re.IGNORECASE),
    "pe_ratio": re.compile(r"\b(?:p/e|price[- ]to[- ]earnings)\b", re.IGNORECASE),
    "forward_pe": re.compile(r"\bforward\s+(?:p/e|pe)\b", re.IGNORECASE),
    "peg_ratio": re.compile(r"\b(?:peg|price/earnings[- ]to[- ]growth)\b", re.IGNORECASE),
    "price_to_sales": re.compile(r"\b(?:p\s*/\s*s|price[- ]to[- ]sales|price\s*/\s*sales)\b", re.IGNORECASE),
    "price_to_book": re.compile(r"\b(?:p\s*/\s*b|price[- ]to[- ]book|price\s*/\s*book)\b", re.IGNORECASE),
    "price_to_fcf": re.compile(r"\b(?:price[- ]to[- ]fcf|price\s*/\s*(?:fcf|free\s+cash\s+flow))\b", re.IGNORECASE),
    "ev_to_ebitda": re.compile(r"\b(?:ev\s*/\s*ebitda|enterprise[- ]value[- ]to[- ]ebitda)\b", re.IGNORECASE),
}
PERIOD_SCOPED_SEMANTICS = {
    "revenue", "gross_profit", "operating_income", "net_income", "fcf",
    "gross_margin", "operating_margin", "net_profit_margin", "cash", "debt",
    "shares", "stockholder_equity", "debt_to_equity",
}
CURRENT_PERIOD_RE = re.compile(
    r"\b(?:current(?:\s+ttm)?|latest(?:\s+ttm)?|this\s+period)\b",
    re.IGNORECASE,
)
PREVIOUS_PERIOD_RE = re.compile(
    r"\b(?:previous(?:ly|\s+ttm)?|prior(?:[- ]year|\s+ttm)?|year[- ]ago|last\s+year)\b",
    re.IGNORECASE,
)
SEMANTIC_COORDINATION_RE = re.compile(
    r"\b(?:rather\s+than|instead\s+of|versus|vs\.?|and|or)\b",
    re.IGNORECASE,
)
CHANGE_CONTEXT_RE = re.compile(
    r"\b(?:change[ds]?|declin(?:e|ed|ing)|decreas(?:e|ed|ing)|"
    r"drop(?:ped|ping)?|compress(?:ion|ed|ing)?|increas(?:e|ed|ing)|"
    r"rose|risen|fell|fallen|grew|grown|dilut(?:ion|ed|ing)|"
    r"year[- ]over[- ]year|yoy)\b",
    re.IGNORECASE,
)
AMBIGUOUS_SEMANTIC_KEY = "__ambiguous__"
MAX_REPORT_GENERATION_ATTEMPTS = 2
_REPORT_GENERATION_LOCKS_GUARD = Lock()
_REPORT_GENERATION_LOCKS: dict[tuple[str, str, str], asyncio.Lock] = {}
_REPORT_GENERATION_LOCK_USERS: dict[tuple[str, str, str], int] = {}

PEER_SCOPE_RE = re.compile(r"\b(industry|sector)\b", re.IGNORECASE)
PERCENTILE_TYPE_RE = re.compile(
    r"\b(raw|desirability|summary)\s+percentile\b",
    re.IGNORECASE,
)
COVERAGE_MINIMUM_RE = re.compile(
    r"\b(?:minimum|requires?|required|threshold|at\s+least)\b",
    re.IGNORECASE,
)
COVERAGE_OBSERVATION_AFTER_RE = re.compile(
    r"^\s*(?:valid\s+)?(?:(?:industry|sector)\s+)?"
    r"(?:observations?|peers?|companies|values)\b",
    re.IGNORECASE,
)
PERCENTILE_AFTER_RE = re.compile(r"^\s*percentile\b", re.IGNORECASE)
PERCENTILE_BEFORE_RE = re.compile(
    r"\b(?:percentile|rank(?:s|ed|ing)?)"
    r"(?:\s+(?:is|was|of|at|in))?(?:\s+the)?\s*$",
    re.IGNORECASE,
)
CHANGE_AMOUNT_BEFORE_RE = re.compile(
    r"\b(?:change[ds]?|declin(?:e|ed|ing)|decreas(?:e|ed|ing)|"
    r"drop(?:ped|ping)?|compress(?:ion|ed|ing)?|increas(?:e|ed|ing)|"
    r"rose|risen|fell|fallen|grew|grown|dilut(?:ion|ed|ing))"
    r"(?:\s+(?:was|is|by|of))?\s*$",
    re.IGNORECASE,
)
PROJECTION_YEAR_RE = re.compile(r"\byear\s+(\d+)\b", re.IGNORECASE)
PROJECTION_INITIAL_RE = re.compile(
    r"\b(?:initial|first(?:-year)?|year\s+one)\b",
    re.IGNORECASE,
)
PROJECTION_FINAL_RE = re.compile(
    r"\b(?:final|last|fifth(?:-year)?|year\s+five)\b",
    re.IGNORECASE,
)
QUALITATIVE_TREND_PATTERNS = (
    (
        "+",
        re.compile(
            r"\b(?:grow(?:s|ing)?|grew|grown|ris(?:e|es|ing)|rose|risen|"
            r"increas(?:e|es|ed|ing)|improv(?:e|es|ed|ing)|"
            r"expand(?:s|ed|ing)|strengthen(?:s|ed|ing)|"
            r"accelerat(?:e|es|ed|ing)|recover(?:s|ed|ing)|booming|higher)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "-",
        re.compile(
            r"\b(?:declin(?:e|es|ed|ing)|decreas(?:e|es|ed|ing)|"
            r"fall(?:s|ing)?|fell|fallen|drop(?:s|ped|ping)|"
            r"contract(?:s|ed|ing)|deteriorat(?:e|es|ed|ing)|"
            r"weaken(?:s|ed|ing)|shrink(?:s|ing)?|shrank|shrunk|"
            r"collaps(?:es|ed|ing)|lower)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "stable",
        re.compile(
            r"\b(?:stable|flat|unchanged|steady)\b",
            re.IGNORECASE,
        ),
    ),
)
QUALITATIVE_SIGN_PATTERNS = (
    ("+", re.compile(r"\bpositive\b", re.IGNORECASE)),
    ("-", re.compile(r"\bnegative\b", re.IGNORECASE)),
)
EVALUATIVE_QUALITATIVE_RE = re.compile(
    r"\b(?:strong(?:er|est)?|weak(?:er|est)?|cheap(?:er|est)?|"
    r"expensive|attractive|unattractive|healthy|poor|robust|elevated|"
    r"depressed|superior|inferior|favou?rable|unfavou?rable|compelling|"
    r"concerning|resilient)\b",
    re.IGNORECASE,
)
QUALITATIVE_NEGATION_BEFORE_RE = re.compile(
    r"(?:\b(?:not|never|no\s+longer|hardly|scarcely|without)\b"
    r"(?:\s+[A-Za-z'-]+){0,3}|\b[A-Za-z]+n['’]t"
    r"(?:\s+[A-Za-z'-]+){0,3})\s*$",
    re.IGNORECASE,
)
QUALITATIVE_RULE_LABEL_AFTER_RE = re.compile(
    r"^\s+(?:warning|rule|check|risk|threshold|assessment)\b",
    re.IGNORECASE,
)
WARNING_LABEL_RE = re.compile(
    r"\b(?:warning|rule|check|risk|threshold|assessment)\b",
    re.IGNORECASE,
)
WARNING_TRIGGER_RE = re.compile(
    r"\btrigger(?:s|ed|ing)?\b",
    re.IGNORECASE,
)
WARNING_TRIGGER_NEGATION_BEFORE_RE = re.compile(
    r"(?:\bno(?:\s+[A-Za-z'-]+){0,8}|"
    r"\bfail(?:s|ed|ing)?\s+to)\s*$",
    re.IGNORECASE,
)


class EvidenceCitationError(ValueError):
    """Raised when a generated brief is not bound to supplied evidence."""


@dataclass
class _EvidenceNumericContext:
    numeric_values: list[float]
    ratio_values: list[float]
    multiple_values: list[float]
    currency_values: list[float]
    string_values: list[float]
    percent_string_values: list[float]
    strings: set[str]
    semantic_numeric_values: dict[str, list[float]]
    semantic_ratio_values: dict[str, list[float]]
    semantic_multiple_values: dict[str, list[float]]
    semantic_currency_values: dict[str, list[float]]
    semantic_percent_string_values: dict[str, list[float]]
    semantic_string_values: dict[str, list[float]]


def build_evidence_hash(decision_support: dict, model: str) -> str:
    metadata = decision_support.get("metadata") or {}
    payload = {
        "prompt_version": PROMPT_VERSION,
        "model": model,
        "prompt_identity": {
            "ticker": metadata.get("ticker"),
            "company_name": metadata.get("company_name"),
            "currency": metadata.get("currency"),
        },
        "source_dates": {
            key: value
            for key, value in metadata.items()
            if key.endswith("_date") or key.endswith("_at")
        },
        "valuation_assumptions": [
            item.get("assumptions")
            for item in (decision_support.get("valuation") or {}).get("scenarios", [])
        ],
        "evidence": [
            {
                "id": item.get("id"),
                "available": item.get("available"),
                "source_date": item.get("source_date"),
                "value": item.get("value"),
            }
            for item in decision_support.get("evidence", [])
        ],
    }
    serialized = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _company_identity_strings(company_name: str, ticker: str) -> tuple[str, ...]:
    aliases = [value for value in (company_name.strip(), ticker.strip()) if value]
    stripped = company_name.strip()
    while stripped:
        without_suffix = LEGAL_COMPANY_SUFFIX_RE.sub("", stripped).strip()
        if without_suffix == stripped:
            break
        stripped = without_suffix
        aliases.append(stripped)
    if stripped.lower().startswith("the "):
        aliases.append(stripped[4:].strip())
    for value in tuple(aliases):
        numeric_name_prefix = re.match(
            r"^[+\-−–—]?\d+(?:\.\d+)?(?:[A-Za-z×]+)?",
            value,
        )
        if numeric_name_prefix:
            aliases.append(numeric_name_prefix.group(0))
    return tuple(dict.fromkeys(aliases))


def validate_evidence_citations(content: str, evidence_ids: set[str]) -> None:
    if not content or len(content) > 20_000:
        raise EvidenceCitationError("The generated brief is empty or too long.")

    headings = list(SECTION_HEADING_RE.finditer(content))
    sections: dict[str, str] = {}
    for index, match in enumerate(headings):
        start = match.end()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(content)
        sections[match.group(1)] = content[start:end]

    missing_sections = [name for name in REPORT_SECTIONS if name not in sections]
    if missing_sections:
        raise EvidenceCitationError(
            f"Missing required analytical sections: {', '.join(missing_sections)}."
        )

    cited_ids = set(CITATION_RE.findall(content))
    unknown_ids = sorted(cited_ids - evidence_ids)
    if unknown_ids:
        raise EvidenceCitationError(
            f"Unknown evidence citations: {', '.join(unknown_ids)}."
        )
    for name in REPORT_SECTIONS:
        section_ids = set(CITATION_RE.findall(sections[name]))
        if not section_ids:
            raise EvidenceCitationError(
                f"The {name} section does not cite decision-support evidence."
            )
        if not section_ids <= evidence_ids:
            raise EvidenceCitationError(
                f"The {name} section contains an absent evidence ID."
            )
        for sentence in _claim_segments(sections[name]):
            if not CITATION_RE.search(sentence):
                raise EvidenceCitationError(
                    f"Every analytical sentence in the {name} section must "
                    "contain a local evidence citation."
                )


def _evidence_numeric_context(item: dict) -> _EvidenceNumericContext:
    """Collect explicit evidence numbers without treating an evidence ID as a fact."""
    numeric_values: list[float] = []
    ratio_values: list[float] = []
    multiple_values: list[float] = []
    currency_values: list[float] = []
    string_values: list[float] = []
    percent_string_values: list[float] = []
    strings: set[str] = set()
    semantic_numeric_values: dict[str, list[float]] = {}
    semantic_ratio_values: dict[str, list[float]] = {}
    semantic_multiple_values: dict[str, list[float]] = {}
    semantic_currency_values: dict[str, list[float]] = {}
    semantic_percent_string_values: dict[str, list[float]] = {}
    semantic_string_values: dict[str, list[float]] = {}

    def append_semantic(
        target: dict[str, list[float]],
        semantic_hint: Optional[str],
        number: float,
        period_scope: Optional[str] = None,
        semantic_aliases: tuple[str, ...] = (),
    ) -> None:
        for hint in dict.fromkeys((semantic_hint, *semantic_aliases)):
            if not hint:
                continue
            target.setdefault(hint, []).append(number)
            if period_scope:
                target.setdefault(f"{period_scope}:{hint}", []).append(number)

    def visit(
        value: object,
        *,
        ratio_hint: bool = False,
        multiple_hint: bool = False,
        currency_hint: bool = False,
        semantic_hint: Optional[str] = None,
        period_scope: Optional[str] = None,
        semantic_aliases: tuple[str, ...] = (),
        peer_scope: Optional[str] = None,
        peer_summary_scope: Optional[str] = None,
    ) -> None:
        if isinstance(value, bool):
            return
        elif isinstance(value, (int, float)):
            number = float(value)
            if math.isfinite(number):
                numeric_values.append(number)
                append_semantic(
                    semantic_numeric_values,
                    semantic_hint,
                    number,
                    period_scope,
                    semantic_aliases,
                )
                if ratio_hint:
                    ratio_values.append(number)
                    append_semantic(
                        semantic_ratio_values,
                        semantic_hint,
                        number,
                        period_scope,
                        semantic_aliases,
                    )
                if multiple_hint:
                    multiple_values.append(number)
                    append_semantic(
                        semantic_multiple_values,
                        semantic_hint,
                        number,
                        period_scope,
                        semantic_aliases,
                    )
                if currency_hint:
                    currency_values.append(number)
                    append_semantic(
                        semantic_currency_values,
                        semantic_hint,
                        number,
                        period_scope,
                        semantic_aliases,
                    )
        elif isinstance(value, str):
            strings.add(value)
            numeric_text = ISO_DATE_RE.sub("", value)
            for match in STRING_NUMBER_RE.finditer(numeric_text):
                raw_token = match.group(0)
                raw_value = raw_token.replace("−", "-").replace("–", "-").replace("—", "-")
                try:
                    string_value = float(raw_value)
                except ValueError:
                    continue
                if raw_token[0] not in "+-−–—":
                    before = numeric_text[max(0, match.start() - 48):match.start()]
                    after = numeric_text[match.end():match.end() + 32]
                    if (
                        NEGATIVE_DIRECTION_BEFORE_RE.search(before)
                        or NEGATIVE_DIRECTION_AFTER_RE.search(after)
                    ):
                        string_value = -abs(string_value)
                string_values.append(string_value)
                append_semantic(
                    semantic_string_values,
                    semantic_hint,
                    string_value,
                    period_scope,
                    semantic_aliases,
                )
            for match in PERCENT_STRING_NUMBER_RE.finditer(numeric_text):
                raw_token = match.group(1)
                raw_value = raw_token.replace("−", "-").replace("–", "-").replace("—", "-")
                try:
                    percent_value = float(raw_value)
                except ValueError:
                    continue
                if raw_token[0] not in "+-−–—":
                    before = numeric_text[max(0, match.start() - 48):match.start()]
                    after = numeric_text[match.end():match.end() + 32]
                    if (
                        NEGATIVE_DIRECTION_BEFORE_RE.search(before)
                        or NEGATIVE_DIRECTION_AFTER_RE.search(after)
                    ):
                        percent_value = -abs(percent_value)
                percent_string_values.append(percent_value)
                append_semantic(
                    semantic_percent_string_values,
                    semantic_hint,
                    percent_value,
                    period_scope,
                    semantic_aliases,
                )
        elif isinstance(value, dict):
            format_is_percent = value.get("format") == "percent"
            format_is_multiple = value.get("format") in {"multiple", "ratio"}
            summary_scope_value = value.get("summary_scope")
            dict_summary_scope = (
                str(summary_scope_value).lower()
                if summary_scope_value in {"industry", "sector"}
                else peer_summary_scope
            )
            warning_metric = value.get("metric")
            warning_semantic = SEMANTIC_VALUE_KEYS.get(str(warning_metric))
            if warning_semantic is None:
                warning_semantic = SEMANTIC_VALUE_KEYS.get(
                    str(value.get("evidence_metric"))
                )
            for key, nested in value.items():
                normalized_key = str(key).lower()
                if (
                    normalized_key == "id"
                    or normalized_key.endswith("_id")
                    or normalized_key.endswith("_ids")
                ):
                    continue
                nested_is_ratio = (
                    ratio_hint
                    or normalized_key in RATIO_VALUE_KEYS
                    or (normalized_key == "metric_value" and format_is_percent)
                    or (
                        normalized_key in {"current", "previous"}
                        and warning_metric in RATIO_WARNING_METRICS
                    )
                )
                nested_is_multiple = (
                    multiple_hint
                    or normalized_key in MULTIPLE_VALUE_KEYS
                    or (normalized_key == "metric_value" and format_is_multiple)
                    or (
                        normalized_key in {"current", "previous"}
                        and warning_metric in MULTIPLE_WARNING_METRICS
                    )
                )
                nested_is_currency = (
                    currency_hint
                    or normalized_key in CURRENCY_VALUE_KEYS
                    or (
                        normalized_key in {"current", "previous"}
                        and warning_metric in CURRENCY_WARNING_METRICS
                    )
                )
                nested_semantic = SEMANTIC_VALUE_KEYS.get(normalized_key)
                peer_semantic = SEMANTIC_VALUE_KEYS.get(
                    str(value.get("metric_key"))
                )
                nested_aliases: tuple[str, ...] = ()
                nested_peer_scope = peer_scope
                if normalized_key in {"industry", "sector"}:
                    nested_peer_scope = normalized_key
                if normalized_key == "metric_value" and peer_semantic:
                    nested_semantic = peer_semantic
                elif normalized_key == "summary_percentile" and peer_semantic:
                    nested_semantic = f"percentile:summary:{peer_semantic}"
                    if dict_summary_scope:
                        nested_aliases = (
                            f"percentile:{dict_summary_scope}:summary:"
                            f"{peer_semantic}",
                        )
                elif normalized_key in {
                    "raw_percentile",
                    "desirability_percentile",
                } and peer_semantic and peer_scope:
                    percentile_type = normalized_key.removesuffix("_percentile")
                    nested_semantic = (
                        f"percentile:{peer_scope}:{percentile_type}:"
                        f"{peer_semantic}"
                    )
                    if peer_scope == dict_summary_scope:
                        nested_aliases = (
                            f"percentile:{percentile_type}:{peer_semantic}",
                        )
                elif normalized_key in {
                    "observation_count",
                    "minimum_observations",
                } and peer_semantic and peer_scope:
                    coverage_role = (
                        "observation"
                        if normalized_key == "observation_count"
                        else "minimum"
                    )
                    nested_semantic = (
                        f"coverage:{peer_scope}:{coverage_role}:{peer_semantic}"
                    )
                    if peer_scope == dict_summary_scope:
                        nested_aliases = (
                            f"coverage:summary:{coverage_role}:{peer_semantic}",
                        )
                if normalized_key == "message" and warning_semantic:
                    nested_semantic = f"change:{warning_semantic}"
                if (
                    nested_semantic is None
                    and normalized_key in {"current", "previous"}
                    and warning_semantic
                ):
                    nested_semantic = warning_semantic
                nested_period_scope = period_scope
                if normalized_key in {"current_ttm", "latest_balance", "current"}:
                    nested_period_scope = "current"
                elif normalized_key in {
                    "previous_ttm",
                    "prior_year_balance",
                    "previous",
                }:
                    nested_period_scope = "previous"
                visit(
                    nested,
                    ratio_hint=nested_is_ratio,
                    multiple_hint=nested_is_multiple,
                    currency_hint=nested_is_currency,
                    semantic_hint=nested_semantic or semantic_hint,
                    period_scope=nested_period_scope,
                    semantic_aliases=(
                        nested_aliases if nested_semantic else semantic_aliases
                    ),
                    peer_scope=nested_peer_scope,
                    peer_summary_scope=dict_summary_scope,
                )
        elif isinstance(value, (list, tuple)):
            for index, nested in enumerate(value, start=1):
                nested_aliases = semantic_aliases
                if semantic_hint == "projected_fcf":
                    projection_aliases = [
                        f"projection:{index}:projected_fcf",
                    ]
                    if index == 1:
                        projection_aliases.append("initial:projected_fcf")
                    if index == len(value):
                        projection_aliases.append("final:projected_fcf")
                    nested_aliases = tuple(
                        dict.fromkeys((*semantic_aliases, *projection_aliases))
                    )
                    append_semantic(
                        semantic_numeric_values,
                        "projection_year",
                        float(index),
                    )
                visit(
                    nested,
                    ratio_hint=ratio_hint,
                    multiple_hint=multiple_hint,
                    currency_hint=currency_hint,
                    semantic_hint=semantic_hint,
                    period_scope=period_scope,
                    semantic_aliases=nested_aliases,
                    peer_scope=peer_scope,
                    peer_summary_scope=peer_summary_scope,
                )

    # The stable ID is intentionally excluded: citing E24 must not make 24 a
    # supported analytical value. Labels and source dates support exact textual
    # and date claims only; their numeric fragments are not evidence values.
    for key in ("label", "source_date"):
        value = item.get(key)
        if value is not None:
            strings.add(str(value))
    visit(
        item.get("value"),
        currency_hint=item.get("kind") == "price",
        semantic_hint="current_price" if item.get("kind") == "price" else None,
    )
    return _EvidenceNumericContext(
        numeric_values=numeric_values,
        ratio_values=ratio_values,
        multiple_values=multiple_values,
        currency_values=currency_values,
        string_values=string_values,
        percent_string_values=percent_string_values,
        strings=strings,
        semantic_numeric_values=semantic_numeric_values,
        semantic_ratio_values=semantic_ratio_values,
        semantic_multiple_values=semantic_multiple_values,
        semantic_currency_values=semantic_currency_values,
        semantic_percent_string_values=semantic_percent_string_values,
        semantic_string_values=semantic_string_values,
    )


def _claim_segments(content: str) -> list[str]:
    segments: list[str] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        line = re.sub(r"^(?:[-*+]\s+|\d+[.)]\s+)", "", line)
        segments.extend(
            part.strip()
            for part in re.split(r"(?<=[.!?])\s+", line)
            if part.strip()
        )
    return segments


CitationCluster = tuple[int, int, tuple[str, ...]]


def _citation_clusters(segment: str) -> list[CitationCluster]:
    clusters: list[CitationCluster] = []
    for match in CITATION_RE.finditer(segment):
        if clusters and CITATION_CLUSTER_JOIN_RE.fullmatch(
            segment[clusters[-1][1]:match.start()]
        ):
            start, _, evidence_ids = clusters[-1]
            clusters[-1] = (start, match.end(), (*evidence_ids, match.group(1)))
        else:
            clusters.append((match.start(), match.end(), (match.group(1),)))
    return clusters


def _nearest_citation_cluster(
    start: int,
    end: int,
    clusters: list[CitationCluster],
) -> CitationCluster:
    def rank(cluster: CitationCluster) -> tuple[int, int, int]:
        cluster_start, cluster_end, _ = cluster
        if end <= cluster_start:
            return cluster_start - end, 0, cluster_start
        if cluster_end <= start:
            return start - cluster_end, 1, cluster_start
        return 0, 0, cluster_start

    return min(clusters, key=rank)


def _adjacent_citation_assignments(
    segment: str,
    clusters: list[CitationCluster],
    claims: list[re.Match[str]],
) -> dict[tuple[int, int], set[str]]:
    """Bind ordered claims to ordered IDs instead of pooling adjacent citations."""
    assignments: dict[tuple[int, int], set[str]] = {}
    for cluster in clusters:
        evidence_ids = cluster[2]
        if len(evidence_ids) <= 1:
            continue
        local_claims = [
            claim
            for claim in claims
            if _nearest_citation_cluster(claim.start(), claim.end(), clusters) == cluster
        ]
        if not local_claims:
            continue

        clause_groups: list[list[re.Match[str]]] = [[local_claims[0]]]
        for claim in local_claims[1:]:
            previous = clause_groups[-1][-1]
            if CLAUSE_BREAK_RE.search(segment[previous.end():claim.start()]):
                clause_groups.append([claim])
            else:
                clause_groups[-1].append(claim)

        if len(clause_groups) == len(evidence_ids):
            for evidence_id, group in zip(evidence_ids, clause_groups):
                for claim in group:
                    assignments[(claim.start(), claim.end())] = {evidence_id}
        elif len(local_claims) == len(evidence_ids):
            for evidence_id, claim in zip(evidence_ids, local_claims):
                assignments[(claim.start(), claim.end())] = {evidence_id}
    return assignments


def _match_is_identity_mention(
    match: re.Match[str],
    claim: str,
    identity_strings: tuple[str, ...],
) -> bool:
    for identity in identity_strings:
        if not identity:
            continue
        for identity_match in re.finditer(re.escape(identity), claim, re.IGNORECASE):
            if not (
                identity_match.start() <= match.start()
                and match.end() <= identity_match.end()
            ):
                continue
            # A longer identity phrase (for example, "10x Genomics") is
            # unambiguously a name. A compact identity by itself ("3M") is
            # ignored only where it is grammatically used as the subject/name,
            # never in a value position such as "share count was 3M".
            if identity_match.group(0).casefold() != match.group(0).casefold():
                return True
            before = claim[:identity_match.start()]
            after = claim[identity_match.end():]
            if IDENTITY_VALUE_PREDICATE_RE.match(after):
                return False
            if not (
                IDENTITY_SUBJECT_FOLLOW_RE.match(after)
                or IDENTITY_SECURITY_FOLLOW_RE.match(after)
            ):
                return False
            if not before.strip() or IDENTITY_PREFIX_RE.search(before):
                return True
    return False


def _explicit_claim_sign(match: re.Match[str]) -> str:
    signs = [
        value
        for value in (
            match.group("sign_before_currency"),
            match.group("sign_after_currency"),
        )
        if value
    ]
    normalized = ["-" if value in {"−", "–", "—"} else value for value in signs]
    if len(set(normalized)) > 1:
        return "invalid"
    return normalized[0] if normalized else ""


def _claim_direction(match: re.Match[str], claim: str) -> str:
    explicit_sign = _explicit_claim_sign(match)
    before = claim[max(0, match.start() - 48):match.start()]
    after = claim[match.end():match.end() + 32]
    word_before = before.rstrip()
    if word_before.endswith("("):
        word_before = word_before[:-1].rstrip()
    semantic_directions: set[str] = set()
    if (
        before.endswith("(")
        and after.lstrip().startswith(")")
    ):
        semantic_directions.add("-")
    if NEGATIVE_DIRECTION_AFTER_RE.search(after):
        semantic_directions.add("-")
    if POSITIVE_DIRECTION_AFTER_RE.search(after):
        semantic_directions.add("+")
    if (
        NEGATED_NEGATIVE_BEFORE_RE.search(word_before)
        and not AFFIRMING_NEGATIVE_BEFORE_RE.search(word_before)
    ):
        semantic_directions.add("+")
    elif NEGATIVE_DIRECTION_BEFORE_RE.search(word_before):
        semantic_directions.add("-")
    if (
        NEGATED_POSITIVE_BEFORE_RE.search(word_before)
        and not AFFIRMING_POSITIVE_BEFORE_RE.search(word_before)
    ):
        semantic_directions.add("-")
    elif POSITIVE_DIRECTION_BEFORE_RE.search(word_before):
        semantic_directions.add("+")
    if len(semantic_directions) > 1:
        return "invalid"
    semantic_direction = next(iter(semantic_directions), "")
    if explicit_sign and semantic_direction and explicit_sign != semantic_direction:
        return "invalid"
    return explicit_sign or semantic_direction


def _rounded_match(candidate: float, supported: float, decimal_places: int, direction: str) -> bool:
    if direction == "+" and supported < 0:
        return False
    if direction == "-" and supported > 0:
        return False
    candidate_value = abs(candidate) if direction else candidate
    supported_value = abs(supported) if direction else supported
    tolerance = (0.5 * (10 ** -decimal_places)) + 1e-9
    return abs(candidate_value - supported_value) <= tolerance


def _numeric_phrase_has_percentile_context(
    match: re.Match[str],
    claim: str,
    region_start: int,
    region_end: int,
) -> bool:
    before = claim[region_start:match.start()]
    after = claim[match.end():region_end]
    return bool(
        match.group("ordinal")
        or PERCENTILE_BEFORE_RE.search(before)
        or PERCENTILE_AFTER_RE.search(after)
    )


def _numeric_phrase_has_coverage_context(
    match: re.Match[str],
    claim: str,
    region_start: int,
    region_end: int,
) -> bool:
    before = claim[region_start:match.start()]
    after = claim[match.end():region_end]
    coverage_before = re.search(
        r"\b(?:coverage|observations?|valid\s+(?:peers?|companies|values))"
        r"(?:\s+(?:is|was|has|had|totals?|requires?|required))?\s*$",
        before,
        re.IGNORECASE,
    )
    return bool(
        coverage_before
        or COVERAGE_OBSERVATION_AFTER_RE.search(after)
    )


def _numeric_phrase_has_change_context(
    match: re.Match[str],
    claim: str,
    region_start: int,
    region_end: int,
) -> bool:
    before = claim[region_start:match.start()]
    if re.search(r"\b(?:to|from|at)\s*$", before, re.IGNORECASE):
        return False
    after = claim[match.end():region_end]
    if CHANGE_AMOUNT_BEFORE_RE.search(before):
        return True
    return bool(
        re.match(r"^\s*percentage\s+points?\b", after, re.IGNORECASE)
        and CHANGE_CONTEXT_RE.search(before)
    )


def _single_peer_scope(local_text: str, claim: str) -> Optional[str]:
    local_scopes = {
        match.group(1).lower()
        for match in PEER_SCOPE_RE.finditer(local_text)
    }
    if len(local_scopes) == 1:
        return next(iter(local_scopes))
    claim_scopes = {match.group(1).lower() for match in PEER_SCOPE_RE.finditer(claim)}
    if len(claim_scopes) == 1:
        return next(iter(claim_scopes))
    return None


def _single_percentile_type(local_text: str, claim: str) -> Optional[str]:
    local_types = {
        match.group(1).lower()
        for match in PERCENTILE_TYPE_RE.finditer(local_text)
    }
    if len(local_types) == 1:
        return next(iter(local_types))
    claim_types = {
        match.group(1).lower()
        for match in PERCENTILE_TYPE_RE.finditer(claim)
    }
    if len(claim_types) == 1:
        return next(iter(claim_types))
    return None


def _claim_semantic_key(match: re.Match[str], claim: str) -> Optional[str]:
    dates = list(ISO_DATE_RE.finditer(claim))
    other_numbers = [
        numeric_match
        for numeric_match in NUMERIC_CLAIM_RE.finditer(claim)
        if (numeric_match.start(), numeric_match.end())
        != (match.start(), match.end())
        and not any(
            numeric_match.start() < date_match.end()
            and date_match.start() < numeric_match.end()
            for date_match in dates
        )
    ]
    region_start = max(
        (numeric_match.end() for numeric_match in other_numbers if numeric_match.end() <= match.start()),
        default=0,
    )
    region_end = min(
        (numeric_match.start() for numeric_match in other_numbers if match.end() <= numeric_match.start()),
        default=len(claim),
    )
    candidates: list[tuple[int, int, int, str, int, int]] = []
    for semantic_key, pattern in SEMANTIC_CLAIM_PATTERNS.items():
        for semantic_match in pattern.finditer(claim):
            if not (
                region_start <= semantic_match.start()
                and semantic_match.end() <= region_end
            ):
                continue
            if semantic_match.end() <= match.start():
                distance = match.start() - semantic_match.end()
                side = 0
            elif match.end() <= semantic_match.start():
                distance = semantic_match.start() - match.end()
                side = 1
            else:
                distance = 0
                side = 0
            if distance <= 64:
                candidates.append((
                    distance,
                    side,
                    -len(semantic_match.group(0)),
                    semantic_key,
                    semantic_match.start(),
                    semantic_match.end(),
                ))
    if not candidates:
        fallback_candidates: list[tuple[int, int, int, str, int, int]] = []
        for semantic_key, pattern in SEMANTIC_CLAIM_PATTERNS.items():
            for semantic_match in pattern.finditer(claim):
                if semantic_match.end() <= match.start():
                    distance = match.start() - semantic_match.end()
                    side = 0
                elif match.end() <= semantic_match.start():
                    distance = semantic_match.start() - match.end()
                    side = 1
                else:
                    distance = 0
                    side = 0
                fallback_candidates.append((
                    distance,
                    side,
                    -len(semantic_match.group(0)),
                    semantic_key,
                    semantic_match.start(),
                    semantic_match.end(),
                ))
        fallback_candidates = [
            candidate
            for candidate in fallback_candidates
            if not any(
                other[4] <= candidate[4]
                and candidate[5] <= other[5]
                and (other[5] - other[4]) > (candidate[5] - candidate[4])
                for other in fallback_candidates
            )
        ]
        if len({candidate[3] for candidate in fallback_candidates}) != 1:
            return None
        candidates = fallback_candidates
    candidates = [
        candidate
        for candidate in candidates
        if not any(
            other[4] <= candidate[4]
            and candidate[5] <= other[5]
            and (other[5] - other[4]) > (candidate[5] - candidate[4])
            for other in candidates
        )
    ]

    for side in (0, 1):
        same_side = [candidate for candidate in candidates if candidate[1] == side]
        semantic_keys = {candidate[3] for candidate in same_side}
        if len(semantic_keys) <= 1:
            continue
        start = min(candidate[4] for candidate in same_side)
        end = max(candidate[5] for candidate in same_side)
        if SEMANTIC_COORDINATION_RE.search(claim[start:end]):
            return AMBIGUOUS_SEMANTIC_KEY

    _, _, _, semantic_key, _, _ = min(candidates)
    local_text = claim[region_start:region_end]
    before = claim[region_start:match.start()]
    if semantic_key == "projected_fcf":
        if re.search(r"\byear\s*$", before, re.IGNORECASE):
            return "projection_year"
        projection_years = list(PROJECTION_YEAR_RE.finditer(claim[:match.start()]))
        projection_year = projection_years[-1] if projection_years else None
        if projection_year and match.start() - projection_year.end() <= 64:
            return f"projection:{int(projection_year.group(1))}:projected_fcf"
        if PROJECTION_INITIAL_RE.search(local_text):
            return "initial:projected_fcf"
        if PROJECTION_FINAL_RE.search(local_text):
            return "final:projected_fcf"
    if _numeric_phrase_has_percentile_context(
        match,
        claim,
        region_start,
        region_end,
    ):
        percentile_type = _single_percentile_type(local_text, claim) or "summary"
        peer_scope = _single_peer_scope(local_text, claim)
        if percentile_type == "summary" and not peer_scope:
            return f"percentile:summary:{semantic_key}"
        if not peer_scope:
            return f"percentile:{percentile_type}:{semantic_key}"
        return f"percentile:{peer_scope}:{percentile_type}:{semantic_key}"
    if _numeric_phrase_has_coverage_context(
        match,
        claim,
        region_start,
        region_end,
    ):
        peer_scope = _single_peer_scope(local_text, claim) or "summary"
        coverage_role = (
            "minimum"
            if COVERAGE_MINIMUM_RE.search(local_text)
            else "observation"
        )
        return f"coverage:{peer_scope}:{coverage_role}:{semantic_key}"
    if (
        _numeric_phrase_has_change_context(
            match,
            claim,
            region_start,
            region_end,
        )
        and "growth" not in semantic_key
    ):
        return f"change:{semantic_key}"
    if semantic_key in PERIOD_SCOPED_SEMANTICS:
        if re.search(r"\bto\s*$", before, re.IGNORECASE):
            return f"current:{semantic_key}"
        if re.search(r"\bfrom\s*$", before, re.IGNORECASE):
            return f"previous:{semantic_key}"
        current_scope = bool(CURRENT_PERIOD_RE.search(local_text))
        previous_scope = bool(PREVIOUS_PERIOD_RE.search(local_text))
        if current_scope and previous_scope:
            return AMBIGUOUS_SEMANTIC_KEY
        if current_scope:
            return f"current:{semantic_key}"
        if previous_scope:
            return f"previous:{semantic_key}"
    return semantic_key


def _numeric_claim_supported(
    match: re.Match[str],
    context: _EvidenceNumericContext,
    claim: str,
) -> bool:
    raw_number = match.group("number").replace(",", "")
    sign = _explicit_claim_sign(match)
    if sign == "invalid":
        return False
    candidate = float(f"{sign}{raw_number}")
    direction = _claim_direction(match, claim)
    if direction == "invalid":
        return False
    mantissa, _, exponent_text = raw_number.lower().partition("e")
    exponent = int(exponent_text) if exponent_text else 0
    decimal_places = max(0, len(mantissa.partition(".")[2]) - exponent)
    scale = (match.group("scale") or "").lower()
    compact_scale = (match.group("compact_scale") or "").lower()
    basis_points = bool(match.group("basis_points"))
    semantic_key = _claim_semantic_key(match, claim)
    if semantic_key == AMBIGUOUS_SEMANTIC_KEY:
        return False

    def semantic_or_default(
        semantic_values: dict[str, list[float]],
        default_values: list[float],
    ) -> list[float]:
        if semantic_key is None:
            return default_values
        return semantic_values.get(semantic_key, [])

    unit_count = sum(bool(value) for value in (
        match.group("percent"),
        scale,
        compact_scale,
        basis_points,
        match.group("multiple"),
    ))
    if unit_count > 1:
        return False
    if match.group("currency") and (
        match.group("percent")
        or basis_points
        or match.group("multiple")
    ):
        return False

    if match.group("percent"):
        supported_values = [
            *[
                value * 100.0
                for value in semantic_or_default(
                    context.semantic_ratio_values,
                    context.ratio_values,
                )
            ],
            *(
                context.semantic_percent_string_values.get(semantic_key, [])
                if semantic_key
                else context.percent_string_values
            ),
        ]
    elif basis_points:
        supported_values = [
            value * 10_000.0
            for value in semantic_or_default(
                context.semantic_ratio_values,
                context.ratio_values,
            )
        ]
    elif scale:
        divisor = SCALE_DIVISORS[scale]
        source_values = (
            semantic_or_default(
                context.semantic_currency_values,
                context.currency_values,
            )
            if match.group("currency")
            else semantic_or_default(
                context.semantic_numeric_values,
                context.numeric_values,
            )
        )
        supported_values = [value / divisor for value in source_values]
    elif compact_scale:
        divisor = COMPACT_SCALE_DIVISORS[compact_scale]
        source_values = (
            semantic_or_default(
                context.semantic_currency_values,
                context.currency_values,
            )
            if match.group("currency")
            else semantic_or_default(
                context.semantic_numeric_values,
                context.numeric_values,
            )
        )
        supported_values = [value / divisor for value in source_values]
    elif match.group("multiple"):
        supported_values = semantic_or_default(
            context.semantic_multiple_values,
            context.multiple_values,
        )
    elif match.group("currency"):
        supported_values = semantic_or_default(
            context.semantic_currency_values,
            context.currency_values,
        )
    else:
        supported_values = [
            *semantic_or_default(
                context.semantic_numeric_values,
                context.numeric_values,
            ),
            *(
                context.semantic_string_values.get(semantic_key, [])
                if semantic_key
                else context.string_values
            ),
        ]

    return any(
        _rounded_match(candidate, supported, decimal_places, direction)
        for supported in supported_values
    )


def validate_evidence_numbers(
    content: str,
    evidence: list[dict],
    *,
    identity_strings: tuple[str, ...] = (),
) -> None:
    """Reject numeric prose that is not explicitly supported by its citations.

    Formatting a ratio as a percentage and scaling large values to thousands,
    millions, billions, or trillions are allowed. New arithmetic is not: this
    keeps a model from silently changing an evidence value while paraphrasing.
    """
    contexts = {
        str(item.get("id")): _evidence_numeric_context(item)
        for item in evidence
        if item.get("id")
    }

    for segment in _claim_segments(content):
        citation_ids = set(CITATION_RE.findall(segment))
        citation_clusters = _citation_clusters(segment)
        dates = list(ISO_DATE_RE.finditer(segment))
        numeric_claims = [
            numeric_match
            for numeric_match in NUMERIC_CLAIM_RE.finditer(segment)
            if not any(
                numeric_match.start() < date_match.end()
                and date_match.start() < numeric_match.end()
                for date_match in dates
            )
            and not _match_is_identity_mention(
                numeric_match,
                segment,
                identity_strings,
            )
        ]
        if not dates and not numeric_claims:
            continue
        if not citation_ids:
            raise EvidenceCitationError(
                "Every sentence containing a numeric value must cite supporting evidence."
            )
        citation_assignments = _adjacent_citation_assignments(
            segment,
            citation_clusters,
            numeric_claims,
        )

        def local_context(
            claim_start: int,
            claim_end: int,
        ) -> tuple[set[str], _EvidenceNumericContext]:
            nearest_cluster = _nearest_citation_cluster(
                claim_start,
                claim_end,
                citation_clusters,
            )
            local_ids = citation_assignments.get((claim_start, claim_end))
            if local_ids is None:
                if len(nearest_cluster[2]) > 1:
                    raise EvidenceCitationError(
                        "Adjacent citations are ambiguous for a numeric claim; "
                        "place each evidence ID immediately after the claim it supports."
                    )
                local_ids = set(nearest_cluster[2])
            unknown_ids = local_ids - contexts.keys()
            if unknown_ids:
                raise EvidenceCitationError(
                    f"Unknown evidence citations: {', '.join(sorted(unknown_ids))}."
                )
            local_contexts = [contexts[evidence_id] for evidence_id in local_ids]

            def merge_semantic_values(
                attribute: str,
            ) -> dict[str, list[float]]:
                merged: dict[str, list[float]] = {}
                for context in local_contexts:
                    for semantic_key, values in getattr(context, attribute).items():
                        merged.setdefault(semantic_key, []).extend(values)
                return merged

            return (
                local_ids,
                _EvidenceNumericContext(
                    numeric_values=[
                        number
                        for context in local_contexts
                        for number in context.numeric_values
                    ],
                    ratio_values=[
                        number
                        for context in local_contexts
                        for number in context.ratio_values
                    ],
                    multiple_values=[
                        number
                        for context in local_contexts
                        for number in context.multiple_values
                    ],
                    currency_values=[
                        number
                        for context in local_contexts
                        for number in context.currency_values
                    ],
                    string_values=[
                        number
                        for context in local_contexts
                        for number in context.string_values
                    ],
                    percent_string_values=[
                        number
                        for context in local_contexts
                        for number in context.percent_string_values
                    ],
                    strings={
                        value
                        for context in local_contexts
                        for value in context.strings
                    },
                    semantic_numeric_values=merge_semantic_values(
                        "semantic_numeric_values"
                    ),
                    semantic_ratio_values=merge_semantic_values(
                        "semantic_ratio_values"
                    ),
                    semantic_multiple_values=merge_semantic_values(
                        "semantic_multiple_values"
                    ),
                    semantic_currency_values=merge_semantic_values(
                        "semantic_currency_values"
                    ),
                    semantic_percent_string_values=merge_semantic_values(
                        "semantic_percent_string_values"
                    ),
                    semantic_string_values=merge_semantic_values(
                        "semantic_string_values"
                    ),
                ),
            )

        for date_match in dates:
            leading_sentence_date = (
                numeric_claims
                and date_match.end() <= numeric_claims[0].start()
            )
            trailing_sentence_date = (
                numeric_claims
                and date_match.start() >= numeric_claims[-1].end()
                and re.search(
                    r"\bas\s+of\s*$",
                    segment[:date_match.start()],
                    re.IGNORECASE,
                )
            )
            if leading_sentence_date or trailing_sentence_date:
                local_ids = set()
                for numeric_claim in numeric_claims:
                    local_ids.update(
                        citation_assignments.get(
                            (numeric_claim.start(), numeric_claim.end()),
                            set(_nearest_citation_cluster(
                                numeric_claim.start(),
                                numeric_claim.end(),
                                citation_clusters,
                            )[2]),
                        )
                    )
            else:
                local_ids = set(_nearest_citation_cluster(
                    date_match.start(),
                    date_match.end(),
                    citation_clusters,
                )[2])
            unknown_ids = local_ids - contexts.keys()
            if unknown_ids:
                raise EvidenceCitationError(
                    f"Unknown evidence citations: {', '.join(sorted(unknown_ids))}."
                )
            date_value = date_match.group(0)
            unsupported_ids = [
                evidence_id
                for evidence_id in sorted(local_ids)
                if not any(
                    date_value in value
                    for value in contexts[evidence_id].strings
                )
            ]
            if unsupported_ids:
                raise EvidenceCitationError(
                    f"Unsupported date claim {date_value!r} for citations "
                    f"{', '.join(unsupported_ids)}."
                )
        for numeric_claim in numeric_claims:
            local_ids, context = local_context(
                numeric_claim.start(),
                numeric_claim.end(),
            )
            if not _numeric_claim_supported(
                numeric_claim,
                context,
                segment,
            ):
                raise EvidenceCitationError(
                    f"Unsupported numeric claim {numeric_claim.group(0).strip()!r} "
                    f"for citations {', '.join(sorted(local_ids))}."
                )


def _qualitative_semantic_key(
    claim: str,
    start: int,
    end: int,
) -> Optional[str]:
    candidates: list[tuple[int, int, str, int, int]] = []
    for semantic_key, pattern in SEMANTIC_CLAIM_PATTERNS.items():
        for semantic_match in pattern.finditer(claim):
            if semantic_match.end() <= start:
                distance = start - semantic_match.end()
                side = 0
            elif end <= semantic_match.start():
                distance = semantic_match.start() - end
                side = 1
            else:
                distance = 0
                side = 0
            if distance <= 96:
                candidates.append((
                    distance,
                    side,
                    semantic_key,
                    semantic_match.start(),
                    semantic_match.end(),
                ))
    candidates = [
        candidate
        for candidate in candidates
        if not any(
            other[3] <= candidate[3]
            and candidate[4] <= other[4]
            and (other[4] - other[3]) > (candidate[4] - candidate[3])
            for other in candidates
        )
    ]
    if not candidates:
        return None
    minimum_distance = min(candidate[0] for candidate in candidates)
    nearest = [
        candidate
        for candidate in candidates
        if candidate[0] == minimum_distance
    ]
    if len({candidate[2] for candidate in nearest}) != 1:
        return None
    return min(nearest)[2]


def _qualitative_match_is_negated(claim: str, start: int) -> bool:
    return bool(
        QUALITATIVE_NEGATION_BEFORE_RE.search(
            claim[max(0, start - 64):start]
        )
    )


def _qualitative_period_scope(
    claim: str,
    start: int,
    end: int,
) -> Optional[str]:
    candidates: list[tuple[int, str]] = []
    for scope, pattern in (
        ("current", CURRENT_PERIOD_RE),
        ("previous", PREVIOUS_PERIOD_RE),
    ):
        for period_match in pattern.finditer(claim):
            if period_match.end() <= start:
                distance = start - period_match.end()
            elif end <= period_match.start():
                distance = period_match.start() - end
            else:
                distance = 0
            if distance <= 96:
                candidates.append((distance, scope))
    if not candidates:
        return None
    minimum_distance = min(distance for distance, _scope in candidates)
    nearest_scopes = {
        scope for distance, scope in candidates if distance == minimum_distance
    }
    if len(nearest_scopes) != 1:
        return AMBIGUOUS_SEMANTIC_KEY
    return next(iter(nearest_scopes))


def _evidence_semantic_metric(item: dict) -> Optional[str]:
    value = item.get("value")
    if not isinstance(value, dict):
        return None
    for key in ("metric", "evidence_metric", "metric_key"):
        semantic_key = SEMANTIC_VALUE_KEYS.get(str(value.get(key)))
        if semantic_key:
            return semantic_key
    return None


def _evidence_evaluative_strings(item: dict) -> dict[str, set[str]]:
    semantic_key = _evidence_semantic_metric(item)
    if semantic_key is None:
        return {}

    strings: set[str] = set()

    def collect(value: object) -> None:
        if isinstance(value, str):
            strings.add(value)
        elif isinstance(value, dict):
            for nested in value.values():
                collect(nested)
        elif isinstance(value, (list, tuple)):
            for nested in value:
                collect(nested)

    collect(item.get("value"))
    if item.get("label") is not None:
        strings.add(str(item["label"]))
    return {semantic_key: strings}


def _warning_trigger_is_negated(claim: str, start: int) -> bool:
    before = claim[max(0, start - 128):start]
    return bool(
        QUALITATIVE_NEGATION_BEFORE_RE.search(before)
        or WARNING_TRIGGER_NEGATION_BEFORE_RE.search(before)
    )


def validate_evidence_qualitative_claims(
    content: str,
    evidence: list[dict],
) -> None:
    """Validate directional prose that can contradict evidence without digits."""
    evidence_by_id = {
        str(item.get("id")): item
        for item in evidence
        if item.get("id")
    }
    contexts = {
        str(item.get("id")): _evidence_numeric_context(item)
        for item in evidence
        if item.get("id")
    }

    for segment in _claim_segments(content):
        clusters = _citation_clusters(segment)
        if not clusters:
            continue

        def cited_contexts(start: int, end: int) -> list[_EvidenceNumericContext]:
            evidence_ids = _nearest_citation_cluster(start, end, clusters)[2]
            return [
                contexts[evidence_id]
                for evidence_id in evidence_ids
                if evidence_id in contexts
            ]

        def cited_ids(start: int, end: int) -> tuple[str, ...]:
            return _nearest_citation_cluster(start, end, clusters)[2]

        if WARNING_LABEL_RE.search(segment):
            for trigger_match in WARNING_TRIGGER_RE.finditer(segment):
                local_ids = cited_ids(
                    trigger_match.start(),
                    trigger_match.end(),
                )
                expected_triggered = not _warning_trigger_is_negated(
                    segment,
                    trigger_match.start(),
                )
                semantic_key = _qualitative_semantic_key(
                    segment,
                    trigger_match.start(),
                    trigger_match.end(),
                )
                trigger_states: list[bool] = []
                for evidence_id in local_ids:
                    item = evidence_by_id.get(evidence_id)
                    value = item.get("value") if item else None
                    state = (
                        value.get("triggered")
                        if isinstance(value, dict)
                        else None
                    )
                    if not isinstance(state, bool):
                        raise EvidenceCitationError(
                            f"Unsupported warning trigger claim for {evidence_id}."
                        )
                    evidence_metric = _evidence_semantic_metric(item)
                    if semantic_key is not None and evidence_metric != semantic_key:
                        raise EvidenceCitationError(
                            f"Warning trigger claim for {semantic_key} is not "
                            f"supported by {evidence_id}."
                        )
                    trigger_states.append(state)
                if not trigger_states or any(
                    state is not expected_triggered
                    for state in trigger_states
                ):
                    raise EvidenceCitationError(
                        "Warning trigger polarity does not match cited evidence."
                    )

        for expected_direction, pattern in QUALITATIVE_TREND_PATTERNS:
            for direction_match in pattern.finditer(segment):
                if QUALITATIVE_RULE_LABEL_AFTER_RE.search(
                    segment[direction_match.end():]
                ):
                    continue
                if _qualitative_match_is_negated(
                    segment,
                    direction_match.start(),
                ):
                    raise EvidenceCitationError(
                        f"Negated qualitative construction around "
                        f"{direction_match.group(0)!r} is not accepted."
                    )
                semantic_key = _qualitative_semantic_key(
                    segment,
                    direction_match.start(),
                    direction_match.end(),
                )
                if semantic_key is None:
                    raise EvidenceCitationError(
                        f"Unsupported qualitative claim {direction_match.group(0)!r}; "
                        "no unambiguous evidence metric is identified."
                    )
                local_contexts = cited_contexts(
                    direction_match.start(),
                    direction_match.end(),
                )
                current_values = [
                    value
                    for context in local_contexts
                    for value in context.semantic_numeric_values.get(
                        f"current:{semantic_key}",
                        [],
                    )
                ]
                previous_values = [
                    value
                    for context in local_contexts
                    for value in context.semantic_numeric_values.get(
                        f"previous:{semantic_key}",
                        [],
                    )
                ]
                if expected_direction == "+":
                    supported = any(
                        current > previous
                        for current in current_values
                        for previous in previous_values
                    )
                elif expected_direction == "-":
                    supported = any(
                        current < previous
                        for current in current_values
                        for previous in previous_values
                    )
                else:
                    supported = any(
                        math.isclose(current, previous, rel_tol=1e-9, abs_tol=1e-12)
                        for current in current_values
                        for previous in previous_values
                    )
                if not supported:
                    raise EvidenceCitationError(
                        f"Unsupported qualitative direction "
                        f"{direction_match.group(0)!r} for {semantic_key}."
                    )

        for expected_sign, pattern in QUALITATIVE_SIGN_PATTERNS:
            for sign_match in pattern.finditer(segment):
                if _qualitative_match_is_negated(
                    segment,
                    sign_match.start(),
                ):
                    raise EvidenceCitationError(
                        f"Negated qualitative construction around "
                        f"{sign_match.group(0)!r} is not accepted."
                    )
                semantic_key = _qualitative_semantic_key(
                    segment,
                    sign_match.start(),
                    sign_match.end(),
                )
                if semantic_key is None:
                    raise EvidenceCitationError(
                        f"Unsupported qualitative sign {sign_match.group(0)!r}."
                    )
                local_contexts = cited_contexts(
                    sign_match.start(),
                    sign_match.end(),
                )
                period_scope = _qualitative_period_scope(
                    segment,
                    sign_match.start(),
                    sign_match.end(),
                )
                if period_scope == AMBIGUOUS_SEMANTIC_KEY:
                    raise EvidenceCitationError(
                        f"Ambiguous period for qualitative sign "
                        f"{sign_match.group(0)!r}."
                    )
                values = [
                    value
                    for context in local_contexts
                    for value in (
                        context.semantic_numeric_values.get(
                            f"{period_scope}:{semantic_key}",
                            [],
                        )
                        if period_scope
                        else (
                            context.semantic_numeric_values.get(
                                f"current:{semantic_key}",
                                [],
                            )
                            or context.semantic_numeric_values.get(
                                semantic_key,
                                [],
                            )
                        )
                    )
                ]
                supported = any(
                    value > 0 if expected_sign == "+" else value < 0
                    for value in values
                )
                if not supported:
                    raise EvidenceCitationError(
                        f"Unsupported qualitative sign {sign_match.group(0)!r} "
                        f"for {semantic_key}."
                    )

        for evaluation_match in EVALUATIVE_QUALITATIVE_RE.finditer(segment):
            if _qualitative_match_is_negated(
                segment,
                evaluation_match.start(),
            ):
                raise EvidenceCitationError(
                    f"Negated qualitative construction around "
                    f"{evaluation_match.group(0)!r} is not accepted."
                )
            word = evaluation_match.group(0).casefold()
            semantic_key = _qualitative_semantic_key(
                segment,
                evaluation_match.start(),
                evaluation_match.end(),
            )
            if semantic_key is None:
                raise EvidenceCitationError(
                    f"Unsupported evaluative claim {evaluation_match.group(0)!r}; "
                    "no unambiguous evidence metric is identified."
                )
            local_ids = cited_ids(
                evaluation_match.start(),
                evaluation_match.end(),
            )
            if not any(
                re.search(rf"\b{re.escape(word)}\b", value, re.IGNORECASE)
                for evidence_id in local_ids
                for value in _evidence_evaluative_strings(
                    evidence_by_id.get(evidence_id, {})
                ).get(semantic_key, set())
            ):
                raise EvidenceCitationError(
                    f"Unsupported evaluative claim {evaluation_match.group(0)!r} "
                    f"for {semantic_key}."
                )


def _evidence_prompt(ticker: str, decision_support: dict) -> str:
    metadata = decision_support.get("metadata") or {}
    evidence = decision_support.get("evidence") or []
    evidence_payload = json.dumps(
        evidence,
        sort_keys=True,
        ensure_ascii=False,
        default=str,
        indent=2,
    )
    return f"""
You are preparing an objective personal-investment research brief for
{metadata.get('company_name') or ticker} ({ticker}). You may use only the
decision-support evidence records below. Do not add facts, forecasts, news,
technical signals, scores, or recommendations that are not present in them.
All monetary evidence is denominated in {metadata.get('currency') or 'the reported currency'};
do not label it as another currency.

Evidence records (their stable IDs are the only permitted citations):
{evidence_payload}

Write concise English Markdown using exactly these four level-two headings:
## Core View
## Valuation
## Peer Context
## Risks

Every analytical sentence must contain at least one inline citation in the
exact form [E1], [E2], and so on. Cite only IDs in the evidence records.
Explicitly call out unavailable evidence and data-quality limits. Every
sentence containing a number must place the supporting evidence ID immediately
after that claim.
Do not pool adjacent evidence IDs for separate facts. You may round evidence
values, format ratios as percentages, and scale
currency values to thousands, millions, billions, or trillions. Do not perform
new arithmetic or introduce a derived number; reuse the supplied values such as
upside_downside directly. Directional words such as upside/downside and
above/below must agree with the sign of the cited evidence. Trend words such as
growing, declining, improving, or weakening must agree with cited current and
previous values. Sign words must respect any current or prior-period wording.
Use triggered or not triggered only when that exact warning state is present in
the cited record. Avoid subjective adjectives unless the cited evidence applies
that exact assessment to the same metric, and avoid other negated qualitative
constructions. Keep the brief
below 650 words, avoid an aggregate
score or confidence number, and avoid direct buy/sell advice.
""".strip()


def _repair_prompt(base_prompt: str, rejected_content: str, error: Exception) -> str:
    return f"""
{base_prompt}

The previous draft below was rejected by deterministic evidence validation:
{error}

Rewrite the entire brief. Preserve the four required headings. Correct the
reported validation issue, keep every numeric sentence tied to its supporting
evidence in that same sentence, and do not copy an unsupported number from the
rejected draft.

Rejected draft:
---
{rejected_content}
---
""".strip()


def _report_cache_identity(ticker: str, decision_support: dict) -> tuple[str, str, str]:
    model = settings.DEEPSEEK_MODEL
    evidence_hash = build_evidence_hash(decision_support, model)
    canonical_ticker = (decision_support.get("metadata") or {}).get("ticker") or ticker
    return canonical_ticker, model, evidence_hash


@asynccontextmanager
async def _singleflight_report_generation(
    identity: tuple[str, str, str],
) -> AsyncIterator[None]:
    """Serialize identical cache misses within this application process."""
    with _REPORT_GENERATION_LOCKS_GUARD:
        generation_lock = _REPORT_GENERATION_LOCKS.setdefault(
            identity,
            asyncio.Lock(),
        )
        _REPORT_GENERATION_LOCK_USERS[identity] = (
            _REPORT_GENERATION_LOCK_USERS.get(identity, 0) + 1
        )
    acquired = False
    try:
        await generation_lock.acquire()
        acquired = True
        yield
    finally:
        if acquired:
            generation_lock.release()
        with _REPORT_GENERATION_LOCKS_GUARD:
            remaining_users = _REPORT_GENERATION_LOCK_USERS[identity] - 1
            if remaining_users == 0:
                _REPORT_GENERATION_LOCK_USERS.pop(identity, None)
                _REPORT_GENERATION_LOCKS.pop(identity, None)
            else:
                _REPORT_GENERATION_LOCK_USERS[identity] = remaining_users


async def get_cached_stock_report(
    ticker: str,
    decision_support: dict,
    db: AsyncSession,
) -> Optional[str]:
    """Return a validated immutable cache hit without invoking the provider."""
    canonical_ticker, model, evidence_hash = _report_cache_identity(
        ticker,
        decision_support,
    )
    cache_result = await db.execute(
        select(DecisionBriefCache).where(
            DecisionBriefCache.ticker == canonical_ticker,
            DecisionBriefCache.model == model,
            DecisionBriefCache.evidence_hash == evidence_hash,
        )
    )
    cached = cache_result.scalar_one_or_none()
    return cached.content if cached is not None else None


async def generate_stock_report(
    ticker: str,
    decision_support: dict,
    db: AsyncSession,
) -> AsyncIterator[str]:
    """Generate, validate, and cache an evidence-bound decision brief."""
    api_key = settings.DEEPSEEK_API_KEY
    if not api_key:
        logger.error("DEEPSEEK_API_KEY environment variable is missing.")
        yield "Error: The evidence brief is not configured. Deterministic cockpit data remains available."
        return

    canonical_ticker, model, evidence_hash = _report_cache_identity(
        ticker,
        decision_support,
    )
    cached = await get_cached_stock_report(ticker, decision_support, db)
    if cached is not None:
        yield cached
        return

    content = ""
    async with _singleflight_report_generation(
        (canonical_ticker, model, evidence_hash)
    ):
        # A request that waited for the same identity must reuse the winner
        # instead of spending another provider call.
        cached = await get_cached_stock_report(ticker, decision_support, db)
        if cached is not None:
            content = cached
        else:
            try:
                base_prompt = _evidence_prompt(canonical_ticker, decision_support)
                evidence_ids = {
                    str(item["id"])
                    for item in decision_support.get("evidence", [])
                    if item.get("id")
                }
                metadata = decision_support.get("metadata") or {}
                identity_strings = _company_identity_strings(
                    str(metadata.get("company_name") or ""),
                    canonical_ticker,
                )
                prompt = base_prompt
                for attempt in range(MAX_REPORT_GENERATION_ATTEMPTS):
                    content = await generate_deepseek_text(prompt)
                    try:
                        validate_evidence_citations(content, evidence_ids)
                        validate_evidence_numbers(
                            content,
                            decision_support.get("evidence", []),
                            identity_strings=identity_strings,
                        )
                        validate_evidence_qualitative_claims(
                            content,
                            decision_support.get("evidence", []),
                        )
                        break
                    except EvidenceCitationError as exc:
                        if attempt + 1 >= MAX_REPORT_GENERATION_ATTEMPTS:
                            raise
                        logger.info(
                            "Retrying evidence brief for %s after validation "
                            "failure: %s",
                            canonical_ticker,
                            exc,
                        )
                        prompt = _repair_prompt(base_prompt, content, exc)
                db.add(
                    DecisionBriefCache(
                        ticker=canonical_ticker,
                        evidence_hash=evidence_hash,
                        model=model,
                        content=content,
                        evidence_ids=sorted(evidence_ids),
                    )
                )
                try:
                    await db.commit()
                except IntegrityError:
                    # Another process may still win the database uniqueness
                    # race even though requests in this process are serialized.
                    await db.rollback()
                    winning_content = await get_cached_stock_report(
                        ticker,
                        decision_support,
                        db,
                    )
                    if winning_content is not None:
                        content = winning_content
            except EvidenceCitationError as exc:
                logger.warning(
                    "Rejected invalid evidence brief for %s: %s",
                    canonical_ticker,
                    exc,
                )
                content = (
                    "Error: The generated brief failed evidence validation. "
                    "Deterministic cockpit data remains available."
                )
            except Exception:
                logger.exception(
                    "Error generating evidence brief for %s",
                    canonical_ticker,
                )
                content = (
                    "Error: The evidence brief is temporarily unavailable. "
                    "Deterministic cockpit data remains available."
                )
    yield content


async def generate_anomaly_attribution(
    ticker: str,
    price_change: float,
    news_list: list,
) -> str:
    """
    Generates a concise attribution report for a stock price anomaly.
    Initializes the client PER REQUEST to ensure concurrency safety.
    """
    api_key = settings.DEEPSEEK_API_KEY
    if not api_key:
        logger.error("DEEPSEEK_API_KEY environment variable is missing.")
        raise AttributionGenerationError("Attribution service is not configured")

    try:
        numbered_news = "\n\n".join(
            f"[{index}] {summary}"
            for index, summary in enumerate(news_list, start=1)
        )
        prompt = f"""
        你是一个华尔街量化分析师。今日 {ticker} 股价异动，涨跌幅为 {price_change}%。
        以下是过去 24 小时的相关新闻摘要：
        {numbered_news}
        
        请严格根据这些新闻，分析导致该股票异动的最核心原因。
        如果新闻中没有明确原因，请回复‘缺乏明确新闻催化剂，可能为资金面或技术面行为’。
        输出要求专业、简洁，不超过 150 字。引用新闻事实时用 [1]、[2] 这样的编号标明来源，
        不得补充输入中不存在的数字、评级或事件。
        """

        response = await generate_deepseek_text(prompt)
        return response or "无法生成归因分析"
        
    except Exception as exc:
        logger.exception("Error generating anomaly attribution for %s", ticker)
        raise AttributionGenerationError(
            "Attribution service is temporarily unavailable"
        ) from exc
