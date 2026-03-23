import { create } from 'zustand';
import { AnomalyReport } from '@/lib/api';

// --- Screener Types ---
export interface ScreenerFilters {
    sector: string;
    market_cap: string;
    pe: string;
    rsi: string;
    price_ma50: string;
    roe: string;
    debt_to_equity: string;
    fcf: string;
    gross_margin: string;
    sales_growth_5yr: string;
    sort_by: string;
    sort_desc: string;
}

export interface ScreenerResult {
    ticker: string;
    name?: string;
    sector?: string;
    market_cap?: number;
    close?: number;
    pe_ratio?: number;
    pb_ratio?: number;
    rsi_14?: number;
    fcf?: number;
    dividend_yield?: number;
    roe?: number;
    debt_to_equity?: number;
    gross_margin?: number;
    sales_growth_5yr?: number;
    [key: string]: unknown; // Allow other properties
}

export interface ScreenerState {
    filters: ScreenerFilters;
    results: ScreenerResult[];
    totalCount: number;
    page: number;
    setScreenerState: (partial: Partial<Omit<ScreenerState, 'setScreenerState'>>) => void;
}

// --- Anomalies Types ---
export interface AnomaliesState {
    data: AnomalyReport[];
    lastFetchTime: number | null;
    setAnomaliesData: (data: AnomalyReport[]) => void;
}

// --- Combined Store ---
export const useAppStore = create<ScreenerState & AnomaliesState>((set) => ({
    // Screener Initial State
    filters: {
        sector: '',
        market_cap: '',
        pe: '',
        rsi: '',
        price_ma50: '',
        roe: '',
        debt_to_equity: '',
        fcf: '',
        gross_margin: '',
        sales_growth_5yr: '',
        sort_by: 'market_cap',
        sort_desc: 'desc',
    },
    results: [],
    totalCount: 0,
    page: 1,
    setScreenerState: (partial) => set((state) => ({ ...state, ...partial })),

    // Anomalies Initial State
    data: [],
    lastFetchTime: null,
    setAnomaliesData: (data) => set({ data, lastFetchTime: Date.now() }),
}));
