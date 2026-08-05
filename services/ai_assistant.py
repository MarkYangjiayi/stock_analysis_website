import hashlib
import json
import logging
import math
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
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


PROMPT_VERSION = "decision-evidence-v9"
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
IDENTITY_VALUE_PREDICATE_RE = re.compile(
    r"^\s+(?:is|was|were|equals?|equaled|represented|reached|totaled)\s+"
    r"(?:the\s+)?(?:published\s+)?(?:current\s+)?"
    r"(?:assets|book|cash|debt|earnings|ebitda|enterprise\s+value|equity|"
    r"fcf|free\s+cash\s+flow|multiple|price|revenue|sales|shares?|turnover)\b",
    re.IGNORECASE,
)
IDENTITY_SUBJECT_FOLLOW_RE = re.compile(
    r"^\s*(?:['’]s\b|[,;:]|(?:shares?|stock)\b|"
    r"(?:is|was|has|had|does|did|reports?|reported|"
    r"shows?|showed|faces?|faced|remains?|remained|trades?|traded|covers?|"
    r"covered)\b)",
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
MAX_REPORT_GENERATION_ATTEMPTS = 2


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


def build_evidence_hash(decision_support: dict, model: str) -> str:
    metadata = decision_support.get("metadata") or {}
    payload = {
        "prompt_version": PROMPT_VERSION,
        "model": model,
        "prompt_identity": {
            "ticker": metadata.get("ticker"),
            "company_name": metadata.get("company_name"),
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


def _evidence_numeric_context(item: dict) -> _EvidenceNumericContext:
    """Collect explicit evidence numbers without treating an evidence ID as a fact."""
    numeric_values: list[float] = []
    ratio_values: list[float] = []
    multiple_values: list[float] = []
    currency_values: list[float] = []
    string_values: list[float] = []
    percent_string_values: list[float] = []
    strings: set[str] = set()

    def visit(
        value: object,
        *,
        ratio_hint: bool = False,
        multiple_hint: bool = False,
        currency_hint: bool = False,
    ) -> None:
        if isinstance(value, bool):
            return
        elif isinstance(value, (int, float)):
            number = float(value)
            if math.isfinite(number):
                numeric_values.append(number)
                if ratio_hint:
                    ratio_values.append(number)
                if multiple_hint:
                    multiple_values.append(number)
                if currency_hint:
                    currency_values.append(number)
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
        elif isinstance(value, dict):
            format_is_percent = value.get("format") == "percent"
            format_is_multiple = value.get("format") in {"multiple", "ratio"}
            warning_metric = value.get("metric")
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
                visit(
                    nested,
                    ratio_hint=nested_is_ratio,
                    multiple_hint=nested_is_multiple,
                    currency_hint=nested_is_currency,
                )
        elif isinstance(value, (list, tuple)):
            for nested in value:
                visit(
                    nested,
                    ratio_hint=ratio_hint,
                    multiple_hint=multiple_hint,
                    currency_hint=currency_hint,
                )

    # The stable ID is intentionally excluded: citing E24 must not make 24 a
    # supported analytical value. Labels and source dates support exact textual
    # and date claims only; their numeric fragments are not evidence values.
    for key in ("label", "source_date"):
        value = item.get(key)
        if value is not None:
            strings.add(str(value))
    visit(item.get("value"), currency_hint=item.get("kind") == "price")
    return _EvidenceNumericContext(
        numeric_values=numeric_values,
        ratio_values=ratio_values,
        multiple_values=multiple_values,
        currency_values=currency_values,
        string_values=string_values,
        percent_string_values=percent_string_values,
        strings=strings,
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
            if not IDENTITY_SUBJECT_FOLLOW_RE.match(after):
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
        match.group("currency")
        and before.endswith("(")
        and after.lstrip().startswith(")")
    ):
        semantic_directions.add("-")
    if NEGATIVE_DIRECTION_AFTER_RE.search(after):
        semantic_directions.add("-")
    if POSITIVE_DIRECTION_AFTER_RE.search(after):
        semantic_directions.add("+")
    if (
        NEGATIVE_DIRECTION_BEFORE_RE.search(word_before)
        and not NEGATED_NEGATIVE_BEFORE_RE.search(word_before)
    ):
        semantic_directions.add("-")
    if (
        POSITIVE_DIRECTION_BEFORE_RE.search(word_before)
        and not NEGATED_POSITIVE_BEFORE_RE.search(word_before)
    ):
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
            *[value * 100.0 for value in context.ratio_values],
            *context.percent_string_values,
        ]
    elif basis_points:
        supported_values = [value * 10_000.0 for value in context.ratio_values]
    elif scale:
        divisor = SCALE_DIVISORS[scale]
        source_values = (
            context.currency_values
            if match.group("currency")
            else context.numeric_values
        )
        supported_values = [value / divisor for value in source_values]
    elif compact_scale:
        divisor = COMPACT_SCALE_DIVISORS[compact_scale]
        source_values = (
            context.currency_values
            if match.group("currency")
            else context.numeric_values
        )
        supported_values = [value / divisor for value in source_values]
    elif match.group("multiple"):
        supported_values = context.multiple_values
    elif match.group("currency"):
        supported_values = context.currency_values
    else:
        supported_values = [*context.numeric_values, *context.string_values]

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
                ),
            )

        for date_match in dates:
            if numeric_claims and date_match.end() <= numeric_claims[0].start():
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

Evidence records (their stable IDs are the only permitted citations):
{evidence_payload}

Write concise English Markdown using exactly these four level-two headings:
## Core View
## Valuation
## Peer Context
## Risks

Every section must contain at least one inline citation in the exact form [E1],
[E2], and so on. Cite only IDs in the evidence records. Explicitly call out
unavailable evidence and data-quality limits. Every sentence containing a
number must place the supporting evidence ID immediately after that claim.
Do not pool adjacent evidence IDs for separate facts. You may round evidence
values, format ratios as percentages, and scale
currency values to thousands, millions, billions, or trillions. Do not perform
new arithmetic or introduce a derived number; reuse the supplied values such as
upside_downside directly. Directional words such as upside/downside and
above/below must agree with the sign of the cited evidence. Keep the brief
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
        content = ""
        for attempt in range(MAX_REPORT_GENERATION_ATTEMPTS):
            content = await generate_deepseek_text(prompt)
            try:
                validate_evidence_citations(content, evidence_ids)
                validate_evidence_numbers(
                    content,
                    decision_support.get("evidence", []),
                    identity_strings=identity_strings,
                )
                break
            except EvidenceCitationError as exc:
                if attempt + 1 >= MAX_REPORT_GENERATION_ATTEMPTS:
                    raise
                logger.info(
                    "Retrying evidence brief for %s after validation failure: %s",
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
            # A concurrent request may have populated the same immutable cache key.
            await db.rollback()
        yield content
    except EvidenceCitationError as exc:
        logger.warning("Rejected invalid evidence brief for %s: %s", canonical_ticker, exc)
        yield "Error: The generated brief failed evidence validation. Deterministic cockpit data remains available."
    except Exception:
        logger.exception("Error generating evidence brief for %s", canonical_ticker)
        yield "Error: The evidence brief is temporarily unavailable. Deterministic cockpit data remains available."


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
