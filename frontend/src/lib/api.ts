export const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

export class ApiError extends Error {
    status: number;
    detail?: string;

    constructor(message: string, status: number, detail?: string) {
        super(message);
        this.name = "ApiError";
        this.status = status;
        this.detail = detail;
    }
}

const parseError = async (response: Response) => {
    const payload = await response.json().catch(() => ({})) as { detail?: string; message?: string };
    return payload.detail || payload.message || `Request failed with status ${response.status}`;
};

export async function apiRequest<T>(
    path: string,
    init: RequestInit = {},
    timeoutMs = 30_000,
): Promise<T> {
    const controller = new AbortController();
    if (init.signal?.aborted) controller.abort();
    let timedOut = false;
    const timeout = globalThis.setTimeout(() => {
        timedOut = true;
        controller.abort();
    }, timeoutMs);
    const onAbort = () => controller.abort();
    init.signal?.addEventListener("abort", onAbort, { once: true });

    try {
        const headers = new Headers(init.headers);
        if (!headers.has("Accept")) headers.set("Accept", "application/json");
        const response = await fetch(`${API_BASE_URL}${path}`, {
            ...init,
            headers,
            signal: controller.signal,
        });
        if (!response.ok) {
            const message = await parseError(response);
            throw new ApiError(message, response.status, message);
        }
        return response.json() as Promise<T>;
    } catch (error) {
        if (timedOut) throw new ApiError(`Request timed out after ${Math.round(timeoutMs / 1000)} seconds`, 408);
        throw error;
    } finally {
        globalThis.clearTimeout(timeout);
        init.signal?.removeEventListener("abort", onAbort);
    }
}

export interface StockProfile {
    ticker: string;
    name: string | null;
    exchange: string | null;
    sector: string | null;
    industry: string | null;
    description: string | null;
    currency: string | null;
    last_updated: string | null;
}

export interface HistoricalDataPoint {
    date: string;
    open: number | null;
    high: number | null;
    low: number | null;
    close: number | null;
    volume: number | null;
    MA20?: number | null;
    MA50?: number | null;
    RSI?: number | null;
    MACD?: number | null;
    MACD_Signal?: number | null;
    MACD_Hist?: number | null;
}

export interface ValuationMetrics {
    ttm: {
        revenue: number;
        gross_profit: number;
        net_income: number;
        free_cash_flow: number;
        roe: number;
    };
    balance_sheet_latest: {
        total_assets: number;
        total_liabilities: number;
        total_stockholder_equity: number;
        shares_outstanding: number;
    };
    valuation: {
        dcf_intrinsic_value_per_share: number;
        current_price: number;
        margin_of_safety: number;
        assumptions: {
            fcf_growth_rate_5yr: number;
            wacc: number;
            perpetual_growth: number;
        };
    };
    data_quality_warnings?: Array<{
        code: string;
        message: string;
        [key: string]: unknown;
    }>;
}

export interface HistoricalFinancialPoint {
    date: string;
    revenue: number;
    net_income: number;
    gross_margin: number;
    free_cash_flow?: number | null;
    operating_margin?: number | null;
    cash_and_short_term_investments?: number | null;
    total_debt?: number | null;
    stockholder_equity?: number | null;
    debt_to_equity?: number | null;
    shares_outstanding?: number | null;
    shares_reported?: number | null;
    share_adjustment_factor?: number | null;
    price?: number | null;
}

export interface StockDataResponse {
    profile: StockProfile;
    historical_data: HistoricalDataPoint[];
    historical_financials: HistoricalFinancialPoint[];
    valuation_metrics?: ValuationMetrics | null;
}

export type ValuationScenarioName = "bear" | "base" | "bull";

export interface DecisionValuationScenarioInput {
    scenario: ValuationScenarioName;
    fcf_growth_rate: number;
    wacc: number;
    perpetual_growth: number;
}

