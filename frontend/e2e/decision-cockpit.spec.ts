import { expect, Page, test } from "@playwright/test";

const scenarioInputs = [
    { scenario: "bear", fcf_growth_rate: 0.05, wacc: 0.105, perpetual_growth: 0.02 },
    { scenario: "base", fcf_growth_rate: 0.10, wacc: 0.09, perpetual_growth: 0.025 },
    { scenario: "bull", fcf_growth_rate: 0.15, wacc: 0.08, perpetual_growth: 0.03 },
];

const financialPoints = Array.from({ length: 8 }, (_, index) => ({
    date: `${index < 4 ? 2025 : 2024}-${["12-31", "09-30", "06-30", "03-31"][index % 4]}`,
    revenue: 100_000_000 - index * 2_000_000,
    net_income: 20_000_000 - index * 500_000,
    gross_margin: 0.50,
    operating_margin: 0.25,
    free_cash_flow: 18_000_000 - index * 400_000,
    cash_and_short_term_investments: 200_000_000,
    total_debt: 50_000_000,
    shares_outstanding: 100_000_000,
    price: 100 - index,
}));

const peerScope = (scope: "industry" | "sector", available = true) => ({
    scope,
    minimum_observations: scope === "industry" ? 10 : 20,
    observation_count: scope === "industry" ? 12 : 40,
    available,
    raw_percentile: available ? 80 : null,
    desirability_percentile: available ? 80 : null,
    reason: available ? null : "Insufficient valid observations.",
});

const peerMetric = {
    key: "sales_growth_ttm",
    label: "Sales growth (TTM)",
    direction: "higher_better",
    format: "percent",
    evidence_id: "E7",
    value: 0.20,
    industry: peerScope("industry"),
    sector: peerScope("sector"),
    summary_scope: "industry",
    summary_percentile: 80,
};

const summaryMetric = {
    key: peerMetric.key,
    label: peerMetric.label,
    value: peerMetric.value,
    format: peerMetric.format,
    direction: peerMetric.direction,
    scope: "industry",
    desirability_percentile: 80,
    evidence_id: "E7",
};

const risk = {
    id: "fcf_decline",
    severity: "high",
    title: "Negative free cash flow",
    message: "TTM free cash flow is negative.",
    metric: "fcf",
    current: -10_000_000,
    previous: 20_000_000,
    evidence_metric: "fcf",
    evidence_id: "E30",
};

function decisionFixture(ticker: string, kind: "complete" | "sparse" | "outside" | "negative") {
    const sparse = kind === "sparse";
    const outside = kind === "outside";
    const negative = kind === "negative";
    const available = !sparse && !negative;
    const reasons = sparse
        ? ["Free cash flow is unavailable.", "Cash and short-term investments are unavailable."]
        : negative
            ? ["Free cash flow must be positive for this DCF."]
            : [];
    const valuation = {
        available,
        unavailable_reasons: reasons,
        inputs: {
            fcf: sparse ? null : negative ? -10_000_000 : 70_000_000,
            cash: sparse ? null : 200_000_000,
            debt: sparse ? null : 50_000_000,
            shares: sparse ? null : 100_000_000,
            financial_statement_date: "2025-12-31",
        },
        current_price: 100,
        scenario_source: "default",
        scenarios: scenarioInputs.map((assumptions, index) => ({
            scenario: assumptions.scenario,
            assumptions,
            available,
            ...(available ? { intrinsic_value_per_share: 80 + index * 25, upside_downside: -0.2 + index * 0.25 } : { reasons }),
        })),
        position: {
            status: available ? "between_bear_base" : "unavailable",
            text: available ? "Price is between the Bear- and Base-case intrinsic values." : `Valuation unavailable: ${reasons.join(" ")}`,
        },
        sensitivity: {
            growth_values: [0, 0.05, 0.10, 0.15, 0.20],
            wacc_values: [0.07, 0.08, 0.09, 0.10, 0.11],
            terminal_growth: 0.025,
            values: Array.from({ length: 5 }, (_, row) => Array.from({ length: 5 }, (_, column) => available ? 80 + row * 5 - column * 3 : null)),
            cell_reasons: Array.from({ length: 5 }, () => Array.from({ length: 5 }, () => available ? null : reasons[0])),
        },
        formula: { forecast_years: 5, cash_treatment: "added", debt_treatment: "deducted", terminal_value: "formula" },
    };
    const warnings = negative ? [risk] : [];
    const missing = [
        ...reasons,
        ...(outside ? ["Ticker is outside the latest published Screener universe."] : []),
    ];
    return {
        metadata: {
            ticker, company_name: `${kind} fixture`, industry: "Software", sector: "Technology",
            price_date: "2026-01-02", screener_date: "2025-12-31", screener_published_at: "2026-01-01",
            financial_statement_date: "2025-12-31", factor_date: "2025-12-31", factor_published_at: "2026-01-01",
        },
        summary: {
            valuation_position: valuation.position,
            strongest_peer_metrics: outside ? [] : [summaryMetric],
            weakest_peer_metrics: outside ? [] : [summaryMetric],
            fundamental_warnings: warnings,
            coverage: {
                quarterly_statements: sparse ? 2 : 8,
                peer_metrics_available: outside ? 0 : 1,
                peer_metrics_total: 20,
                published_factor_count: 1,
                missing_data_reasons: missing,
                data_quality_notes: sparse ? [{ code: "insufficient_quarterly_history", message: "Eight quarterly statements are required; 2 are available." }] : [],
            },
        },
        valuation,
        peer_comparison: {
            ticker_in_screener: !outside,
            industry: "Software", sector: "Technology", industry_member_count: 12, sector_member_count: 40,
            metrics: outside ? [{ ...peerMetric, value: null, industry: peerScope("industry", false), sector: peerScope("sector", false), summary_scope: null, summary_percentile: null }] : [peerMetric],
            strongest: outside ? [] : [summaryMetric], weakest: outside ? [] : [summaryMetric],
            available_metric_count: outside ? 0 : 1, total_metric_count: 20,
        },
        risks: {
            warnings,
            data_quality_notes: sparse ? [{ code: "insufficient_quarterly_history", message: "Eight quarterly statements are required; 2 are available." }] : [],
            high_count: negative ? 1 : 0,
            warning_count: 0,
        },
        evidence: [{ id: "E1", kind: "price", label: "Current price", value: 100, source_date: "2026-01-02", available: true }],
    };
}

