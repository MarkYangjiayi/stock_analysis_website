import asyncio
from datetime import date, timedelta
from threading import Event
from unittest.mock import AsyncMock, Mock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from core.config import settings
from core.time_utils import utc_now
from models import EarningsQualityAnalysisRun, FinancialStatement, Ticker
from services.earnings_quality import (
    EARNINGS_QUALITY_PROMPT_VERSION,
    EARNINGS_QUALITY_SCHEMA_VERSION,
    evaluate_statement,
    evaluate_statement_series,
    get_earnings_quality,
    get_statement_for_period,
    statement_fingerprint,
)
from services.earnings_quality_validation import (
    FilingEarningsQualityExtraction,
    validate_filing_extraction,
)
from services.raw_store import persist_snapshot


def statement(
    period_end: date,
    *,
    period: str = "Quarterly",
    revenue=1_000,
    pretax=100,
    net_income=80,
    continuing=80,
    **fields,
) -> FinancialStatement:
    income = {
        "totalRevenue": revenue,
        "incomeBeforeTax": pretax,
        "netIncome": net_income,
        "netIncomeFromContinuingOps": continuing,
        "incomeTaxExpense": 20,
        **fields,
    }
    return FinancialStatement(
        ticker="TEST.US",
        fiscal_date=period_end,
        period=period,
        revenue=revenue,
        net_income=net_income,
        income_statement=income,
    )


@pytest.mark.parametrize(
    ("amount", "severity"),
    [(10, "warning"), (-10, "warning"), (25, "high"), (-25, "high")],
)
def test_materiality_thresholds_are_inclusive_and_sign_symmetric(amount, severity):
    result = evaluate_statement(
        statement(date(2025, 12, 31), nonRecurring=amount)
    )

    assert result["materiality_base"] == 100
    assert result["flags"][0]["severity"] == severity
    assert result["flags"][0]["amount"] == amount
    assert result["flags"][0]["treatment"] == "flag_only"


def test_revenue_floor_handles_near_break_even_pre_tax_income():
    result = evaluate_statement(
        statement(
            date(2025, 12, 31),
            revenue=10_000,
            pretax=1,
            nonRecurring=11,
        )
    )

    assert result["materiality_base"] == 100
    assert result["flags"][0]["severity"] == "warning"


def test_missing_materiality_fields_is_unavailable_not_clean():
    record = statement(
        date(2025, 12, 31),
        revenue=None,
        pretax=None,
        net_income=None,
        continuing=None,
    )
    result = evaluate_statement(record)

    assert result["assessment"] == "unavailable"
    assert {warning["code"] for warning in result["data_quality_warnings"]} == {
        "materiality_base_unavailable",
        "earnings_quality_fields_incomplete",
    }


def test_clean_verdict_requires_every_screening_field():
    complete = statement(
        date(2025, 12, 31),
        nonRecurring=0,
        extraordinaryItems=0,
        discontinuedOperations=0,
        nonOperatingIncomeNetOther=0,
    )
    missing_tax = statement(
        date(2025, 12, 31),
        nonRecurring=0,
        extraordinaryItems=0,
        discontinuedOperations=0,
        nonOperatingIncomeNetOther=0,
    )
    missing_tax.income_statement.pop("incomeTaxExpense")

    assert evaluate_statement(complete)["assessment"] == "no_material_candidates"
    assert evaluate_statement(missing_tax)["assessment"] == "unavailable"


def test_unreconciled_continuing_operations_gap_is_data_quality_warning():
    result = evaluate_statement(
        statement(
            date(2025, 12, 31),
            net_income=100,
            continuing=70,
            discontinuedOperations=5,
        )
    )

    assert "continuing_operations_gap_unreconciled" in {
        warning["code"] for warning in result["data_quality_warnings"]
    }
    assert not any(
        flag["category"] == "continuing_operations_reconciliation"
        for flag in result["flags"]
    )


def test_reconciled_continuing_operations_gap_can_be_classified_as_flag():
    result = evaluate_statement(
        statement(
            date(2025, 12, 31),
            net_income=100,
            continuing=70,
            discontinuedOperations=30,
        )
    )

    assert any(
        flag["category"] == "continuing_operations_reconciliation"
        for flag in result["flags"]
    )


def test_annual_recurrence_marks_second_occurrence_as_not_one_time():
    records = [
        statement(date(year, 12, 31), period="Yearly", nonRecurring=20)
        for year in (2025, 2024, 2023)
    ]
    results = evaluate_statement_series(records)

    assert all(
        flag["recurring_adjustment"]
        for result in results
        for flag in result["flags"]
        if flag["category"] == "non_recurring"
    )
    assert all(
        flag["treatment"] == "recurring_flag_only"
        for result in results
        for flag in result["flags"]
        if flag["category"] == "non_recurring"
    )


