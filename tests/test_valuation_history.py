from datetime import date, datetime
from types import SimpleNamespace

import httpx
import pytest

from api.schemas import ValuationHistoryResponse
from services.valuation_history import KEYS, build_valuation_history, get_valuation_history


def price(day, close=40, adjusted_close=3):
    return SimpleNamespace(date=date.fromisoformat(day), close=close, adjusted_close=adjusted_close)


def statements():
    result = []
    for end, filing in [("2023-12-31", "2024-02-01"), ("2024-03-31", "2024-05-01"), ("2024-06-30", "2024-08-01"), ("2024-09-30", "2024-11-01")]:
        result.append(SimpleNamespace(
            period_end=date.fromisoformat(end), filing_at=datetime.fromisoformat(filing),
            available_at=datetime.fromisoformat(filing), availability_estimated=False,
            fetched_at=datetime(2025, 2, 1), revision=1, source="EODHD", raw_snapshot_id=1,
            income_statement={"currency_symbol": "USD", "netIncome": 10, "totalRevenue": 100, "ebitda": 20},
            balance_sheet={"currency_symbol": "USD", "commonStockSharesOutstanding": 10, "totalStockholderEquity": 200, "totalDebt": 100, "cashAndShortTermInvestments": 20},
            cash_flow={"currency_symbol": "USD", "totalCashFromOperatingActivities": 30, "capitalExpenditures": -10, "freeCashFlow": 999},
        ))
    return result


def calculate(rows=None, prices=None, splits=(), **kwargs):
    return build_valuation_history("TEST.US", prices or [price("2024-11-04")], rows if rows is not None else statements(), splits, currency="USD", **kwargs)


def test_all_six_multiples_use_reported_inputs_and_raw_price():
    result = calculate()
    assert result["points"][0]["values"] == pytest.approx({"pe": 10, "ps": 1, "pb": 2, "pfcf": 5, "ev_revenue": 1.2, "ev_ebitda": 6})
    assert result["history_basis"] == "reconstructed_estimates"
    assert result["bases"][0]["inputs"]["fcf"] == 80  # CFO-capex, not provider FCF 999
    ValuationHistoryResponse.model_validate(result)


def test_filing_and_cash_flow_disclosure_dates_are_not_backdated():
    rows = statements()
    rows[-1].cash_flow["filing_date"] = "2024-11-05"
    result = calculate(rows, [price("2024-11-01"), price("2024-11-05"), price("2024-11-06")])
    assert [point["values"]["pe"] for point in result["points"]] == [None, None, 10]
    assert result["bases"][-1]["available_from"] == "2024-11-06"


def test_split_adjusted_provider_shares_are_not_adjusted_twice():
    split = SimpleNamespace(ex_date=date(2024, 12, 2), split_factor=2)
    result = calculate(prices=[price("2024-11-29", 40), price("2024-12-02", 20)], splits=[split, split])
    before, after = result["points"]
    assert before["values"] == after["values"]
    assert after["values"]["pe"] == 5
    # Moving the output's reference date before the split must not change ratios.
    earlier = calculate(prices=[price("2024-11-29", 40)], splits=[split])
    assert earlier["points"][0]["values"] == before["values"]


def test_per_quarter_eps_uses_each_quarters_share_count():
    rows = statements()
    rows[-1].balance_sheet["commonStockSharesOutstanding"] = 20
    result = calculate(rows)
    assert result["points"][0]["values"]["pe"] == pytest.approx(40 / 3.5)
    assert result["points"][0]["values"]["ps"] == 2


def test_revisions_apply_after_availability_and_keep_their_own_split_vintage():
    import copy
    rows = statements()
    for row in rows:
        row.fetched_at = datetime(2024, 11, 2)
    revised = copy.deepcopy(rows[-1])
    revised.revision = 2
    revised.available_at = datetime(2024, 12, 9)
    revised.fetched_at = datetime(2024, 12, 9)
    revised.income_statement["netIncome"] = 20
    revised.balance_sheet["commonStockSharesOutstanding"] = 20
    rows.append(revised)
    split = SimpleNamespace(ex_date=date(2024, 12, 2), split_factor=2)
    result = calculate(rows, [price("2024-11-29", 40), price("2024-12-09", 20), price("2024-12-10", 20)], [split])
    assert [point["values"]["pe"] for point in result["points"]] == [10, 10, 8]


@pytest.mark.parametrize("kind", ["missing_quarter", "estimated_date", "missing_date"])
def test_incomplete_or_unknown_disclosure_windows_do_not_fabricate_ttm(kind):
    rows = statements()
    if kind == "missing_quarter":
        rows.pop(1)
    elif kind == "estimated_date":
        rows[1].availability_estimated = True
    else:
        rows[1].filing_at = rows[1].available_at = None
    result = calculate(rows)
    point = result["points"][0]
    assert point["values"]["pe"] is None
    assert point["values"]["ps"] is None
    assert point["values"]["pb"] == 2


@pytest.mark.parametrize("key,part,field,value", [
    ("pe", "income_statement", "netIncome", -10),
    ("pfcf", "cash_flow", "capitalExpenditures", None),
    ("ev_ebitda", "balance_sheet", "totalDebt", None),
    ("pb", "balance_sheet", "totalStockholderEquity", 0),
    ("pe", "balance_sheet", "commonStockSharesOutstanding", float("inf")),
])
def test_invalid_inputs_disable_affected_metrics_without_silent_zero(key, part, field, value):
    rows = statements()
    for row in rows:
        getattr(row, part)[field] = value
    result = calculate(rows)
    assert result["points"][0]["values"][key] is None
    meta = next(metric for metric in result["metrics"] if metric["key"] == key)
    assert meta["latest_reason"]
    assert meta["median"] is None


