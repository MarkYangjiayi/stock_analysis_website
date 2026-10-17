from datetime import date

import pytest
from sqlalchemy import func, select

from models import DailyPrice, DataPublication, EtfHoldingSnapshot, PipelineRun, SecurityMaster, Ticker
from services.market_breadth import market_sessions_through
from services.themes import etf_prices
from services.themes.membership import FUND_METADATA_HOLDING_KEY
from services.themes.taxonomy import parse_taxonomy


TARGET = date(2026, 10, 9)  # Friday session

TAXONOMY = """
version: test-v1
themes:
  - slug: ai-compute
    name_en: AI Compute
    name_zh: AI 算力
    definition: Accelerators.
    proxy_etfs: [SMH, SOXX]
  - slug: memory-storage
    name_en: Memory
    name_zh: 存储
    definition: Memory makers.
    proxy_etfs: [DRAM, SMH, NEWX, SPY]
  - slug: old-theme
    name_en: Old
    name_zh: 旧
    definition: Retired.
    proxy_etfs: [OLDX]
    status: retired
"""


@pytest.fixture
def taxonomy(monkeypatch):
    monkeypatch.setattr(etf_prices, "load_taxonomy", lambda: parse_taxonomy(TAXONOMY))


async def _seed_inception(db_session, values):
    db_session.add_all(
        EtfHoldingSnapshot(etf=etf, as_of_date=date(2026, 10, 2), source="eodhd", holding_key="X.US",
                           holding_ticker="X.US", weight_pct=1.0, inception_date=inception)
        for etf, inception in values.items()
    )
    await db_session.commit()


def _capture_backfill(monkeypatch):
    captured = {}

    async def fake_backfill(tickers, **kwargs):
        captured["tickers"] = list(tickers)
        captured.update(kwargs)
        return {"run_id": 1, "status": "published", "ticker_coverage": 1.0}

    monkeypatch.setattr(etf_prices, "backfill_price_history", fake_backfill)
    return captured


async def test_required_sessions_start_after_each_known_inception(monkeypatch, db_session, taxonomy):
    dram_inception = date(2026, 4, 2)
    await _seed_inception(db_session, {"DRAM.US": dram_inception, "NEWX.US": date(2026, 11, 2), "SMH.US": None})
    captured = _capture_backfill(monkeypatch)

    result = await etf_prices.refresh_theme_etf_prices(TARGET)

    sessions = market_sessions_through(TARGET, 353)
    # Retired themes' ETFs are skipped, SPY is added once, an unlisted fund is not requested.
    assert captured["tickers"] == ["SMH.US", "SOXX.US", "DRAM.US", "SPY.US"]
    required = captured["required_sessions_by_ticker"]
    assert required["SMH.US"] == sessions
    assert required["SOXX.US"] == sessions
    assert required["SPY.US"] == sessions
    assert required["DRAM.US"] == [session for session in sessions if session > dram_inception]
    assert min(required["DRAM.US"]) > dram_inception
    assert {key: captured[key] for key in (
        "history_days", "target_date", "include_corporate_actions", "include_target_session",
        "publication_dataset", "publish_when_complete", "minimum_ticker_coverage",
    )} == {
        "history_days": 352,
        "target_date": TARGET,
        "include_corporate_actions": False,
        "include_target_session": True,
        "publication_dataset": "theme_etf_prices",
        "publish_when_complete": True,
        "minimum_ticker_coverage": 0.80,
    }
    assert result["status"] == "published"
    assert result["as_of_date"] == TARGET.isoformat()
    assert result["not_listed"] == ["NEWX.US"]


async def test_fund_without_listed_holdings_still_has_known_inception(monkeypatch, db_session, taxonomy):
    inception = date(2026, 4, 2)
    # The membership refresh keeps only a metadata row when the provider listed no holdings.
    db_session.add(EtfHoldingSnapshot(etf="DRAM.US", as_of_date=date(2026, 10, 2), source="eodhd",
                                      holding_key=FUND_METADATA_HOLDING_KEY, inception_date=inception))
    await db_session.commit()
    captured = _capture_backfill(monkeypatch)

    await etf_prices.refresh_theme_etf_prices(TARGET)

    sessions = market_sessions_through(TARGET, 353)
    assert captured["required_sessions_by_ticker"]["DRAM.US"] == [s for s in sessions if s > inception]


async def test_weekend_target_resolves_to_previous_session(monkeypatch, db_session, taxonomy):
    captured = _capture_backfill(monkeypatch)

    await etf_prices.refresh_theme_etf_prices(date(2026, 10, 11))

    assert captured["target_date"] == TARGET
    assert all(sessions[-1] == TARGET for sessions in captured["required_sessions_by_ticker"].values())


def test_inception_on_the_target_session_requires_only_that_session():
    sessions = market_sessions_through(TARGET, 5)

    required, not_listed = etf_prices.required_sessions_by_etf(
        ["NEW.US", "OLD.US", "UNKNOWN.US"],
        sessions,
        {"NEW.US": TARGET, "OLD.US": date(2000, 1, 1)},
    )

    assert required == {"NEW.US": [TARGET], "OLD.US": sessions, "UNKNOWN.US": sessions}
    assert not_listed == []


async def test_real_backfill_accepts_the_required_window_and_skips_security_master(monkeypatch, db_session):
    monkeypatch.setattr(etf_prices, "load_taxonomy", lambda: parse_taxonomy("""
version: test-v1
themes:
  - slug: memory-storage
    name_en: Memory
    name_zh: 存储
    definition: Memory makers.
    proxy_etfs: [DRAM, SMH]
"""))
    inception = date(2026, 4, 2)
    await _seed_inception(db_session, {"DRAM.US": inception})
    requests = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    async def fake_eod(ticker, from_date=None, to_date=None, client=None):
        requests.append(ticker)
        first = inception if ticker == "DRAM.US" else date(2000, 1, 1)
        # The provider's first DRAM bar lags its listing date by a day.
        return [
            {"date": session.isoformat(), "open": 10, "high": 11, "low": 9, "close": 10,
             "adjusted_close": 10, "volume": 1000}
            for session in market_sessions_through(TARGET, 400)
            if session > first
        ]

    monkeypatch.setattr("services.history_backfill.eodhd_client.create_http_client", lambda: Client())
    monkeypatch.setattr("services.history_backfill.eodhd_client.get_eod_historical_data", fake_eod)

    result = await etf_prices.refresh_theme_etf_prices(TARGET)

    assert result["status"] == "published"
    assert result["failed"] == 0
    assert sorted(requests) == ["DRAM.US", "SMH.US", "SPY.US"]
    publication = (await db_session.execute(
        select(DataPublication).where(DataPublication.dataset == etf_prices.THEME_ETF_PRICE_DATASET)
    )).scalar_one()
    assert publication.as_of_date == TARGET
    run = await db_session.get(PipelineRun, publication.pipeline_run_id)
    assert run.status == "published"
    assert await db_session.scalar(select(func.count()).select_from(SecurityMaster)) == 0
    assert set((await db_session.execute(select(Ticker.ticker))).scalars()) == {"DRAM.US", "SMH.US", "SPY.US"}
    latest = await db_session.scalar(select(func.max(DailyPrice.date)).where(DailyPrice.ticker == "DRAM.US"))
    assert latest == TARGET