def test_quarterly_recurrence_requires_four_of_eight():
    dates = [
        date(2025, 12, 31), date(2025, 9, 30), date(2025, 6, 30), date(2025, 3, 31),
        date(2024, 12, 31), date(2024, 9, 30), date(2024, 6, 30), date(2024, 3, 31),
    ]
    records = [
        statement(value, nonRecurring=20 if index < 4 else 0)
        for index, value in enumerate(dates)
    ]

    results = evaluate_statement_series(records)
    flags = [
        flag
        for result in results
        for flag in result["flags"]
        if flag["category"] == "non_recurring"
    ]
    assert len(flags) == 4
    assert all(flag["recurring_adjustment"] for flag in flags)


def test_financial_company_disables_non_operating_swing_rule():
    previous = statement(
        date(2025, 9, 30),
        nonOperatingIncomeNetOther=0,
    )
    current = statement(
        date(2025, 12, 31),
        nonOperatingIncomeNetOther=50,
    )

    non_financial = evaluate_statement(
        current,
        previous_same_frequency=previous,
        financial_company=False,
    )
    financial = evaluate_statement(
        current,
        previous_same_frequency=previous,
        financial_company=True,
    )

    assert any(flag["category"] == "non_operating_swing" for flag in non_financial["flags"])
    assert not any(flag["category"] == "non_operating_swing" for flag in financial["flags"])


VALID_SOURCE = "The company recorded an impairment charge of 25, a tax benefit of 5, and an after-tax earnings effect of 20; adjusted net income was 120 and adjusted diluted EPS was 1.2."


def test_reported_net_income_falls_back_from_invalid_json_to_column():
    import services.filing_analysis as filing_analysis

    record = statement(date(2025, 12, 31), net_income=80)
    record.income_statement["netIncome"] = None
    assert filing_analysis._reported_net_income(record) == 80

    record.income_statement["netIncome"] = "not-a-number"
    assert filing_analysis._reported_net_income(record) == 80


def test_relevant_context_honors_configured_character_cap(monkeypatch):
    import services.filing_analysis as filing_analysis

    monkeypatch.setattr(settings, "EARNINGS_QUALITY_MAX_CONTEXT_CHARS", 30)
    documents = [
        {
            "source_id": f"filing:{index}",
            "accession": str(index),
            "form": "10-K",
            "document_name": f"report-{index}.htm",
            "source_url": f"https://www.sec.gov/{index}",
            "text": "Adjusted net income and impairment details are disclosed here.",
        }
        for index in range(2)
    ]

    context = filing_analysis._relevant_context(documents)

    assert [len(item["text"]) for item in context] == [15, 15]
    assert sum(len(item["text"]) for item in context) == 30


def test_relevant_context_prioritizes_unrealized_gain_over_generic_matches(
    monkeypatch,
):
    import services.filing_analysis as filing_analysis

    monkeypatch.setattr(settings, "EARNINGS_QUALITY_MAX_CONTEXT_CHARS", 300)
    documents = [{
        "source_id": "filing:primary",
        "accession": "0001",
        "form": "10-Q",
        "document_name": "report.htm",
        "source_url": "https://www.sec.gov/report.htm",
        "text": "\n".join([
            *[
                f"Investment and fair value background line {index}"
                for index in range(220)
            ],
            "Other income included unrealized gains on equity securities of $99.0 billion.",
            *[f"Trailing filing line {index}" for index in range(20)],
        ]),
    }]

    context = filing_analysis._relevant_context(documents)

    assert "unrealized gains on equity securities" in context[0]["text"]


def extraction_payload(**overrides):
    payload = {
        "period_end": "2025-12-31",
        "currency": "USD",
        "unit_scale": 1,
        "reported_net_income": 100,
        "adjustments": [{
            "category": "impairment",
            "label": "Asset impairment",
            "pretax_earnings_effect": -25,
            "tax_effect": 5,
            "earnings_effect_after_tax": -20,
            "include_in_normalized": True,
            "recurring": False,
            "cash_effect": "non_cash",
            "citation": {
                "source_id": "filing:primary",
                "accession": "0001",
                "document_name": "report.htm",
                "section": "Impairment",
                "excerpt": VALID_SOURCE,
                "source_amount": -25,
                "source_unit_scale": 1,
            },
        }],
        "company_adjusted": {
            "label": "Adjusted diluted EPS",
            "adjusted_net_income": 120,
            "adjusted_diluted_eps": 1.2,
            "net_income_citation": {
                "source_id": "filing:primary",
                "accession": "0001",
                "document_name": "report.htm",
                "section": "Impairment",
                "excerpt": VALID_SOURCE,
                "source_amount": 120,
                "source_unit_scale": 1,
            },
            "diluted_eps_citation": {
                "source_id": "filing:primary",
                "accession": "0001",
                "document_name": "report.htm",
                "section": "Adjusted EPS",
                "excerpt": VALID_SOURCE,
                "source_amount": 1.2,
                "source_unit_scale": 1,
            },
        },
        "disclosed_adjusted_net_income": 120,
        "disclosed_adjusted_diluted_eps": 1.2,
        "notes": [],
    }
    payload.update(overrides)
    return payload


