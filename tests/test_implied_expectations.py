from datetime import date
from types import SimpleNamespace

import pytest

from models import DailyPrice, FinancialStatement, Ticker
from services.decision_support import build_company_valuation_assumptions, build_financial_context, get_decision_support
from services.expectation_signals import consensus_fiscal_years
from services.implied_expectations import (
    DEFAULT_REQUIRED_RETURN,
    MAX_SALES_TO_CAPITAL,
    build_implied_expectations,
    consensus_growth_path,
    consensus_references,
    enterprise_value,
    growth_path,
    history_references,
)
from services.raw_store import persist_snapshot

TERMINAL_GROWTH = 0.025
CONSENSUS = {"next_year_growth": 0.18, "following_year_growth": 0.15, "operating_margin": 0.24}
HISTORY = {"revenue_growth": 0.12, "revenue_growth_years": 5, "operating_margin_low": 0.15, "operating_margin_high": 0.28}


def _company(**overrides):
    values = dict(
        current_price=40.0, shares=100.0, cash=500.0, debt=300.0, equity=1_200.0,
        revenue=1_000.0, operating_margin=0.20, tax_rate=0.21, terminal_growth=TERMINAL_GROWTH,
        history=HISTORY, consensus=CONSENSUS,
    )
    return build_implied_expectations(**{**values, **overrides})


def _value(result, *, target_margin, growth_rates, discount_rate):
    inputs = result["inputs"]
    return enterprise_value(
        revenue=inputs["revenue_ttm"], start_margin=inputs["operating_margin_ttm"], target_margin=target_margin,
        growth_rates=growth_rates, tax_rate=inputs["tax_rate"], sales_to_capital=inputs["sales_to_capital"],
        discount_rate=discount_rate, terminal_growth=TERMINAL_GROWTH,
    )


def test_every_solved_hurdle_reproduces_the_price():
    result = _company()
    target = result["inputs"]["target_enterprise_value"]

    assert result["available"] is True
    assert target == pytest.approx(40 * 100 - 500 + 300)
    assert [item["required_return"] for item in result["hurdles"]] == [0.08, 0.10, 0.12]
    for hurdle in result["hurdles"]:
        assert [item["basis"] for item in hurdle["growth_by_margin"]] == ["trailing", "consensus", "five_year_high"]
        for item in hurdle["growth_by_margin"]:
            assert _value(
                result, target_margin=item["target_margin"],
                growth_rates=growth_path(item["revenue_growth"], TERMINAL_GROWTH),
                discount_rate=hurdle["required_return"],
            ) == pytest.approx(target, rel=1e-6)
        assert _value(
            result, target_margin=hurdle["margin_at_consensus_growth"],
            growth_rates=consensus_growth_path(0.18, 0.15, TERMINAL_GROWTH),
            discount_rate=hurdle["required_return"],
        ) == pytest.approx(target, rel=1e-6)
    # At 8% the price needs less revenue per dollar of capital than the company earns today.
    needed = result["hurdles"][0]["sales_to_capital_at_consensus"]
    assert needed < result["inputs"]["sales_to_capital"]
    assert enterprise_value(
        revenue=1_000.0, start_margin=0.20, target_margin=0.24, tax_rate=0.21, sales_to_capital=needed,
        growth_rates=consensus_growth_path(0.18, 0.15, TERMINAL_GROWTH), discount_rate=0.08,
        terminal_growth=TERMINAL_GROWTH,
    ) == pytest.approx(target, rel=1e-6)


def test_a_higher_required_return_or_a_lower_margin_needs_more_growth():
    hurdles = _company()["hurdles"]
    by_basis = [{item["basis"]: item["revenue_growth"] for item in hurdle["growth_by_margin"]} for hurdle in hurdles]

    assert by_basis[0]["trailing"] < by_basis[1]["trailing"] < by_basis[2]["trailing"]
    assert all(row["five_year_high"] < row["consensus"] < row["trailing"] for row in by_basis)