export interface DecisionValuationScenarioResult {
    scenario: ValuationScenarioName;
    assumptions: DecisionValuationScenarioInput;
    available: boolean;
    reasons?: string[];
    intrinsic_value_per_share?: number;
    enterprise_value?: number;
    equity_value?: number;
    projected_fcf?: number[];
    present_value_explicit_fcf?: number;
    present_value_terminal?: number;
    upside_downside?: number | null;
}

export interface DecisionValuation {
    available: boolean;
    unavailable_reasons: string[];
    inputs: {
        fcf: number | null;
        cash: number | null;
        debt: number | null;
        shares: number | null;
        financial_statement_date: string | null;
    };
    current_price: number | null;
    scenario_source: "default" | "saved" | "request";
    scenarios: DecisionValuationScenarioResult[];
    position: { status: string; text: string };
    sensitivity: {
        growth_values: number[];
        wacc_values: number[];
        terminal_growth: number;
        values: Array<Array<number | null>>;
        cell_reasons: Array<Array<string | null>>;
    };
    formula: {
        forecast_years: number;
        cash_treatment: string;
        debt_treatment: string;
        terminal_value: string;
    };
}

export interface PeerScopeResult {
    scope: "industry" | "sector";
    minimum_observations: number;
    observation_count: number;
    available: boolean;
    raw_percentile: number | null;
    desirability_percentile: number | null;
    reason: string | null;
}

export interface PeerMetric {
    key: string;
    label: string;
    direction: "higher_better" | "lower_better";
    format: "percent" | "multiple" | "ratio";
    evidence_id: string;
    value: number | null;
    industry: PeerScopeResult;
    sector: PeerScopeResult;
    summary_scope: "industry" | "sector" | null;
    summary_percentile: number | null;
}

export interface DecisionWarning {
    id: string;
    severity: "warning" | "high";
    title: string;
    message: string;
    metric: string;
    current: number | null;
    previous: number | null;
    evidence_metric: "revenue" | "gross_margin" | "operating_margin" | "fcf" | "cash" | "debt" | "debt_to_equity" | "shares";
    evidence_id: string;
}

export interface DecisionSummaryMetric {
    key: string;
    label: string;
    value: number | null;
    format: PeerMetric["format"];
    direction: PeerMetric["direction"];
    scope: "industry" | "sector";
    desirability_percentile: number;
    evidence_id: string;
}

export interface DecisionEvidenceItem {
    id: string;
    kind: string;
    label: string;
    value: unknown;
    source_date: string | null;
    available: boolean;
}

export type EarningsQualityPeriodType = "annual" | "quarterly";

export interface EarningsQualityFlag {
    category: string;
    label: string;
    amount: number;
    materiality_ratio: number;
    severity: "warning" | "high";
    source: string;
    source_field: string;
    detail: string;
    treatment: "flag_only" | "recurring_flag_only";
    recurring_adjustment: boolean;
    reported_amount?: number;
    comparison_amount?: number;
}

export interface EarningsQualitySourceSnapshot {
    source_id: string;
    accession: string;
    form: string;
    document_name: string;
    source_url: string;
    html_snapshot_id: number;
    text_snapshot_id: number;
    html_checksum: string;
    text_checksum: string;
}