def validate(payload):
    extraction = FilingEarningsQualityExtraction.model_validate(payload)
    return validate_filing_extraction(
        extraction,
        expected_period_end=date(2025, 12, 31),
        expected_currency="USD",
        reported_net_income=100,
        source_documents={"filing:primary": VALID_SOURCE},
    )


def test_complete_filing_reconciliation_produces_verified_normalized_values():
    result, report = validate(extraction_payload())

    assert report["verified"] is True
    assert result["verification_status"] == "verified"
    assert result["normalized_net_income"] == 120
    assert result["adjusted_eps"] == 1.2


def test_gain_is_normalized_symmetrically_with_charge():
    gain_source = "The company reported a disposal gain of 25, tax expense of 5, and an after-tax earnings effect of 20; adjusted net income was 80."
    payload = extraction_payload(
        adjustments=[{
            **extraction_payload()["adjustments"][0],
            "category": "asset_or_business_disposal",
            "label": "Disposal gain",
            "pretax_earnings_effect": 25,
            "tax_effect": -5,
            "earnings_effect_after_tax": 20,
            "citation": {
                **extraction_payload()["adjustments"][0]["citation"],
                "source_amount": 25,
                "excerpt": gain_source,
            },
        }],
        disclosed_adjusted_net_income=80,
        disclosed_adjusted_diluted_eps=None,
        company_adjusted={
            "label": "Adjusted net income",
            "adjusted_net_income": 80,
            "adjusted_diluted_eps": None,
            "net_income_citation": {
                "source_id": "filing:primary",
                "accession": "0001",
                "document_name": "report.htm",
                "section": "Disposal",
                "excerpt": gain_source,
                "source_amount": 80,
                "source_unit_scale": 1,
            },
            "diluted_eps_citation": None,
        },
    )
    extraction = FilingEarningsQualityExtraction.model_validate(payload)
    result, report = validate_filing_extraction(
        extraction,
        expected_period_end=date(2025, 12, 31),
        expected_currency="USD",
        reported_net_income=100,
        source_documents={"filing:primary": gain_source},
    )

    assert report["verified"] is True
    assert result["normalized_net_income"] == 80


def test_deterministically_recurring_event_cannot_enter_normalized_result():
    extraction = FilingEarningsQualityExtraction.model_validate(extraction_payload())
    result, report = validate_filing_extraction(
        extraction,
        expected_period_end=date(2025, 12, 31),
        expected_currency="USD",
        reported_net_income=100,
        source_documents={"filing:primary": VALID_SOURCE},
        recurring_categories={"impairment"},
    )

    assert report["verified"] is False
    assert result["normalized_net_income"] is None
    assert "category_not_normalizable" in {
        failure["code"] for failure in report["failures"]
    }


def test_recurring_provider_amount_binds_to_filing_level_category():
    import services.filing_analysis as filing_analysis

    extraction = FilingEarningsQualityExtraction.model_validate(extraction_payload())
    categories = filing_analysis._recurring_categories_for_extraction(
        [{
            "category": "non_recurring",
            "amount": -25,
            "recurring_adjustment": True,
        }],
        extraction,
    )

    assert "non_recurring" in categories
    assert "impairment" in categories


@pytest.mark.parametrize(
    ("mutator", "failure_code"),
    [
        (lambda payload: payload.update(currency="EUR"), "currency_mismatch"),
        (lambda payload: payload.update(unit_scale=1_000), "unit_mismatch"),
        (lambda payload: payload["adjustments"][0].update(tax_effect=None), "tax_reconciliation_incomplete"),
        (lambda payload: payload["adjustments"][0]["citation"].update(excerpt="Invented amount 25"), "citation_not_located"),
        (lambda payload: payload["adjustments"][0].update(category="stock_based_compensation"), "category_not_normalizable"),
        (lambda payload: payload.update(disclosed_adjusted_net_income=999), "disclosed_reconciliation_mismatch"),
    ],
)
def test_validation_failures_withhold_adjusted_values(mutator, failure_code):
    payload = extraction_payload()
    mutator(payload)
    result, report = validate(payload)

    assert report["verified"] is False
    assert result["verification_status"] == "flag_only"
    assert result["normalized_net_income"] is None
    assert failure_code in {failure["code"] for failure in report["failures"]}


def test_unverified_eps_does_not_withhold_verified_normalized_net_income():
    payload = extraction_payload()
    payload["company_adjusted"]["diluted_eps_citation"].update(source_amount=9)

    result, report = validate(payload)

    assert report["verified"] is True
    assert report["eps_verified"] is False
    assert result["verification_status"] == "verified"
    assert result["normalized_net_income"] == 120
    assert result["adjusted_eps"] is None
    assert "adjusted_eps_unverified" in {
        failure["code"] for failure in report["eps_failures"]
    }