type MockLifecycle = {
    onDecisionRequest?: () => void;
    beforeStockResponse?: () => Promise<void> | void;
};

async function mockTicker(
    page: Page,
    ticker: string,
    kind: "complete" | "sparse" | "outside" | "negative",
    lifecycle: MockLifecycle = {},
) {
    await page.route("**/api/quant/factors/**/latest", async (route) => route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ ticker, as_of_date: "2025-12-31", published_at: "2026-01-01", version: "v1", factors: { quality: { raw_value: 1, normalized_value: 1 } } }),
    }));
    await page.route("**/api/stocks/**", async (route) => {
        const url = new URL(route.request().url());
        if (url.pathname.endsWith("/decision-support")) {
            lifecycle.onDecisionRequest?.();
            await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(decisionFixture(ticker, kind)) });
            return;
        }
        if (url.pathname.endsWith("/news")) {
            await route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
            return;
        }
        if (url.pathname.endsWith("/earnings-quality")) {
            await route.fulfill({
                status: 200,
                contentType: "application/json",
                body: JSON.stringify({
                    ticker,
                    currency: "USD",
                    methodology: {
                        materiality_base: "income before tax",
                        warning_threshold: 0.1,
                        high_threshold: 0.25,
                        reported_remains_primary: true,
                        structured_flags_are_adjustments: false,
                    },
                    summary: {
                        verdict: "no_material_candidates_on_available_data",
                        evaluated_periods: 0,
                        flagged_periods: 0,
                        data_quality_periods: 0,
                        financial_industry_exemption: false,
                        message: "No filing candidates were found in the available fixture data.",
                    },
                    annual: [],
                    quarterly: [],
                    sec_analysis: {
                        supported: false,
                        cik: null,
                        reason: "Fixture does not include SEC documents.",
                        supported_forms: [],
                        unsupported_forms: [],
                    },
                }),
            });
            return;
        }
        if (url.pathname.endsWith("/events-expectations")) {
            await route.fulfill({
                status: 200,
                contentType: "application/json",
                body: JSON.stringify({
                    ticker,
                    source: "fixture",
                    as_of: "2026-01-02T00:00:00",
                    available: true,
                    next_event: {
                        id: "earnings-2026-03-31",
                        kind: "earnings",
                        status: "upcoming",
                        title: "Upcoming earnings",
                        event_date: "2026-04-21",
                        period_end: "2026-03-31",
                        timing: "AfterMarket",
                        eps_estimate: 1.5,
                    },
                    upcoming_events: [],
                    recent_earnings: [],
                    expectations: [{
                        period: "+1q",
                        label: "Next quarter",
                        period_end: "2026-06-30",
                        eps_average: 1.5,
                        eps_growth: 0.12,
                        revenue_average: 1_000_000_000,
                        revenue_growth: 0.1,
                        eps_analyst_count: 12,
                        revenue_analyst_count: 10,
                        eps_revisions_up_30d: 4,
                        eps_revisions_down_30d: 1,
                        eps_trend_current: 1.5,
                        eps_trend_30d: 1.45,
                    }],
                    wall_street_target_price: 140.5,
                    dividend_yield: 0.02,
                    annual_dividend_per_share: 1.2,
                    data_quality_notes: [],
                }),
            });
            return;
        }
        await lifecycle.beforeStockResponse?.();
        await route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
                profile: { ticker, name: `${kind} fixture`, exchange: "US", sector: "Technology", industry: "Software", description: "Fixture company.", currency: "USD", last_updated: "2026-01-02" },
                historical_data: [
                    { date: "2025-12-31", open: 98, high: 101, low: 97, close: 99, volume: 1_000_000 },
                    { date: "2026-01-02", open: 99, high: 102, low: 98, close: 100, volume: 1_100_000 },
                ],
                historical_financials: kind === "sparse" ? financialPoints.slice(-2) : financialPoints,
                valuation_metrics: {
                    ttm: { revenue: 400_000_000, gross_profit: 200_000_000, net_income: 80_000_000, free_cash_flow: kind === "negative" ? -10_000_000 : 70_000_000, roe: 0.2 },
                    balance_sheet_latest: { total_assets: 1_000_000_000, total_liabilities: 300_000_000, total_stockholder_equity: 700_000_000, shares_outstanding: 100_000_000 },
                    valuation: { dcf_intrinsic_value_per_share: 100, current_price: 100, margin_of_safety: 0, assumptions: { fcf_growth_rate_5yr: 0.1, wacc: 0.09, perpetual_growth: 0.025 } },
                    factor_scores: { value: 0, quality: 0, growth: 0, health: 0, momentum: 0 },
                },
            }),
        });
    });
}