def test_implied_return_discounts_the_consensus_path_back_to_the_price():
    result = _company()
    implied = result["implied_return"]

    assert implied["margin_basis"] == "consensus"
    assert implied["growth_rates"][:3] == pytest.approx([0.18, 0.15, 0.15 + (TERMINAL_GROWTH - 0.15) / 8])
    assert implied["growth_rates"][-1] == pytest.approx(TERMINAL_GROWTH)
    assert _value(
        result, target_margin=0.24, growth_rates=implied["growth_rates"], discount_rate=implied["rate"],
    ) == pytest.approx(result["inputs"]["target_enterprise_value"], rel=1e-6)
    expected = "above_required" if implied["rate"] >= DEFAULT_REQUIRED_RETURN else "below_required"
    assert result["assessment"]["return_vs_required"] == expected


def test_assessment_places_the_default_hurdle_against_history_and_consensus():
    cheap = _company(current_price=15.0)["assessment"]
    dear = _company(current_price=400.0)["assessment"]

    assert cheap["growth_margin_basis"] == "consensus"
    assert cheap["required_growth"] < 0.12 and cheap["required_growth_vs_references"] == "below_references"
    assert dear["required_growth"] > 0.18 and dear["required_growth_vs_references"] == "above_references"
    assert cheap["return_vs_required"] == "above_required"
    assert dear["return_vs_required"] == "below_required"


def test_a_return_below_the_search_range_is_reported_as_such():
    implied = _company(current_price=400.0)["implied_return"]

    assert (implied["status"], implied["rate"], implied["minimum_rate"]) == ("below_range", None, 0.03)
    assert _company()["implied_return"]["status"] == "solved"
    assert _company(consensus=None)["implied_return"]["status"] == "unavailable"


def test_without_consensus_only_the_growth_hurdles_are_solved():
    result = _company(consensus=None)

    assert result["available"] is True
    assert result["implied_return"]["rate"] is None
    assert result["assessment"]["return_vs_required"] is None
    assert result["assessment"]["growth_margin_basis"] == "trailing"
    assert [item["basis"] for item in result["hurdles"][0]["growth_by_margin"]] == ["trailing", "five_year_high"]
    assert result["hurdles"][0]["margin_at_consensus_growth"] is None
    assert result["hurdles"][0]["sales_to_capital_at_consensus"] is None


def test_reinvestment_comes_from_invested_capital_and_is_capped_when_it_is_tiny():
    assert _company()["inputs"]["sales_to_capital"] == pytest.approx(1_000 / (1_200 + 300 - 500))
    net_cash = _company(equity=100.0, debt=0.0)["inputs"]
    assert net_cash["invested_capital"] == pytest.approx(-400)
    assert net_cash["sales_to_capital"] == MAX_SALES_TO_CAPITAL
    assert net_cash["sales_to_capital_basis"] == "capped"
    # Capital-heavy revenue makes growth worth less, so the same price needs more of it.
    heavy = _company(equity=1_700.0)["hurdles"][0]["growth_by_margin"][0]["revenue_growth"]
    light = _company()["hurdles"][0]["growth_by_margin"][0]["revenue_growth"]
    assert heavy > light
    # When new capital earns less than the required return, no growth rate justifies the price.
    assert _company(equity=4_000.0)["hurdles"][0]["growth_by_margin"][0]["revenue_growth"] is None


def test_shrinking_revenue_does_not_release_invested_capital():
    path = dict(revenue=1_000.0, start_margin=0.2, target_margin=0.2, tax_rate=0.21,
                growth_rates=growth_path(-0.10, TERMINAL_GROWTH), discount_rate=0.10, terminal_growth=TERMINAL_GROWTH)

    # Only the years that grow again need capital, so heavier capital needs can only lower the value.
    assert enterprise_value(**path, sales_to_capital=0.5) < enterprise_value(**path, sales_to_capital=5.0)
    shrinking_forever = {**path, "growth_rates": [-0.10] * 10}
    assert enterprise_value(**shrinking_forever, sales_to_capital=0.5) == pytest.approx(
        enterprise_value(**shrinking_forever, sales_to_capital=5.0)
    )


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"current_price": None}, "a positive current price"),
        ({"revenue": 0.0}, "positive trailing-twelve-month revenue"),
        ({"operating_margin": None}, "trailing-twelve-month operating income"),
        ({"equity": None}, "stockholders' equity"),
        ({"cash": 20_000.0}, "at or below net cash"),
        ({"operating_margin": -0.10, "consensus": None, "history": {}}, "No revenue growth"),
    ],
)
def test_unavailable_results_say_why(overrides, reason):
    result = _company(**overrides)

    assert result["available"] is False
    assert reason in " ".join(result["reasons"])


