import hashlib
import json
import logging
import math
import re
from collections.abc import AsyncIterator
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


PROMPT_VERSION = "decision-evidence-v4"
REPORT_SECTIONS = ("Core View", "Valuation", "Peer Context", "Risks")
SECTION_HEADING_RE = re.compile(
    r"(?im)^(?:#{1,4}\s*|\*\*)?"
    r"(Core View|Valuation|Peer Context|Risks)"
    r"(?:\*\*)?\s*:?\s*$"
)
CITATION_RE = re.compile(r"\[(E\d+)\]")
ISO_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
NUMERIC_CLAIM_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?P<currency>[$€£])?\s*"
    r"(?P<sign>[+\-−–—])?"
    r"(?P<number>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"
    r"(?P<ordinal>st|nd|rd|th)?"
    r"(?P<percent>%)?"
    r"(?:\s*(?P<scale>thousand|million|billion|trillion))?"
    r"(?P<multiple>[x×])?"
    r"(?![A-Za-z0-9])",
    re.IGNORECASE,
)
STRING_NUMBER_RE = re.compile(r"(?<!\d)[+-]?\d+(?:\.\d+)?")
SCALE_DIVISORS = {
    "thousand": 1_000.0,
    "million": 1_000_000.0,
    "billion": 1_000_000_000.0,
    "trillion": 1_000_000_000_000.0,
}
NEGATIVE_DIRECTION_BEFORE_RE = re.compile(
    r"\b(?:downside|decline|decrease|drop|loss)(?:\s+(?:of|is|was|by))?\s*$"
    r"|\b(?:fell|declined|decreased|dropped|lost|down)(?:\s+by)?\s*$",
    re.IGNORECASE,
)
POSITIVE_DIRECTION_BEFORE_RE = re.compile(
    r"\b(?:upside|increase|gain|premium)(?:\s+(?:of|is|was|by))?\s*$"
    r"|\b(?:rose|increased|grew|gained|up)(?:\s+by)?\s*$",
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
MAX_REPORT_GENERATION_ATTEMPTS = 2


class EvidenceCitationError(ValueError):
    """Raised when a generated brief is not bound to supplied evidence."""


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


def _evidence_numeric_context(item: dict) -> tuple[list[float], list[float], set[str]]:
    """Collect explicit evidence numbers without treating an evidence ID as a fact."""
    numeric_values: list[float] = []
    string_values: list[float] = []
    strings: set[str] = set()

    def visit(value: object) -> None:
        if isinstance(value, bool):
            numeric_values.append(float(value))
        elif isinstance(value, (int, float)):
            number = float(value)
            if math.isfinite(number):
                numeric_values.append(number)
        elif isinstance(value, str):
            strings.add(value)
            for match in STRING_NUMBER_RE.finditer(value):
                try:
                    string_values.append(float(match.group(0)))
                except ValueError:
                    continue
        elif isinstance(value, dict):
            for nested in value.values():
                visit(nested)
        elif isinstance(value, (list, tuple)):
            numeric_values.append(float(len(value)))
            for nested in value:
                visit(nested)

    # The stable ID is intentionally excluded: citing E24 must not make 24 a
    # supported analytical value. Labels and source dates are included because
    # they carry facts such as "5Y" and the publication date.
    for key in ("label", "source_date", "value"):
        visit(item.get(key))
    return numeric_values, string_values, strings


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


def _claim_direction(match: re.Match[str], claim: str) -> str:
    explicit_sign = match.group("sign") or ""
    if explicit_sign in {"−", "–", "—"}:
        explicit_sign = "-"
    if explicit_sign:
        return explicit_sign

    before = claim[max(0, match.start() - 48):match.start()]
    after = claim[match.end():match.end() + 32]
    if NEGATIVE_DIRECTION_AFTER_RE.search(after):
        return "-"
    if POSITIVE_DIRECTION_AFTER_RE.search(after):
        return "+"
    if NEGATIVE_DIRECTION_BEFORE_RE.search(before):
        return "-"
    if POSITIVE_DIRECTION_BEFORE_RE.search(before):
        return "+"
    return ""


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
    numeric_values: list[float],
    string_values: list[float],
    claim: str,
) -> bool:
    raw_number = match.group("number").replace(",", "")
    raw_sign = match.group("sign") or ""
    sign = "-" if raw_sign in {"−", "–", "—"} else raw_sign
    candidate = float(f"{sign}{raw_number}")
    direction = _claim_direction(match, claim)
    decimal_places = len(raw_number.partition(".")[2])
    scale = (match.group("scale") or "").lower()

    if match.group("percent"):
        supported_values = [value * 100.0 for value in numeric_values]
    elif scale:
        divisor = SCALE_DIVISORS[scale]
        supported_values = [value / divisor for value in numeric_values]
    else:
        supported_values = [*numeric_values, *string_values]

    return any(
        _rounded_match(candidate, supported, decimal_places, direction)
        for supported in supported_values
    )


def validate_evidence_numbers(content: str, evidence: list[dict]) -> None:
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
        claim = CITATION_RE.sub("", segment)
        dates = ISO_DATE_RE.findall(claim)
        claim_without_dates = ISO_DATE_RE.sub("", claim)
        numeric_claims = list(NUMERIC_CLAIM_RE.finditer(claim_without_dates))
        if not dates and not numeric_claims:
            continue
        if not citation_ids:
            raise EvidenceCitationError(
                "Every sentence containing a numeric value must cite supporting evidence."
            )

        cited_contexts = [contexts[evidence_id] for evidence_id in citation_ids]
        supported_numbers = [
            number
            for numeric_values, _, _ in cited_contexts
            for number in numeric_values
        ]
        supported_string_numbers = [
            number
            for _, string_values, _ in cited_contexts
            for number in string_values
        ]
        supported_strings = {
            value
            for _, _, strings in cited_contexts
            for value in strings
        }

        for date_value in dates:
            if not any(date_value in value for value in supported_strings):
                raise EvidenceCitationError(
                    f"Unsupported date claim {date_value!r} for citations "
                    f"{', '.join(sorted(citation_ids))}."
                )
        for numeric_claim in numeric_claims:
            if not _numeric_claim_supported(
                numeric_claim,
                supported_numbers,
                supported_string_numbers,
                claim_without_dates,
            ):
                raise EvidenceCitationError(
                    f"Unsupported numeric claim {numeric_claim.group(0).strip()!r} "
                    f"for citations {', '.join(sorted(citation_ids))}."
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
number must end with the evidence ID or IDs that explicitly contain that
number. You may round evidence values, format ratios as percentages, and scale
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
        prompt = base_prompt
        content = ""
        for attempt in range(MAX_REPORT_GENERATION_ATTEMPTS):
            content = await generate_deepseek_text(prompt)
            try:
                validate_evidence_citations(content, evidence_ids)
                validate_evidence_numbers(content, decision_support.get("evidence", []))
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