export interface EarningsQualityAnalysis {
    id: number;
    ticker: string;
    period_end: string;
    period_type: EarningsQualityPeriodType;
    status: "queued" | "running" | "completed" | "failed" | "waiting_for_filing";
    stage: string;
    model: string;
    prompt_version: string;
    source_accession: string | null;
    source_snapshots: EarningsQualitySourceSnapshot[];
    result: {
        verification_status: "verified" | "flag_only";
        reported_net_income: number;
        normalized_net_income: number | null;
        adjusted_eps: number | null;
        company_adjusted: {
            label: string;
            adjusted_net_income: number | null;
            adjusted_diluted_eps: number | null;
        } | null;
        adjustments: Array<{
            category: string;
            label: string;
            pretax_earnings_effect: number | null;
            tax_effect: number | null;
            earnings_effect_after_tax: number;
            include_in_normalized: boolean;
            recurring: boolean;
            cash_effect: "cash" | "non_cash" | "mixed" | "unknown";
            citation: {
                source_id: string;
                accession: string;
                document_name: string;
                section: string;
                excerpt: string;
                source_amount: number;
                source_unit_scale: number;
                period_end: string;
                period_scope: "quarter" | "annual";
            };
        }>;
        unquantified_candidates?: Array<{
            label: string;
            model_category: string;
            policy_category: string;
            failure_codes: string[];
        }>;
        notes: string[];
    } | null;
    validation_report: {
        verified: boolean;
        eps_verified?: boolean;
        checks: Array<Record<string, unknown>>;
        failures: Array<{ code: string; message: string; adjustment_index?: number }>;
        eps_failures?: Array<{ code: string; message: string }>;
        rejected_adjustments?: Array<{
            adjustment_index: number;
            label: string;
            model_category: string;
            policy_category: string;
            failure_codes?: string[];
        }>;
        sign_convention: string;
        gains_and_charges_treated_symmetrically: boolean;
    } | null;
    error_message: string | null;
    retryable: boolean;
    created_at: string;
    started_at: string | null;
    finished_at: string | null;
}

export interface EarningsQualityPeriod {
    period_end: string;
    period_type: EarningsQualityPeriodType;
    reported: {
        revenue: number | null;
        net_income: number | null;
        income_before_tax: number | null;
        net_income_from_continuing_operations: number | null;
        income_tax_expense: number | null;
        non_recurring: number | null;
        extraordinary_items: number | null;
        discontinued_operations: number | null;
        non_operating_income_net_other: number | null;
    };
    materiality_base: number | null;
    thresholds: { warning: number; high: number };
    flags: EarningsQualityFlag[];
    data_quality_warnings: Array<{ code: string; message: string }>;
    assessment: "unavailable" | "material_candidates" | "data_quality_warning" | "no_material_candidates";
    statement_fingerprint: string;
    analysis: EarningsQualityAnalysis | null;
    verified_normalized: {
        net_income: number | null;
        adjusted_eps: number | null;
    } | null;
}

export interface EarningsQualityResponse {
    ticker: string;
    currency: string | null;
    methodology: {
        materiality_base: string;
        warning_threshold: number;
        high_threshold: number;
        reported_remains_primary: boolean;
        structured_flags_are_adjustments: boolean;
    };
    summary: {
        verdict: "unavailable" | "flags_present" | "data_quality_warning" | "no_material_candidates_on_available_data";
        evaluated_periods: number;
        flagged_periods: number;
        data_quality_periods: number;
        financial_industry_exemption: boolean;
        message: string;
    };
    annual: EarningsQualityPeriod[];
    quarterly: EarningsQualityPeriod[];
    sec_analysis: {
        supported: boolean;
        cik: string | null;
        reason: string | null;
        supported_forms: string[];
        unsupported_forms: string[];
    };
}

export interface DecisionSupportResponse {
    metadata: {
        ticker: string;
        company_name: string | null;
        currency: string | null;
        industry: string | null;
        sector: string | null;
        price_date: string | null;
        screener_date: string | null;
        screener_published_at: string | null;
        financial_statement_date: string | null;
        factor_date: string | null;
        factor_published_at: string | null;
    };
    summary: {
        valuation_position: { status: string; text: string };
        strongest_peer_metrics: DecisionSummaryMetric[];
        weakest_peer_metrics: DecisionSummaryMetric[];
        fundamental_warnings: DecisionWarning[];
        coverage: {
            quarterly_statements: number;
            peer_metrics_available: number;
            peer_metrics_total: number;
            published_factor_count: number;
            missing_data_reasons: string[];
            data_quality_notes: Array<{ code: string; message: string }>;
        };
    };
    valuation: DecisionValuation;
    peer_comparison: {
        ticker_in_screener: boolean;
        industry: string | null;
        sector: string | null;
        industry_member_count: number;
        sector_member_count: number;
        metrics: PeerMetric[];
        strongest: DecisionSummaryMetric[];
        weakest: DecisionSummaryMetric[];
        available_metric_count: number;
        total_metric_count: number;
    };
    risks: {
        warnings: DecisionWarning[];
        data_quality_notes: Array<{ code: string; message: string }>;
        high_count: number;
        warning_count: number;
    };
    earnings_quality?: EarningsQualityResponse;
    evidence: DecisionEvidenceItem[];
}