def test_citation_identity_must_match_the_saved_sec_document():
    payload = extraction_payload()
    payload["adjustments"][0]["citation"]["accession"] = "wrong-accession"
    extraction = FilingEarningsQualityExtraction.model_validate(payload)

    result, report = validate_filing_extraction(
        extraction,
        expected_period_end=date(2025, 12, 31),
        expected_currency="USD",
        reported_net_income=100,
        source_documents={"filing:primary": VALID_SOURCE},
        source_metadata={
            "filing:primary": {
                "accession": "0001",
                "document_name": "report.htm",
            }
        },
    )

    assert report["verified"] is False
    assert result["normalized_net_income"] is None
    assert "citation_not_located" in {
        failure["code"] for failure in report["failures"]
    }


def test_pretax_only_disclosure_cannot_verify_tax_or_after_tax_amounts():
    pretax_only_source = "The company recorded an impairment charge of 25 and reported adjusted net income of 120 and adjusted diluted EPS of 1.2."
    payload = extraction_payload()
    payload["adjustments"][0]["citation"]["excerpt"] = pretax_only_source
    payload["company_adjusted"]["net_income_citation"]["excerpt"] = pretax_only_source
    payload["company_adjusted"]["diluted_eps_citation"]["excerpt"] = pretax_only_source
    extraction = FilingEarningsQualityExtraction.model_validate(payload)

    result, report = validate_filing_extraction(
        extraction,
        expected_period_end=date(2025, 12, 31),
        expected_currency="USD",
        reported_net_income=100,
        source_documents={"filing:primary": pretax_only_source},
    )

    assert report["verified"] is False
    assert result["normalized_net_income"] is None
    assert {"tax_amount_not_located", "after_tax_amount_not_located"} <= {
        failure["code"] for failure in report["failures"]
    }


async def seed_supported_company(db_session, ticker="API.US", general=None):
    db_session.add(Ticker(
        ticker=ticker,
        name="API Company",
        sector="Technology",
        industry="Software",
        currency="USD",
    ))
    financial_statement = statement(date(2025, 12, 31), period="Yearly")
    financial_statement.ticker = ticker
    db_session.add(financial_statement)
    await db_session.flush()
    await persist_snapshot(
        db_session,
        "EODHD",
        "fundamentals",
        {
            "General": general if general is not None else {
                "CIK": "0000320193",
                "Type": "Common Stock",
                "CountryISO": "USA",
            }
        },
        details={"ticker": ticker},
    )
    await db_session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ticker", "general", "reason_fragment"),
    [
        ("ETF.US", {"CIK": "1", "Type": "ETF", "CountryISO": "USA"}, "ETFs and funds"),
        ("NOCIK.US", {"Type": "Common Stock", "CountryISO": "USA"}, "No CIK"),
        ("FOREIGN.US", {"CIK": "1", "Type": "Common Stock", "CountryISO": "CAN"}, "20-F/6-K"),
        ("LONDON.LSE", {"CIK": "1", "Type": "Common Stock", "CountryISO": "GBR"}, "Only U.S."),
    ],
)
async def test_unsupported_sec_security_types_are_explicit(
    db_session,
    ticker,
    general,
    reason_fragment,
):
    await seed_supported_company(db_session, ticker=ticker, general=general)

    response = await get_earnings_quality(ticker, db_session)

    assert response["sec_analysis"]["supported"] is False
    assert reason_fragment in response["sec_analysis"]["reason"]
    assert response["sec_analysis"]["unsupported_forms"] == ["20-F", "6-K"]


@pytest.mark.asyncio
async def test_public_earnings_quality_read_never_calls_sec_or_ai(
    db_session,
    monkeypatch,
):
    await seed_supported_company(db_session)
    from main import app
    import services.filing_analysis as filing_analysis

    fetch_mock = Mock(side_effect=AssertionError("SEC must not be called"))
    ai_mock = AsyncMock(side_effect=AssertionError("AI must not be called"))
    monkeypatch.setattr(filing_analysis, "_fetch_sec_documents_sync", fetch_mock)
    monkeypatch.setattr(filing_analysis, "generate_deepseek_json", ai_mock)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/stocks/API.US/earnings-quality")

    assert response.status_code == 200
    assert response.json()["sec_analysis"]["supported"] is True
    fetch_mock.assert_not_called()
    ai_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_recovered_analysis_rechecks_ai_kill_switch(
    db_session,
    monkeypatch,
):
    await seed_supported_company(db_session)
    import services.filing_analysis as filing_analysis

    monkeypatch.setattr(settings, "SEC_USER_AGENT", "Quantify test@example.com")
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "test-model-key")
    run, _ = await filing_analysis.enqueue_filing_analysis(
        db_session,
        "API.US",
        date(2025, 12, 31),
        "annual",
    )
    run_id = run.id
    run.stage = "recovered"
    await db_session.commit()

    sec_mock = Mock(side_effect=AssertionError("SEC must remain disabled"))
    ai_mock = AsyncMock(side_effect=AssertionError("AI must remain disabled"))
    monkeypatch.setattr(filing_analysis, "_fetch_sec_documents_sync", sec_mock)
    monkeypatch.setattr(filing_analysis, "generate_deepseek_json", ai_mock)
    monkeypatch.setattr(settings, "EARNINGS_QUALITY_AI_ENABLED", False)

    await filing_analysis.execute_filing_analysis(run_id)

    db_session.expire_all()
    failed = await db_session.get(EarningsQualityAnalysisRun, run_id)
    assert failed.status == "failed"
    assert "disabled" in failed.error_message.lower()
    sec_mock.assert_not_called()
    ai_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_analysis_post_fails_closed_when_admin_key_is_not_configured(
    db_session,
    monkeypatch,
):
    await seed_supported_company(db_session)
    from main import app

    monkeypatch.setattr(settings, "ADMIN_API_KEY", "")
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/personal/stocks/API.US/earnings-quality/analyses",
            json={"period_end": "2025-12-31", "period_type": "annual"},
        )

    assert response.status_code == 503
    assert response.json()["detail"] == "Admin operations are disabled"