def test_zero_debt_cash_are_valid_but_conflicting_debt_is_not():
    rows = statements()
    rows[-1].balance_sheet.update(totalDebt=0, cashAndShortTermInvestments=0)
    assert calculate(rows)["points"][0]["values"]["ev_revenue"] == 1
    rows[-1].balance_sheet.update(totalDebt=100, shortLongTermDebtTotal=200)
    result = calculate(rows)["points"][0]["values"]
    assert result["ev_revenue"] is None
    assert result["ps"] == 1


def test_fx_mismatch_and_financial_sector_are_explicit():
    rows = statements()
    rows[-1].income_statement["currency_symbol"] = "EUR"
    result = calculate(rows)
    assert result["points"][0]["values"]["pe"] is None
    assert "currencies" in result["metrics"][0]["latest_reason"]
    result = calculate(sector="Financial Services")
    assert result["points"][0]["values"]["ev_revenue"] is None
    assert result["points"][0]["values"]["pe"] == 10


def test_stale_statement_and_missing_raw_close_are_gaps():
    result = calculate(prices=[price("2024-11-04"), price("2024-11-05", None), price("2025-04-02")])
    assert result["points"][0]["values"]["pe"] == 10
    assert all(v is None for v in result["points"][1]["values"].values())
    assert "180 days" in result["points"][-1]["reason"]
    assert result["metrics"][0]["latest_value"] is None
    assert result["metrics"][0]["median"] == 10


def test_weekly_and_monthly_use_final_actual_session_without_filling_missing_values():
    prices = [price("2024-11-04", 40), price("2024-11-07", 80), price("2024-11-14", None)]
    weekly = calculate(prices=prices, interval="1wk")
    assert [point["date"] for point in weekly["points"]] == ["2024-11-08", "2024-11-15"]
    assert weekly["points"][0]["price_date"] == "2024-11-07"
    assert weekly["points"][0]["values"]["pe"] == 20
    assert weekly["points"][1]["values"]["pe"] is None
    monthly = calculate(prices=prices, interval="1mo")
    assert monthly["points"][0]["date"] == "2024-11-30"
    assert monthly["points"][0]["price_date"] == "2024-11-14"
    assert monthly["points"][0]["values"]["pe"] is None


def test_conflicting_split_records_disable_series():
    splits = [SimpleNamespace(ex_date=date(2024, 12, 2), split_factor=factor) for factor in [2, 3]]
    result = calculate(splits=splits)
    assert all(value is None for value in result["points"][0]["values"].values())
    assert "Conflicting split" in result["warnings"][0]


def test_section_currency_is_recovered_only_for_matching_source_rows():
    import copy
    rows = statements()
    payload = {"Financials": {}}
    for name, attr in [("Income_Statement", "income_statement"), ("Balance_Sheet", "balance_sheet"), ("Cash_Flow", "cash_flow")]:
        getattr(rows[-1], attr).pop("currency_symbol")
        payload["Financials"][name] = {"currency_symbol": "USD", "quarterly": {"2024-09-30": copy.deepcopy(getattr(rows[-1], attr))}}
    assert calculate(rows, fundamentals=payload)["points"][0]["values"]["pe"] == 10
    payload["Financials"]["Income_Statement"]["quarterly"]["2024-09-30"]["netIncome"] = 20
    assert calculate(rows, fundamentals=payload)["points"][0]["values"]["pe"] is None


def test_adr_requires_explicit_share_conversion_even_with_matching_currency():
    result = calculate(fundamentals={"General": {"HomeCategory": "ADR"}})
    assert all(value is None for value in result["points"][0]["values"].values())
    assert "receipt" in result["metrics"][0]["latest_reason"]


@pytest.mark.asyncio
async def test_database_service_and_read_only_endpoint(db_session, monkeypatch):
    from models import DailyPrice, FundamentalVersion, Ticker
    from main import app
    db_session.add(Ticker(ticker="TEST.US", currency="USD", sector="Technology"))
    await db_session.flush()
    db_session.add(DailyPrice(ticker="TEST.US", date=date(2024, 11, 4), close=40, adjusted_close=3))
    for row in statements():
        values = vars(row).copy()
        values.pop("raw_snapshot_id")
        db_session.add(FundamentalVersion(ticker="TEST.US", period_type="Quarterly", **values))
    await db_session.commit()
    async def fresh(*args, **kwargs):
        return SimpleNamespace(needs_sync=False)
    monkeypatch.setattr("api.routers.assess_ticker_freshness", fresh)
    result = await get_valuation_history("TEST.US", db_session)
    assert result["points"][0]["values"]["pe"] == 10
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/stocks/TEST/valuation-history?interval=1wk")
        assert response.status_code == 200
        assert set(response.json()["points"][0]["values"]) == set(KEYS)
        assert response.json()["points"][0]["date"] == "2024-11-08"
        assert (await client.get("/api/stocks/TEST/valuation-history?interval=intraday")).status_code == 422
        empty = await client.get("/api/stocks/MISSING/valuation-history")
        assert empty.status_code == 200
        assert empty.json()["points"] == []
        stock = await client.get("/api/stocks/TEST")
        assert stock.status_code == 200
        assert stock.json()["valuation_history"]["points"][0]["values"]["pe"] == 10