def test_consensus_blends_the_fiscal_years_into_the_next_twelve_months():
    fiscal_years = [
        {"period_end": date(2026, 12, 31), "revenue": 1_100.0, "eps": 2.0},
        {"period_end": date(2027, 12, 31), "revenue": 1_300.0, "eps": 2.6},
    ]
    blended = consensus_references(
        fiscal_years, trailing_revenue=1_050.0, trailing_period_end=date(2026, 9, 30), shares=100.0, tax_rate=0.20,
    )
    remaining = 92 / 365
    assert blended["next_year_growth"] == pytest.approx((remaining * 1_100 + (1 - remaining) * 1_300) / 1_050 - 1)
    assert blended["following_year_growth"] == pytest.approx(1_300 / 1_100 - 1)
    assert blended["operating_margin"] == pytest.approx(2.6 * 100 / 0.8 / 1_300)

    # Once the current fiscal year is fully reported, the next twelve months are the following year.
    reported = consensus_references(
        fiscal_years, trailing_revenue=1_100.0, trailing_period_end=date(2026, 12, 31), shares=100.0, tax_rate=0.20,
    )
    assert reported["next_year_growth"] == pytest.approx(1_300 / 1_100 - 1)

    loss = consensus_references(
        [fiscal_years[0], {**fiscal_years[1], "eps": -1.0}],
        trailing_revenue=1_050.0, trailing_period_end=date(2026, 9, 30), shares=100.0, tax_rate=0.20,
    )
    assert loss["operating_margin"] is None and loss["next_year_growth"] is not None
    assert consensus_references(
        [], trailing_revenue=1_050.0, trailing_period_end=date(2026, 9, 30), shares=100.0, tax_rate=0.20,
    ) is None
    assert consensus_references(
        [fiscal_years[0], {**fiscal_years[1], "revenue": None}],
        trailing_revenue=1_050.0, trailing_period_end=date(2026, 9, 30), shares=100.0, tax_rate=0.20,
    ) is None


def test_consensus_fiscal_years_need_both_years():
    trend = {
        "2026-12-31": {"date": "2026-12-31", "period": "0y", "revenueEstimateAvg": "1100", "epsTrendCurrent": "2.0"},
        "2027-12-31": {"date": "2027-12-31", "period": "+1y", "revenueEstimateAvg": "1300", "earningsEstimateAvg": "2.6"},
    }
    assert consensus_fiscal_years({"Earnings": {"Trend": trend}}, date(2026, 11, 1)) == [
        {"period_end": date(2026, 12, 31), "revenue": 1_100.0, "eps": 2.0},
        {"period_end": date(2027, 12, 31), "revenue": 1_300.0, "eps": 2.6},
    ]
    del trend["2027-12-31"]
    assert consensus_fiscal_years({"Earnings": {"Trend": trend}}, date(2026, 11, 1)) == []
    assert consensus_fiscal_years(None, None) == []


def test_history_covers_the_last_five_fiscal_years():
    points = [
        {"revenue": revenue, "operating_income": revenue * margin}
        for revenue, margin in ((200.0, 0.20), (170.0, 0.25), (150.0, 0.10), (130.0, 0.15), (115.0, 0.18), (100.0, 0.40))
    ]
    history = history_references(points)

    assert history["revenue_growth"] == pytest.approx(2 ** (1 / 5) - 1)
    assert history["revenue_growth_years"] == 5
    # The sixth year only anchors growth; its margin is outside the window.
    assert (history["operating_margin_low"], history["operating_margin_high"]) == pytest.approx((0.10, 0.25))
    assert history_references(points[:3]) == {
        "revenue_growth": None, "revenue_growth_years": None,
        "operating_margin_low": pytest.approx(0.10), "operating_margin_high": pytest.approx(0.25),
    }