@pytest.mark.asyncio
async def test_analysis_post_requires_admin_and_is_single_flight_with_free_cache_hits(
    db_session,
    monkeypatch,
):
    await seed_supported_company(db_session)
    from main import app
    import api.routers as routers

    monkeypatch.setattr(settings, "SEC_USER_AGENT", "Quantify test@example.com")
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "test-model-key")
    limiter = AsyncMock()
    scheduler = Mock()
    monkeypatch.setattr(routers, "limit_expensive_requests", limiter)
    monkeypatch.setattr(routers, "schedule_filing_analysis", scheduler)
    body = {"period_end": "2025-12-31", "period_type": "annual"}

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        unauthorized = await client.post(
            "/api/personal/stocks/API.US/earnings-quality/analyses",
            json=body,
        )
        first = await client.post(
            "/api/personal/stocks/API.US/earnings-quality/analyses",
            json=body,
            headers={"X-API-Key": "test-secret"},
        )
        second = await client.post(
            "/api/personal/stocks/API.US/earnings-quality/analyses",
            json=body,
            headers={"X-API-Key": "test-secret"},
        )

    assert unauthorized.status_code == 401
    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    limiter.assert_awaited_once()
    scheduler.assert_called_once_with(first.json()["id"])

    run = await db_session.get(EarningsQualityAnalysisRun, first.json()["id"])
    run.status = "completed"
    run.stage = "completed"
    run.active_key = None
    run.result = {
        "verification_status": "flag_only",
        "normalized_net_income": None,
    }
    await db_session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        cached = await client.post(
            "/api/personal/stocks/API.US/earnings-quality/analyses",
            json=body,
            headers={"X-API-Key": "test-secret"},
        )
        public_status = await client.get(
            f"/api/stocks/API.US/earnings-quality/analyses/{run.id}"
        )

    assert cached.status_code == 200
    assert public_status.status_code == 200
    assert public_status.json()["status"] == "completed"
    limiter.assert_awaited_once()
    scheduler.assert_called_once()


@pytest.mark.asyncio
async def test_statement_revision_naturally_invalidates_completed_cache(
    db_session,
    monkeypatch,
):
    await seed_supported_company(db_session)
    from services.filing_analysis import enqueue_filing_analysis

    monkeypatch.setattr(settings, "SEC_USER_AGENT", "Quantify test@example.com")
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "test-model-key")
    run, created = await enqueue_filing_analysis(
        db_session,
        "API.US",
        date(2025, 12, 31),
        "annual",
    )
    assert created is True
    run.status = "completed"
    run.active_key = None
    await db_session.commit()

    statement_result = await db_session.execute(
        select(FinancialStatement).where(
            FinancialStatement.ticker == "API.US",
            FinancialStatement.fiscal_date == date(2025, 12, 31),
        )
    )
    revised = statement_result.scalar_one()
    revised.income_statement = {**revised.income_statement, "netIncome": 81}
    revised.net_income = 81
    await db_session.commit()

    replacement, replacement_created = await enqueue_filing_analysis(
        db_session,
        "API.US",
        date(2025, 12, 31),
        "annual",
    )
    assert replacement_created is True
    assert replacement.id != run.id
    assert replacement.cache_identity != run.cache_identity


@pytest.mark.asyncio
async def test_statement_revision_supersedes_stale_active_run(
    db_session,
    monkeypatch,
):
    await seed_supported_company(db_session)
    from services.filing_analysis import enqueue_filing_analysis

    monkeypatch.setattr(settings, "SEC_USER_AGENT", "Quantify test@example.com")
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "test-model-key")
    stale, created = await enqueue_filing_analysis(
        db_session,
        "API.US",
        date(2025, 12, 31),
        "annual",
    )
    assert created is True

    statement_result = await db_session.execute(
        select(FinancialStatement).where(
            FinancialStatement.ticker == "API.US",
            FinancialStatement.fiscal_date == date(2025, 12, 31),
        )
    )
    revised = statement_result.scalar_one()
    revised.income_statement = {**revised.income_statement, "netIncome": 81}
    revised.net_income = 81
    await db_session.commit()

    replacement, replacement_created = await enqueue_filing_analysis(
        db_session,
        "API.US",
        date(2025, 12, 31),
        "annual",
    )

    await db_session.refresh(stale)
    assert replacement_created is True
    assert replacement.id != stale.id
    assert replacement.cache_identity != stale.cache_identity
    assert stale.status == "failed"
    assert stale.stage == "superseded"
    assert stale.active_key is None