export interface PublishedFactorValue {
    raw_value: number | null;
    normalized_value: number | null;
    details?: Record<string, unknown> | null;
}

export interface PublishedFactorSnapshot {
    ticker: string;
    as_of_date: string;
    published_at: string;
    version: string;
    factors: Record<string, PublishedFactorValue>;
}

export interface QuantCoverage {
    publications: Record<string, {
        as_of_date: string;
        published_at: string;
    }>;
    factors: {
        min_date: string | null;
        max_date: string | null;
        date_count: number;
        ticker_count: number;
        names: string[];
    };
}

export type MarketUniverse = "SP500" | "RUSSELL2000" | "SP500_RUSSELL2000";
export type MarketPeriod = "3m" | "6m" | "1y";

export interface MarketOverviewResponse {
    meta: {
        universe: MarketUniverse;
        period: MarketPeriod;
        as_of_date: string;
        expected_as_of_date: string;
        published_at: string;
        stale: boolean;
        membership_mode: "point_in_time";
        data_complete: boolean;
        warnings: string[];
    };
    dates: string[];
    benchmark: {
        ticker: string;
        absolute_index: number[];
    };
    sector_trends: Array<{
        ticker: string;
        label: string;
        absolute_index: number[];
        relative_to_spy_index: number[];
    }>;
    rsp_spy_index: number[];
    breadth: {
        pct_above_ma20: Array<number | null>;
        pct_above_ma50: Array<number | null>;
        pct_above_ma200: Array<number | null>;
        net_advances_pct: Array<number | null>;
        new_high_low_pct: Array<number | null>;
        new_high_pct: Array<number | null>;
        new_low_pct: Array<number | null>;
        mcclellan: Array<number | null>;
        dispersion_1d: Array<number | null>;
        dispersion_20d: Array<number | null>;
        member_count: number[];
        price_coverage_pct: Array<number | null>;
    };
}

export interface NewsItem {
    title: string;
    link: string;
    pub_date: string;
    summary: string;
    publisher: string;
}

export interface AnomalyReport {
    ticker: string;
    company_name: string;
    company_description?: string | null;
    market_cap?: number | null;
    date: string;
    quote_timestamp: string;
    price_change: number;
    ai_analysis: string;
    attribution_status: "completed" | "no_news" | "timed_out" | "news_unavailable" | "attribution_unavailable";
    news: NewsItem[];
    top_news_links: string[];
}

export interface AnomalyScan {
    id: number;
    trigger: string;
    status: "queued" | "running" | "completed" | "failed";
    requested_limit: number;
    threshold_pct: number;
    universe_as_of: string | null;
    quote_as_of: string | null;
    results: AnomalyReport[];
    error_message: string | null;
    created_at: string;
    started_at: string | null;
    finished_at: string | null;
}

export interface ScreenerPayload {
    limit: number;
    offset: number;
    sort_by: string;
    sort_desc: boolean;
    as_of_date?: string;
    sector?: string;
    market_cap_min?: number;
    market_cap_max?: number;
    pe_min?: number;
    pe_max?: number;
    rsi_14_min?: number;
    rsi_14_max?: number;
    price_above_ma50?: boolean;
    price_below_ma50?: boolean;
    roe_min?: number;
    debt_to_equity_max?: number;
    fcf_min?: number;
    gross_margin_min?: number;
    sales_growth_5yr_min?: number;
}

