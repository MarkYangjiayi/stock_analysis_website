"use client";

import React, { useMemo, useState } from "react";
import { API_BASE_URL } from "@/lib/api";

interface ResearchResult {
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

interface BacktestResult {
    id: number;
    status: string;
    metrics: Record<string, number>;
    diagnostics: Record<string, unknown>;
}

const isoDate = (date: Date) => {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
};

export default function ResearchPage() {
    const today = useMemo(() => new Date(), []);
    const oneYearAgo = useMemo(() => new Date(today.getFullYear() - 1, today.getMonth(), today.getDate()), [today]);
    const [startDate, setStartDate] = useState(isoDate(oneYearAgo));
    const [endDate, setEndDate] = useState(isoDate(today));
    const [factorName, setFactorName] = useState("composite");
    const [adminKey, setAdminKey] = useState("");
    const [research, setResearch] = useState<ResearchResult | null>(null);
    const [backtest, setBacktest] = useState<BacktestResult | null>(null);
    const [loading, setLoading] = useState<"research" | "backtest" | null>(null);
    const [error, setError] = useState("");

    const requestJson = async <T,>(path: string, body: Record<string, unknown>, requiresAdmin = false): Promise<T> => {
        const response = await fetch(`${API_BASE_URL}${path}`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                ...(requiresAdmin && adminKey ? { "X-API-Key": adminKey } : {}),
            },
            body: JSON.stringify(body),
        });
        if (!response.ok) {
            const payload = await response.json().catch(() => ({})) as { detail?: string };
            throw new Error(payload.detail || `Request failed with ${response.status}`);
        }
        return response.json() as Promise<T>;
    };

    const runResearch = async () => {
        setLoading("research");
        setError("");
        try {
            setResearch(await requestJson<ResearchResult>("/api/quant/research", {
                start_date: startDate,
                end_date: endDate,
                factor_name: factorName,
                factor_version: "lfq-v1",
                horizon_days: 21,
                quantiles: 5,
            }));
        } catch (caught: unknown) {
            setError(caught instanceof Error ? caught.message : "Factor research failed");
        } finally {
            setLoading(null);
        }
    };

    const runBacktest = async () => {
        setLoading("backtest");
        setError("");
        try {
            setBacktest(await requestJson<BacktestResult>("/api/quant/backtests", {
                name: "Low Frequency Multi-Factor",
                start_date: startDate,
                end_date: endDate,
                factor_name: factorName,
                factor_version: "lfq-v1",
                universe: "SP500_RUSSELL2000",
                benchmark: "SPY.US",
                rebalance_frequency: "monthly",
                signal_lag_days: 1,
                top_n: 30,
                max_position_weight: 0.05,
                max_sector_weight: 0.30,
                transaction_cost_bps: 5,
                slippage_bps: 5,
                require_point_in_time_universe: true,
                missing_price_policy: "fail",
            }, true));
        } catch (caught: unknown) {
            setError(caught instanceof Error ? caught.message : "Backtest failed");
        } finally {
            setLoading(null);
        }
    };

    const metric = (value: number | undefined, percent = false) => {
        if (value == null || Number.isNaN(value)) return "—";
        return percent ? `${(value * 100).toFixed(2)}%` : value.toFixed(3);
    };

    return (
        <div className="h-full overflow-y-auto bg-slate-50 dark:bg-[#0E1117] p-6 md:p-8">
            <div className="max-w-7xl mx-auto space-y-6 pb-12">
                <div>
                    <h1 className="text-3xl font-black text-slate-900 dark:text-white">Point-in-Time Factor Lab</h1>
                    <p className="text-slate-500 dark:text-gray-400 mt-2">Cross-sectional validation and cost-aware, lagged portfolio backtests.</p>
                </div>

                <section className="grid md:grid-cols-5 gap-4 bg-white dark:bg-[#191D26] border border-gray-200 dark:border-gray-800 rounded-2xl p-5">
                    <label className="text-sm text-slate-500">Start<input className="mt-2 w-full input-field bg-slate-100 dark:bg-[#11151d] rounded-lg p-2" type="date" value={startDate} onChange={event => setStartDate(event.target.value)} /></label>
                    <label className="text-sm text-slate-500">End<input className="mt-2 w-full bg-slate-100 dark:bg-[#11151d] rounded-lg p-2" type="date" value={endDate} onChange={event => setEndDate(event.target.value)} /></label>
                    <label className="text-sm text-slate-500">Factor<select className="mt-2 w-full bg-slate-100 dark:bg-[#11151d] rounded-lg p-2" value={factorName} onChange={event => setFactorName(event.target.value)}><option value="composite">Composite</option><option value="value">Value</option><option value="quality">Quality</option><option value="growth">Growth</option><option value="momentum">Momentum</option><option value="low_volatility">Low Volatility</option></select></label>
                    <label className="text-sm text-slate-500">Admin key<input className="mt-2 w-full bg-slate-100 dark:bg-[#11151d] rounded-lg p-2" type="password" value={adminKey} onChange={event => setAdminKey(event.target.value)} placeholder="Required for backtest" /></label>
                    <div className="flex gap-2 items-end"><button onClick={runResearch} disabled={loading !== null} className="flex-1 bg-emerald-500 text-white rounded-lg p-2 font-bold disabled:opacity-50">Research</button><button onClick={runBacktest} disabled={loading !== null} className="flex-1 bg-indigo-500 text-white rounded-lg p-2 font-bold disabled:opacity-50">Backtest</button></div>
                </section>

                {error && <div className="bg-red-500/10 border border-red-500/30 text-red-500 rounded-xl p-4">{error}</div>}

                {research && <section className="space-y-4"><h2 className="text-xl font-bold">Factor validation</h2><div className="grid grid-cols-2 md:grid-cols-6 gap-3">{[
                    ["Rank IC", metric(research.mean_rank_ic)], ["IC IR", metric(research.ic_information_ratio)], ["Positive IC", metric(research.positive_ic_rate, true)], ["Long–short", metric(research.long_short_spread, true)], ["Monotonicity", metric(research.monotonicity)], ["Turnover", metric(research.top_quantile_turnover, true)]
                ].map(([label, value]) => <div key={label} className="bg-white dark:bg-[#191D26] rounded-xl border border-gray-200 dark:border-gray-800 p-4"><div className="text-xs text-slate-500">{label}</div><div className="text-xl font-mono font-bold mt-1">{value}</div></div>)}</div><div className="grid grid-cols-5 gap-2">{Object.entries(research.quantile_returns).map(([quantile, value]) => <div key={quantile} className="bg-emerald-500/10 border border-emerald-500/20 rounded-xl p-4 text-center"><div className="text-xs text-slate-500">Q{quantile}</div><div className="font-mono font-bold mt-1">{metric(value, true)}</div></div>)}</div></section>}

                {backtest && <section className="space-y-4"><h2 className="text-xl font-bold">Backtest #{backtest.id} · {backtest.status}</h2><div className="grid grid-cols-2 md:grid-cols-5 gap-3">{Object.entries(backtest.metrics).map(([label, value]) => <div key={label} className="bg-white dark:bg-[#191D26] rounded-xl border border-gray-200 dark:border-gray-800 p-4"><div className="text-xs text-slate-500 break-all">{label}</div><div className="text-lg font-mono font-bold mt-1">{metric(value, label.includes("return") || label.includes("drawdown") || label.includes("volatility") || label.includes("error"))}</div></div>)}</div></section>}
            </div>
        </div>
    );
}