@pytest.mark.asyncio
async def test_old_prompt_cache_is_not_exposed_as_current(
    db_session,
):
    await seed_supported_company(db_session)
    financial_statement = await get_statement_for_period(
        db_session,
        "API.US",
        date(2025, 12, 31),
        "annual",
    )
    assert financial_statement is not None
    old_run = EarningsQualityAnalysisRun(
        ticker="API.US",
        period_end=date(2025, 12, 31),
        period_type="annual",
        statement_fingerprint=statement_fingerprint(
            financial_statement,
            currency="USD",
        ),
        cache_identity="old-version",
        model=settings.DEEPSEEK_MODEL,
        prompt_version="earnings-quality-v1",
        schema_version=EARNINGS_QUALITY_SCHEMA_VERSION,
        status="completed",
        stage="completed",
        result={
            "verification_status": "verified",
            "normalized_net_income": 999,
            "adjusted_eps": 9.99,
        },
    )
    db_session.add(old_run)
    await db_session.commit()

    response = await get_earnings_quality("API.US", db_session)

    assert response["annual"][0]["analysis"] is None
    assert response["annual"][0]["verified_normalized"] is None


def test_sec_fixture_selects_matching_primary_nearest_earnings_8k_and_exhibit(
    monkeypatch,
):
    import edgar
    import services.filing_analysis as filing_analysis

    class FakeAttachment:
        document_type = "EX-99.1"
        document = "earnings-release.htm"
        description = "Earnings release"
        url = "https://www.sec.gov/exhibit"

        def download(self):
            return "<html><body>Adjusted net income was 120.</body></html>"

    class FakeFiling:
        def __init__(self, accession, form, filing_date, report_date, items="", attachments=None):
            self.accession_no = accession
            self.form = form
            self.filing_date = filing_date
            self.report_date = report_date
            self.items = items
            self.attachments = attachments or []
            self.primary_document = f"{accession}.htm"
            self.url = f"https://www.sec.gov/{accession}"

        def html(self):
            return f"<html><body>{self.form} {self.accession_no}</body></html>"

        def text(self):
            raise AssertionError("HTML should be converted without downloading the filing twice")

    primary = FakeFiling("primary", "10-Q", "2026-02-01", "2025-12-31")
    nearest = FakeFiling(
        "nearest",
        "8-K",
        "2026-01-20",
        "2025-12-31",
        items="2.02",
        attachments=[FakeAttachment()],
    )
    farther = FakeFiling("farther", "8-K", "2026-02-20", "2025-12-31", items="2.02")

    class FakeCompany:
        def get_filings(self, *, form, filing_date):
            assert filing_date == "2025-12-21:2026-04-30"
            return [primary] if form == "10-Q" else [farther, nearest]

    company_factory = Mock(return_value=FakeCompany())
    identity = Mock()
    monkeypatch.setattr(edgar, "Company", company_factory)
    monkeypatch.setattr(edgar, "set_identity", identity)
    monkeypatch.setattr(settings, "SEC_USER_AGENT", "Quantify test@example.com")

    documents = filing_analysis._fetch_sec_documents_sync(
        cik="320193",
        period_end=date(2025, 12, 31),
        period_type="quarterly",
    )

    assert [document["source_id"] for document in documents] == [
        "primary:primary",
        "nearest:primary",
        "nearest:exhibit-0",
    ]
    assert documents[-1]["form"] == "EX-99.1"
    company_factory.assert_called_once_with("320193", include_old_filings=False)
    identity.assert_called_once_with("Quantify test@example.com")


def test_quarterly_sec_fixture_falls_back_to_matching_ten_k(monkeypatch):
    import edgar
    import services.filing_analysis as filing_analysis

    class FakeFiling:
        accession_no = "year-end"
        form = "10-K"
        filing_date = "2026-02-01"
        report_date = "2025-12-31"
        items = ""
        attachments = []
        primary_document = "year-end.htm"
        url = "https://www.sec.gov/year-end"

        def html(self):
            return "<html><body>Fiscal year-end results</body></html>"

        def text(self):
            raise AssertionError("HTML should be converted without a second download")

    requested_forms = []

    class FakeCompany:
        def get_filings(self, *, form, filing_date):
            assert filing_date == "2025-12-21:2026-04-30"
            requested_forms.append(form)
            if form == "10-K":
                return [FakeFiling()]
            return []

    monkeypatch.setattr(edgar, "Company", Mock(return_value=FakeCompany()))
    monkeypatch.setattr(edgar, "set_identity", Mock())
    monkeypatch.setattr(settings, "SEC_USER_AGENT", "Quantify test@example.com")

    documents = filing_analysis._fetch_sec_documents_sync(
        cik="320193",
        period_end=date(2025, 12, 31),
        period_type="quarterly",
    )

    assert documents[0]["form"] == "10-K"
    assert documents[0]["source_id"] == "year-end:primary"
    assert requested_forms == ["10-Q", "10-K", "8-K"]