export interface ScreenerResult {
    ticker: string;
    name?: string | null;
    sector?: string | null;
    industry?: string | null;
    market_cap?: number | null;
    close?: number | null;
    pe_ratio?: number | null;
    roe?: number | null;
    debt_to_equity?: number | null;
    gross_margin?: number | null;
    sales_growth_5yr?: number | null;
    rsi_14?: number | null;
    date?: string;
    [key: string]: unknown;
}

export interface ScreenerResponse {
    total: number;
    items: ScreenerResult[];
    limit: number;
    offset: number;
    as_of_date: string | null;
}

export interface FactorResearchResult {
    observations: number;
    dates: number;
    mean_rank_ic: number;
    ic_information_ratio: number;
    positive_ic_rate: number;
    long_short_spread: number;
    monotonicity: number;
    top_quantile_turnover: number;
    quantile_returns: Record<string, number>;
}

export interface BacktestSummary {
    id: number;
    status: string;
    metrics: Record<string, number>;
    diagnostics: Record<string, unknown>;
}

export interface EquityCurvePoint {
    date: string;
    equity: number;
    daily_return: number;
}

export interface BacktestDetails extends BacktestSummary {
    name: string;
    config: Record<string, unknown>;
    equity_curve: EquityCurvePoint[];
    attribution: {
        sector_return_contribution?: Record<string, number>;
        [key: string]: unknown;
    };
}

export const fetchStockData = (
    ticker: string,
    interval = "1d",
    financialPeriod = "Yearly",
    signal?: AbortSignal,
) => apiRequest<StockDataResponse>(
    `/api/stocks/${encodeURIComponent(ticker)}?interval=${encodeURIComponent(interval)}&financial_period=${encodeURIComponent(financialPeriod)}`,
    { signal },
    90_000,
);

export const fetchLatestTickerFactors = (ticker: string, signal?: AbortSignal) =>
    apiRequest<PublishedFactorSnapshot>(`/api/quant/factors/${encodeURIComponent(ticker)}/latest`, { signal });

const personalHeaders = (adminKey?: string, json = false) => {
    const headers: Record<string, string> = {};
    if (adminKey) headers["X-API-Key"] = adminKey;
    if (json) headers["Content-Type"] = "application/json";
    return headers;
};

export const fetchDecisionSupport = (
    ticker: string,
    adminKey?: string,
    signal?: AbortSignal,
) => apiRequest<DecisionSupportResponse>(
    `/api/stocks/${encodeURIComponent(ticker)}/decision-support`,
    { headers: personalHeaders(adminKey), signal },
);

export const fetchEarningsQuality = (
    ticker: string,
    signal?: AbortSignal,
) => apiRequest<EarningsQualityResponse>(
    `/api/stocks/${encodeURIComponent(ticker)}/earnings-quality`,
    { signal },
);

export const startEarningsQualityAnalysis = (
    ticker: string,
    periodEnd: string,
    periodType: EarningsQualityPeriodType,
    adminKey: string,
    signal?: AbortSignal,
) => apiRequest<EarningsQualityAnalysis>(
    `/api/personal/stocks/${encodeURIComponent(ticker)}/earnings-quality/analyses`,
    {
        method: "POST",
        headers: personalHeaders(adminKey, true),
        body: JSON.stringify({ period_end: periodEnd, period_type: periodType }),
        signal,
    },
);

export const fetchEarningsQualityAnalysis = (
    ticker: string,
    analysisId: number,
    adminKey?: string,
    signal?: AbortSignal,
) => apiRequest<EarningsQualityAnalysis>(
    `/api/stocks/${encodeURIComponent(ticker)}/earnings-quality/analyses/${analysisId}`,
    { headers: personalHeaders(adminKey), signal },
);

export const calculateDecisionValuation = (
    ticker: string,
    scenarios: DecisionValuationScenarioInput[],
    signal?: AbortSignal,
) => apiRequest<DecisionValuation>(
    `/api/stocks/${encodeURIComponent(ticker)}/valuation/calculate`,
    {
        method: "POST",
        headers: personalHeaders(undefined, true),
        body: JSON.stringify({ scenarios }),
        signal,
    },
);

