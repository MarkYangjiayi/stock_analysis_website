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
}

export interface HistoricalFinancialPoint {
    date: string;
    revenue: number;
    net_income: number;
    gross_margin: number;
    price?: number | null;
}

export interface StockDataResponse {
    profile: StockProfile;
    historical_data: HistoricalDataPoint[];
    historical_financials: HistoricalFinancialPoint[];
    valuation_metrics?: ValuationMetrics | null;
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

export const fetchBacktest = (runId: number, signal?: AbortSignal) =>
    apiRequest<BacktestDetails>(`/api/quant/backtests/${runId}`, { signal });