@pytest.mark.asyncio
async def test_clicked_job_uses_mocked_sec_and_ai_and_persists_verified_sources(
    db_session,
    monkeypatch,
):
    await seed_supported_company(db_session)
    import services.filing_analysis as filing_analysis

    monkeypatch.setattr(settings, "SEC_USER_AGENT", "Quantify test@example.com")
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "test-model-key")
    sec_mock = Mock(return_value=[{
        "source_id": "filing:primary",
        "accession": "0001",
        "form": "10-K",
        "filing_date": "2026-02-01",
        "report_date": "2025-12-31",
        "document_name": "report.htm",
        "source_url": "https://www.sec.gov/Archives/report.htm",
        "html": f"<html><body>{VALID_SOURCE}</body></html>",
        "text": VALID_SOURCE,
    }])
    ai_mock = AsyncMock(return_value=extraction_payload(reported_net_income=80))
    ai_mock.return_value["disclosed_adjusted_net_income"] = 100
    ai_mock.return_value["company_adjusted"]["adjusted_net_income"] = 100
    job_source = "The company recorded an impairment charge of 25, a tax benefit of 5, and an after-tax earnings effect of 20; adjusted net income was 100 and adjusted diluted EPS was 1.2."
    ai_mock.return_value["adjustments"][0]["citation"]["excerpt"] = job_source
    ai_mock.return_value["company_adjusted"]["net_income_citation"]["excerpt"] = job_source
    ai_mock.return_value["company_adjusted"]["net_income_citation"]["source_amount"] = 100
    ai_mock.return_value["company_adjusted"]["diluted_eps_citation"]["excerpt"] = job_source
    sec_mock.return_value[0]["html"] = f"<html><body>{job_source}</body></html>"
    sec_mock.return_value[0]["text"] = job_source
    monkeypatch.setattr(filing_analysis, "_fetch_sec_documents_sync", sec_mock)
    monkeypatch.setattr(filing_analysis, "generate_deepseek_json", ai_mock)

    run, created = await filing_analysis.enqueue_filing_analysis(
        db_session,
        "API.US",
        date(2025, 12, 31),
        "annual",
    )
    assert created is True
    run_id = run.id
    await filing_analysis.execute_filing_analysis(run_id)

    db_session.expire_all()
    completed = await db_session.get(EarningsQualityAnalysisRun, run_id)
    assert completed.status == "completed"
    assert completed.result["verification_status"] == "verified"
    assert completed.result["normalized_net_income"] == 100
    assert len(completed.source_snapshots) == 1
    assert completed.source_snapshots[0]["html_snapshot_id"]
    assert completed.source_snapshots[0]["text_snapshot_id"]
    sec_mock.assert_called_once()
    ai_mock.assert_awaited_once()
    assert ai_mock.await_args.kwargs["model"] == settings.DEEPSEEK_MODEL


@pytest.mark.asyncio
async def test_analysis_timeout_releases_single_flight_for_retry(
    db_session,
    monkeypatch,
):
    await seed_supported_company(db_session)
    import services.filing_analysis as filing_analysis

    monkeypatch.setattr(settings, "SEC_USER_AGENT", "Quantify test@example.com")
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "test-model-key")
    monkeypatch.setattr(settings, "EARNINGS_QUALITY_TIMEOUT_SECONDS", 0.01)

    async def slow_analysis(_run_id):
        await asyncio.sleep(0.1)

    monkeypatch.setattr(filing_analysis, "_perform_analysis", slow_analysis)
    run, _ = await filing_analysis.enqueue_filing_analysis(
        db_session,
        "API.US",
        date(2025, 12, 31),
        "annual",
    )
    run_id = run.id
    await filing_analysis.execute_filing_analysis(run_id)

    db_session.expire_all()
    failed = await db_session.get(EarningsQualityAnalysisRun, run_id)
    assert failed.status == "failed"
    assert failed.active_key is None
    assert "timed out" in failed.error_message.lower()

    retry, created = await filing_analysis.enqueue_filing_analysis(
        db_session,
        "API.US",
        date(2025, 12, 31),
        "annual",
    )
    assert created is True
    assert retry.id != run_id


