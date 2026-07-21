from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

from core.config import settings


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
    return QualityReport(passed=not errors, metrics=metrics, errors=errors, warnings=warnings)