export const fetchPersonalWatchlist = (adminKey: string, signal?: AbortSignal) =>
    apiRequest<{ tickers: string[] }>("/api/personal/watchlist", {
        headers: personalHeaders(adminKey),
        signal,
    });

export const replacePersonalWatchlist = (
    tickers: string[],
    adminKey: string,
    signal?: AbortSignal,
) => apiRequest<{ tickers: string[] }>("/api/personal/watchlist", {
    method: "PUT",
    headers: personalHeaders(adminKey, true),
    body: JSON.stringify({ tickers }),
    signal,
});

export const importPersonalWatchlist = (
    tickers: string[],
    adminKey: string,
    signal?: AbortSignal,
) => apiRequest<{ tickers: string[]; imported: boolean }>("/api/personal/watchlist/import", {
    method: "POST",
    headers: personalHeaders(adminKey, true),
    body: JSON.stringify({ tickers }),
    signal,
});

export const savePersonalValuationScenarios = (
    ticker: string,
    scenarios: DecisionValuationScenarioInput[],
    adminKey: string,
) => apiRequest<{ ticker: string; is_saved: boolean; scenarios: DecisionValuationScenarioInput[] }>(
    `/api/personal/stocks/${encodeURIComponent(ticker)}/valuation-scenarios`,
    {
        method: "PUT",
        headers: personalHeaders(adminKey, true),
        body: JSON.stringify({ scenarios }),
    },
);

export const resetPersonalValuationScenarios = (
    ticker: string,
    adminKey: string,
) => apiRequest<{ ticker: string; is_saved: boolean; scenarios: DecisionValuationScenarioInput[] }>(
    `/api/personal/stocks/${encodeURIComponent(ticker)}/valuation-scenarios`,
    { method: "DELETE", headers: personalHeaders(adminKey) },
);

export const fetchQuantCoverage = (signal?: AbortSignal) =>
    apiRequest<QuantCoverage>("/api/quant/coverage", { signal });

export const fetchMarketOverview = (
    universe: MarketUniverse,
    period: MarketPeriod,
    signal?: AbortSignal,
) => apiRequest<MarketOverviewResponse>(
    `/api/v1/market-overview?universe=${encodeURIComponent(universe)}&period=${encodeURIComponent(period)}`,
    { signal },
    90_000,
);

export const fetchStockNews = (ticker: string, signal?: AbortSignal) =>
    apiRequest<NewsItem[]>(`/api/stocks/${encodeURIComponent(ticker)}/news`, { signal });

export const fetchLatestAnomalyScan = (signal?: AbortSignal) =>
    apiRequest<AnomalyScan | null>("/api/market/anomalies", { signal });

export const startAnomalyScan = (signal?: AbortSignal) =>
    apiRequest<AnomalyScan>("/api/market/anomalies/scans", {
        method: "POST",
        signal,
    });

export const fetchAnomalyScan = (scanId: number, signal?: AbortSignal) =>
    apiRequest<AnomalyScan>(`/api/market/anomalies/scans/${scanId}`, { signal });

export const fetchScreener = (payload: ScreenerPayload, signal?: AbortSignal) =>
    apiRequest<ScreenerResponse>("/api/stocks/screener", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        signal,
    });

export const runFactorResearch = (
    payload: Record<string, unknown>,
    signal?: AbortSignal,
) => apiRequest<FactorResearchResult>("/api/quant/research", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
}, 120_000);

export const createBacktest = (
    payload: Record<string, unknown>,
    adminKey: string,
    signal?: AbortSignal,
) => apiRequest<BacktestSummary>("/api/quant/backtests", {
    method: "POST",
    headers: {
        "Content-Type": "application/json",
        "X-API-Key": adminKey,
    },
    body: JSON.stringify(payload),
    signal,
}, 180_000);

export const fetchBacktest = (runId: number, adminKey: string, signal?: AbortSignal) =>
    apiRequest<BacktestDetails>(`/api/quant/backtests/${runId}`, {
        headers: personalHeaders(adminKey),
        signal,
    });
