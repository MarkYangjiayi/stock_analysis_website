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

export interface ValuationMetrics {
    ttm: {
        revenue: number;
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
    price?: number;
}

export interface StockDataResponse {
    profile: StockProfile;
    historical_data: HistoricalDataPoint[];
    historical_financials?: HistoricalFinancialPoint[];
    valuation_metrics?: ValuationMetrics;
}

export const fetchStockData = async (ticker: string, interval: string = '1d'): Promise<StockDataResponse> => {
    const response = await axios.get(`${API_BASE_URL}/api/stocks/${ticker}?interval=${interval}`);
    return response.data;
};

export interface AIReportResponse {
    report: string;
}

export const fetchAIReport = async (ticker: string): Promise<AIReportResponse> => {
    const response = await axios.get(`${API_BASE_URL}/api/stocks/${ticker}/report`);
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
    const response = await axios.get(`${API_BASE_URL}/api/stocks/${ticker}/news`);
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