@pytest.mark.asyncio
async def test_timeout_retains_global_slot_until_sec_thread_exits(
    db_session,
    monkeypatch,
):
    await seed_supported_company(db_session)
    quarterly_statement = statement(date(2025, 9, 30), period="Quarterly")
    quarterly_statement.ticker = "API.US"
    db_session.add(quarterly_statement)
    await db_session.commit()
    import services.filing_analysis as filing_analysis

    monkeypatch.setattr(settings, "SEC_USER_AGENT", "Quantify test@example.com")
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "test-model-key")
    monkeypatch.setattr(settings, "EARNINGS_QUALITY_TIMEOUT_SECONDS", 0.01)
    sec_started = Event()
    release_sec = Event()

    def blocked_sec_fetch(**_kwargs):
        sec_started.set()
        release_sec.wait(timeout=2)
        return []

    monkeypatch.setattr(
        filing_analysis,
        "_fetch_sec_documents_sync",
        blocked_sec_fetch,
    )
    annual, _ = await filing_analysis.enqueue_filing_analysis(
        db_session,
        "API.US",
        date(2025, 12, 31),
        "annual",
    )
    quarterly, _ = await filing_analysis.enqueue_filing_analysis(
        db_session,
        "API.US",
        date(2025, 9, 30),
        "quarterly",
    )
    annual_id = annual.id
    quarterly_id = quarterly.id

    execution = asyncio.create_task(
        filing_analysis.execute_filing_analysis(annual_id)
    )
    try:
        assert await asyncio.to_thread(sec_started.wait, 1)
        await asyncio.sleep(0.05)
        db_session.expire_all()
        in_flight = await db_session.get(EarningsQualityAnalysisRun, annual_id)
        assert in_flight.status == "running"
        assert in_flight.global_slot == filing_analysis.GLOBAL_ANALYSIS_SLOT
        assert await filing_analysis._claim(quarterly_id) is False
    finally:
        release_sec.set()
        await execution

    db_session.expire_all()
    failed = await db_session.get(EarningsQualityAnalysisRun, annual_id)
    assert failed.status == "failed"
    assert "timed out" in failed.error_message.lower()
    assert await filing_analysis._claim(quarterly_id) is True
    await filing_analysis._record_failure(quarterly_id, "test cleanup")


@pytest.mark.asyncio
async def test_database_global_slot_allows_only_one_running_analysis(
    db_session,
    monkeypatch,
):
    await seed_supported_company(db_session)
    quarterly_statement = statement(date(2025, 9, 30), period="Quarterly")
    quarterly_statement.ticker = "API.US"
    db_session.add(quarterly_statement)
    await db_session.commit()
    import services.filing_analysis as filing_analysis

    monkeypatch.setattr(settings, "SEC_USER_AGENT", "Quantify test@example.com")
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "test-model-key")
    annual, _ = await filing_analysis.enqueue_filing_analysis(
        db_session,
        "API.US",
        date(2025, 12, 31),
        "annual",
    )
    quarterly, _ = await filing_analysis.enqueue_filing_analysis(
        db_session,
        "API.US",
        date(2025, 9, 30),
        "quarterly",
    )

    assert await filing_analysis._claim(annual.id) is True
    assert await filing_analysis._claim(quarterly.id) is False

    await filing_analysis._record_failure(annual.id, "test cleanup")
    assert await filing_analysis._claim(quarterly.id) is True
    await filing_analysis._record_failure(quarterly.id, "test cleanup")


@pytest.mark.asyncio
async def test_recovery_requeues_only_expired_or_unowned_leases(
    db_session,
    monkeypatch,
):
    await seed_supported_company(db_session)
    import services.filing_analysis as filing_analysis

    expired = EarningsQualityAnalysisRun(
        ticker="API.US",
        period_end=date(2025, 12, 31),
        period_type="annual",
        statement_fingerprint="old",
        cache_identity="expired",
        model="model",
        prompt_version="v1",
        schema_version="v1",
        status="running",
        stage="calling_ai",
        active_key="expired-key",
        global_slot=filing_analysis.GLOBAL_ANALYSIS_SLOT,
        owner_token="dead-process",
        lease_expires_at=utc_now() - timedelta(seconds=1),
    )
    live = EarningsQualityAnalysisRun(
        ticker="API.US",
        period_end=date(2025, 9, 30),
        period_type="quarterly",
        statement_fingerprint="live",
        cache_identity="live",
        model="model",
        prompt_version="v1",
        schema_version="v1",
        status="running",
        stage="calling_ai",
        active_key="live-key",
        owner_token="live-process",
        lease_expires_at=utc_now() + timedelta(minutes=5),
    )
    db_session.add_all([expired, live])
    await db_session.commit()
    expired_id = expired.id
    live_id = live.id
    scheduled = Mock()
    monkeypatch.setattr(filing_analysis, "schedule_filing_analysis", scheduled)

    await filing_analysis.recover_interrupted_filing_analyses()
    db_session.expire_all()
    recovered = await db_session.get(EarningsQualityAnalysisRun, expired_id)
    untouched = await db_session.get(EarningsQualityAnalysisRun, live_id)

    assert recovered.status == "queued"
    assert recovered.stage == "recovered"
    assert recovered.global_slot is None
    assert recovered.owner_token is None
    assert untouched.status == "running"
    assert untouched.owner_token == "live-process"
    scheduled.assert_called_once_with(expired_id)