test("refreshes decision support after a cold stock read-through completes", async ({ page }) => {
    let stockResponseReady = false;
    const decisionPhases: string[] = [];
    await mockTicker(page, "COLD.US", "complete", {
        onDecisionRequest: () => decisionPhases.push(stockResponseReady ? "after" : "before"),
        beforeStockResponse: async () => {
            await new Promise((resolve) => setTimeout(resolve, 100));
            stockResponseReady = true;
        },
    });

    await page.goto("/?ticker=COLD.US");
    await expect.poll(() => decisionPhases).toContain("before");
    await expect.poll(() => decisionPhases).toContain("after");
});

for (const fixture of [
    { ticker: "COMPLETE.US", kind: "complete" as const },
    { ticker: "SPARSE.US", kind: "sparse" as const },
    { ticker: "OUTSIDE.US", kind: "outside" as const },
    { ticker: "NEGFCF.US", kind: "negative" as const },
]) {
    test(`renders the ${fixture.kind} decision fixture without blocking the evidence page`, async ({ page }) => {
        await mockTicker(page, fixture.ticker, fixture.kind);
        await page.goto(`/?ticker=${fixture.ticker}`);
        await expect(page.getByRole("heading", { name: "Decision Cockpit" })).toBeVisible();
        await expect(page.getByRole("heading", { name: `${fixture.kind} fixture`, exact: true })).toBeVisible();
        await expect(page.getByRole("heading", { name: "Price & volume" })).toBeVisible();

        if (fixture.kind === "complete") {
            await expect(page.getByText("Price is between the Bear- and Base-case intrinsic values.")).toBeVisible();
            await expect(page.getByRole("heading", { name: "What could move the stock next" })).toBeVisible();
            await page.getByRole("button", { name: "Evidence Brief" }).click();
            await expect(page.getByText("Forward consensus")).toBeVisible();
            await expect(page.getByRole("button", { name: "Unlock personal workspace" }).last()).toBeVisible();
        } else if (fixture.kind === "sparse") {
            await expect(page.getByText("2/8")).toBeVisible();
            await expect(page.locator("li").filter({ hasText: "Free cash flow is unavailable." })).toBeVisible();
        } else if (fixture.kind === "outside") {
            await expect(page.getByText("Ticker is outside the latest published Screener universe.")).toBeVisible();
        } else {
            await page.getByRole("button", { name: "Risks" }).click();
            await expect(page.getByText("Negative free cash flow", { exact: true })).toBeVisible();
            await page.getByRole("button", { name: "Show evidence" }).click();
            await expect(page.getByLabel("Financial evidence metric")).toHaveValue("free_cash_flow");
            await expect(page.getByLabel("Financial evidence", { exact: true }).getByRole("button", { name: "Quarterly" })).toHaveAttribute("aria-pressed", "true");
        }
    });
}
