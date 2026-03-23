import axios from 'axios';

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';

export interface StockProfile {
    ticker: string;
    name: string;
    exchange: string;
    sector: string;
    industry: string;
    description: string;
    currency: string;
    last_updated: string;
}

export interface HistoricalDataPoint {
    date: string;
    open: number;
    high: number;
    low: number;
    close: number;
    volume: number;
    MA20?: number;
    MA50?: number;
    RSI?: number;
    MACD?: number;
    MACD_Signal?: number;
    MACD_Hist?: number;
}

export interface ValuationMultiples {
    pe_ratio: number | null;
    pb_ratio: number | null;
    ps_ratio: number | null;
    pfcf_ratio: number | null;
    ev_ebitda: number | null;
    ev_revenue: number | null;
    market_cap: number | null;
    enterprise_value: number | null;
    net_debt: number | null;
    eps: number | null;
}

export interface ProfitabilityMetrics {
    gross_margin: number | null;
    operating_margin: number | null;
    net_margin: number | null;
    roe: number | null;
    roa: number | null;
    fcf_conversion: number | null;
    ttm_ebitda: number | null;
}

export interface FinancialHealthMetrics {
    current_ratio: number | null;
    debt_to_equity: number | null;
    net_debt_ebitda: number | null;
    interest_coverage: number | null;
    net_debt: number | null;
}

export interface GrowthMetrics {
    revenue_yoy: number | null;
    revenue_cagr_3yr: number | null;
    eps_yoy: number | null;
    eps_cagr_3yr: number | null;
    fcf_yoy: number | null;
}

export interface DCFInputs {
    ttm_fcf: number;
    cash: number;
    total_debt: number;
    shares_outstanding: number;
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
        high_52w: number | null;
        low_52w: number | null;
        pct_from_52w_high: number | null;
        pct_from_52w_low: number | null;
        assumptions: {
            fcf_growth_rate_5yr: number;
            wacc: number;
            perpetual_growth: number;
        };
    };
    dcf_inputs?: DCFInputs;
    multiples?: ValuationMultiples;
    profitability?: ProfitabilityMetrics;
    financial_health?: FinancialHealthMetrics;
    growth_metrics?: GrowthMetrics;
    factor_scores: {
        value: number;
        quality: number;
        growth: number;
        health: number;
        momentum: number;
    };
}

export interface HistoricalFinancialPoint {
    date: string;
    revenue: number;
    net_income: number;
    gross_margin: number;
    eps?: number | null;
    price?: number | null;
}

export interface StockDataResponse {
    profile: StockProfile;
    historical_data: HistoricalDataPoint[];
    historical_financials?: HistoricalFinancialPoint[];
    historical_financials_annual?: HistoricalFinancialPoint[];
    valuation_metrics?: ValuationMetrics;
}

export const fetchStockData = async (ticker: string, interval: string = '1d', financialPeriod: string = 'Yearly'): Promise<StockDataResponse> => {
    const response = await axios.get(`${API_BASE_URL}/api/stocks/${encodeURIComponent(ticker)}?interval=${interval}&financial_period=${financialPeriod}`);
    return response.data;
};

export interface AIReportResponse {
    report: string;
}

export const fetchAIReport = async (ticker: string): Promise<AIReportResponse> => {
    const response = await axios.get(`${API_BASE_URL}/api/stocks/${encodeURIComponent(ticker)}/report`);
    return response.data;
};

export interface BatchFactorScore {
    ticker: string;
    factor_scores: {
        value: number;
        quality: number;
        growth: number;
        health: number;
        momentum: number;
    };
}

export const fetchBatchFactors = async (tickers: string[]): Promise<BatchFactorScore[]> => {
    const response = await axios.post(`${API_BASE_URL}/api/stocks/batch-factors`, {
        tickers
    });
    return response.data;
};

export interface NewsItem {
    title: string;
    link: string;
    pub_date: string;
    summary: string;
    publisher: string;
}

export const fetchStockNews = async (ticker: string): Promise<NewsItem[]> => {
    const response = await axios.get(`${API_BASE_URL}/api/stocks/${encodeURIComponent(ticker)}/news`);
    return response.data;
};

export interface AnomalyReport {
    ticker: string;
    company_name: string;
    date: string;
    price_change: number;
    ai_analysis: string;
    top_news_links: string[];
}

export const fetchMarketAnomalies = async (): Promise<AnomalyReport[]> => {
    const response = await axios.get(`${API_BASE_URL}/api/market/anomalies`);
    return response.data;
};

export interface PeerStock {
    ticker: string;
    name: string;
    market_cap: number | null;
    pe_ratio: number | null;
    pb_ratio: number | null;
    roe: number | null;
    gross_margin: number | null;
    sales_growth_5yr: number | null;
    close: number | null;
    is_current: boolean;
}

export interface PeerComparisonResponse {
    peers: PeerStock[];
    industry_medians: {
        pe_ratio: number | null;
        pb_ratio: number | null;
        roe: number | null;
        gross_margin: number | null;
        sales_growth_5yr: number | null;
    };
    sector: string;
}

export const fetchPeerComparison = async (ticker: string): Promise<PeerComparisonResponse> => {
    const response = await axios.get(`${API_BASE_URL}/api/stocks/${ticker}/peers`);
    return response.data;
};
