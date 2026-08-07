from dataclasses import asdict, dataclass, field
import math
from typing import Any, Dict, List

from core.config import settings
from services.screener_normalization import (
    HIGH_DISTANCE_FIELDS,
    LOW_DISTANCE_FIELDS,
    NONNEGATIVE_VALUE_FIELDS,
    POSITIVE_MULTIPLE_FIELDS,
    POSITIVE_VALUE_FIELDS,
    RETURN_FIELDS,
    is_non_primary_exchange,
    is_valid_public_screener_value,
)


@dataclass
class QualityReport:
    passed: bool
    metrics: Dict[str, Any]
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class DataQualityError(RuntimeError):
    def __init__(self, report: QualityReport):
        super().__init__("Data quality gate failed: " + "; ".join(report.errors))
        self.report = report


def validate_screener_records(records: List[dict]) -> QualityReport:
    count = len(records)
    unique_tickers = len({record.get("ticker") for record in records if record.get("ticker")})
    price_count = sum(record.get("close") is not None and record.get("close", 0) > 0 for record in records)
    market_cap_count = sum(record.get("market_cap") is not None for record in records)
    technical_count = sum(record.get("ma50") is not None for record in records)
    price_coverage = price_count / count if count else 0.0
    metrics = {
        "records": count,
        "unique_tickers": unique_tickers,
        "price_coverage": price_coverage,
        "market_cap_coverage": market_cap_count / count if count else 0.0,
        "ma50_coverage": technical_count / count if count else 0.0,
    }
    excluded = {
        "ticker",
        "date",
        "name",
        "sector",
        "industry",
        "exchange",
        "country",
        "ipo_date",
        "candlestick",
        "technical_quality",
    }
    numeric_fields = sorted(
        {
            key
            for record in records
            for key, value in record.items()
            if key not in excluded and (value is None or isinstance(value, (int, float)))
        }
    )
    field_coverage = {}
    invalid_numeric_values = {}
    for field_name in numeric_fields:
        populated = 0
        invalid = 0
        for record in records:
            value = record.get(field_name)
            if value is None:
                continue
            if isinstance(value, float) and not math.isfinite(value):
                invalid += 1
            else:
                populated += 1
        field_coverage[field_name] = populated / count if count else 0.0
        if invalid:
            invalid_numeric_values[field_name] = invalid
    metrics["field_coverage"] = field_coverage
    metrics["invalid_numeric_values"] = invalid_numeric_values
    constrained_fields = (
        POSITIVE_MULTIPLE_FIELDS
        | POSITIVE_VALUE_FIELDS
        | NONNEGATIVE_VALUE_FIELDS
        | RETURN_FIELDS
        | HIGH_DISTANCE_FIELDS
        | LOW_DISTANCE_FIELDS
        | {"analyst_recommendation", "rsi_14"}
    )
    invalid_business_values = {
        field_name: sum(
            not is_valid_public_screener_value(field_name, record.get(field_name))
            for record in records
            if record.get(field_name) is not None
        )
        for field_name in constrained_fields
    }
    invalid_business_values = {
        field_name: invalid
        for field_name, invalid in invalid_business_values.items()
        if invalid
    }
    non_primary_listings = sorted({
        str(record.get("ticker"))
        for record in records
        if record.get("ticker") and is_non_primary_exchange(record.get("exchange"))
    })
    technical_quarantines = {
        str(record.get("ticker")): str(record.get("technical_quality"))
        for record in records
        if record.get("ticker")
        and record.get("technical_quality") not in (None, "ok")
    }
    metrics["invalid_business_values"] = invalid_business_values
    metrics["non_primary_listings"] = non_primary_listings
    metrics["technical_quarantines"] = technical_quarantines
    errors: List[str] = []
    warnings: List[str] = []
    if count < settings.PIPELINE_MIN_UNIVERSE_SIZE:
        errors.append(f"universe too small: {count}")
    if unique_tickers != count:
        errors.append(f"duplicate tickers: {count - unique_tickers}")
    if price_coverage < settings.PIPELINE_MIN_PRICE_COVERAGE:
        errors.append(f"price coverage below threshold: {price_coverage:.2%}")
    if metrics["market_cap_coverage"] < settings.PIPELINE_MIN_FUNDAMENTAL_COVERAGE:
        errors.append(f"fundamental coverage below threshold: {metrics['market_cap_coverage']:.2%}")
    if metrics["ma50_coverage"] < 0.5:
        warnings.append("technical history is still warming up; MA50 coverage below 50%")
    sparse_fields = [
        field_name
        for field_name, coverage in field_coverage.items()
        if field_name not in {"market_cap", "close", "volume"} and coverage < 0.5
    ]
    if sparse_fields:
        warnings.append(
            "optional field coverage below 50%: " + ", ".join(sparse_fields)
        )
    if invalid_numeric_values:
        errors.append("non-finite numeric values present: " + ", ".join(invalid_numeric_values))
    if invalid_business_values:
        errors.append(
            "invalid screener values present: "
            + ", ".join(
                f"{field_name}={count}"
                for field_name, count in sorted(invalid_business_values.items())
            )
        )
    if non_primary_listings:
        errors.append(
            "non-primary listings present: " + ", ".join(non_primary_listings)
        )
    if technical_quarantines:
        warnings.append(
            f"technical metrics quarantined for {len(technical_quarantines)} symbol(s)"
        )
    return QualityReport(passed=not errors, metrics=metrics, errors=errors, warnings=warnings)