def _statement(ticker, fiscal_date, period, *, revenue, shares=100):
    return FinancialStatement(
        ticker=ticker, fiscal_date=fiscal_date, period=period, revenue=revenue, net_income=revenue * 0.15,
        income_statement={
            "totalRevenue": revenue, "grossProfit": revenue * 0.6, "operatingIncome": revenue * 0.2,
            "netIncome": revenue * 0.15, "interestExpense": revenue * 0.01,
            "incomeBeforeTax": revenue * 0.19, "incomeTaxExpense": revenue * 0.04,
        },
        # Capital spending far above operating cash flow: the trailing-FCF DCF has no answer here.
        cash_flow={"freeCashFlow": -revenue * 0.3},
        balance_sheet={
            "cashAndShortTermInvestments": 300, "totalDebt": 500, "totalStockholderEquity": 800,
            "commonStockSharesOutstanding": shares,
        },
    )


@pytest.mark.asyncio
async def test_decision_support_solves_a_company_whose_free_cash_flow_is_negative(db_session):
    ticker = "CAPEX.US"
    db_session.add(Ticker(ticker=ticker, name="Capex Fixture"))
    db_session.add(DailyPrice(ticker=ticker, date=date(2026, 9, 30), close=60, adjusted_close=60))
    quarters = [date(2026, 9, 30), date(2026, 6, 30), date(2026, 3, 31), date(2025, 12, 31),
                date(2025, 9, 30), date(2025, 6, 30), date(2025, 3, 31), date(2024, 12, 31)]
    db_session.add_all([_statement(ticker, value, "Quarterly", revenue=250) for value in quarters])
    db_session.add_all([
        _statement(ticker, date(2025 - index, 12, 31), "Yearly", revenue=900 / 1.1 ** index) for index in range(6)
    ])
    await db_session.commit()
    await persist_snapshot(db_session, "EODHD", "fundamentals", {
        "General": {"UpdatedAt": date.today().isoformat()},
        "Earnings": {"Trend": {
            "2026-12-31": {"date": "2026-12-31", "period": "0y", "revenueEstimateAvg": "1020", "epsTrendCurrent": "1.6"},
            "2027-12-31": {"date": "2027-12-31", "period": "+1y", "revenueEstimateAvg": "1180", "epsTrendCurrent": "2.0"},
        }},
    }, details={"ticker": ticker})

    result = await get_decision_support(ticker, db_session)
    implied = result["implied_expectations"]

    assert result["valuation"]["available"] is False
    assert implied["available"] is True
    assert implied["inputs"]["revenue_ttm"] == pytest.approx(1_000)
    assert implied["inputs"]["target_enterprise_value"] == pytest.approx(60 * 100 - 300 + 500)
    assert implied["references"]["history"]["revenue_growth"] == pytest.approx(0.10)
    assert implied["references"]["consensus"]["following_year_growth"] == pytest.approx(1_180 / 1_020 - 1)
    assert implied["implied_return"]["rate"] is not None
    assert all(item["revenue_growth"] is not None for item in implied["hurdles"][1]["growth_by_margin"])


def test_statement_shares_replace_a_single_class_provider_count():
    records = [
        _statement("DUAL.US", value, "Quarterly", revenue=250, shares=220)
        for value in (date(2026, 9, 30), date(2026, 6, 30), date(2026, 3, 31), date(2025, 12, 31))
    ]
    financial = build_financial_context(records, share_reference_date=date(2026, 10, 1))

    def shares_for(snapshot_shares, market_cap):
        snapshot = SimpleNamespace(
            provider_beta=1.0, beta_1yr=None, market_cap=market_cap, shares_outstanding=snapshot_shares,
            date=date(2026, 10, 1), sales_growth_3yr=None, sales_growth_5yr=None,
        )
        return build_company_valuation_assumptions(financial, snapshot, 50.0)["shares"]

    # One listed class of two: 100 shares against a market value that implies about 220.
    one_class = shares_for(100, 11_500)
    assert one_class["value"] == pytest.approx(220)
    assert one_class["source"] == "split_adjusted_statement_shares"
    # A provider count that agrees with the market value stays.
    assert shares_for(215, 11_000)["source"] == "published_screener_shares_outstanding"
    # Neither count matches (a share-class price gap, an ADR ratio): leave the provider count alone.
    assert shares_for(100, 40_000)["source"] == "published_screener_shares_outstanding"
