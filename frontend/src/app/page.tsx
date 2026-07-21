"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { useRouter, useSearchParams } from "next/navigation";
import { AlertCircle, ArrowDownRight, ArrowUpRight, Check, Clock3, Plus, Search, ShieldCheck } from "lucide-react";
import { fetchLatestTickerFactors, fetchStockData, PublishedFactorSnapshot, StockDataResponse } from "@/lib/api";
import AIReport from "@/components/AIReport";
import NewsFeed from "@/components/NewsFeed";
import PointInTimeFactorPanel from "@/components/PointInTimeFactorPanel";
import ValuationDashboard from "@/components/ValuationDashboard";
import WatchlistSidebar from "@/components/WatchlistSidebar";

const StockChart = dynamic(() => import("@/components/StockChart"), {
    ssr: false,
    loading: () => <div className="h-[480px] animate-pulse rounded-xl bg-slate-100 dark:bg-slate-800" />,
});
const FinancialTrendChart = dynamic(() => import("@/components/FinancialTrendChart"), {
    ssr: false,
    loading: () => <div className="h-[520px] animate-pulse rounded-2xl bg-slate-100 dark:bg-slate-800" />,
});

const DEFAULT_WATCHLIST = ["AAPL.US", "AMAT.US", "ASTS.US", "UNH.US"];

function AnalysisPage() {
    const searchParams = useSearchParams();
    const router = useRouter();
    const requestedTicker = searchParams.get("ticker")?.trim().toUpperCase() || "";
    const [ticker, setTicker] = useState("");
    const [stockData, setStockData] = useState<StockDataResponse | null>(null);
    const [factorSnapshot, setFactorSnapshot] = useState<PublishedFactorSnapshot | null>(null);
    const [loading, setLoading] = useState(false);
    const [factorLoading, setFactorLoading] = useState(false);
    const [chartLoading, setChartLoading] = useState(false);
    const [error, setError] = useState("");
    const [factorError, setFactorError] = useState("");
    const [chartInterval, setChartInterval] = useState("1d");
    const [financialPeriod, setFinancialPeriod] = useState<"annual" | "ttm" | "quarterly">("annual");
    const [watchlist, setWatchlist] = useState<string[]>([]);
    const stockRequestRef = useRef<AbortController | null>(null);
    const factorRequestRef = useRef<AbortController | null>(null);

    useEffect(() => {
        const stored = window.localStorage.getItem("my_watchlist");
        if (stored) {
            try {
                const parsed = JSON.parse(stored);
                if (Array.isArray(parsed)) {
                    setWatchlist(parsed.filter((value): value is string => typeof value === "string"));
                    return;
                }
            } catch { /* Use defaults below. */ }
        }
        setWatchlist(DEFAULT_WATCHLIST);
        window.localStorage.setItem("my_watchlist", JSON.stringify(DEFAULT_WATCHLIST));
    }, []);

    const saveWatchlist = (next: string[]) => {
        setWatchlist(next);
        window.localStorage.setItem("my_watchlist", JSON.stringify(next));
    };

    const loadStock = useCallback(async (
        symbol: string,
        interval = "1d",
        period: "annual" | "ttm" | "quarterly" = "annual",
        initial = false,
    ) => {
        stockRequestRef.current?.abort();
        const controller = new AbortController();
        stockRequestRef.current = controller;
        if (initial) setLoading(true); else setChartLoading(true);
        setError("");
        try {
            const data = await fetchStockData(symbol, interval, period === "quarterly" ? "Quarterly" : "Yearly", controller.signal);
            if (!controller.signal.aborted) setStockData(data);
        } catch (caught) {
            if (caught instanceof DOMException && caught.name === "AbortError") return;
            if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : "Unable to load this security.");
        } finally {
            if (!controller.signal.aborted) {
                setLoading(false);
                setChartLoading(false);
            }
        }
    }, []);

    const loadFactors = useCallback(async (symbol: string) => {
        factorRequestRef.current?.abort();
        const controller = new AbortController();
        factorRequestRef.current = controller;
        setFactorLoading(true);
        setFactorError("");
        setFactorSnapshot(null);
        try {
            const snapshot = await fetchLatestTickerFactors(symbol, controller.signal);
            if (!controller.signal.aborted) setFactorSnapshot(snapshot);
        } catch (caught) {
            if (caught instanceof DOMException && caught.name === "AbortError") return;
            if (!controller.signal.aborted) setFactorError(caught instanceof Error ? caught.message : "Published factors are unavailable.");
        } finally {
            if (!controller.signal.aborted) setFactorLoading(false);
        }
    }, []);

    useEffect(() => {
        if (!requestedTicker) {
            setTicker("");
            setStockData(null);
            setFactorSnapshot(null);
            setError("");
            return;
        }
        setTicker(requestedTicker);
        setStockData(null);
        setChartInterval("1d");
        setFinancialPeriod("annual");
        void loadStock(requestedTicker, "1d", "annual", true);
        void loadFactors(requestedTicker);
        return () => {
            stockRequestRef.current?.abort();
            factorRequestRef.current?.abort();
        };
    }, [loadFactors, loadStock, requestedTicker]);

    const selectTicker = (symbol: string) => router.push(`/?ticker=${encodeURIComponent(symbol.toUpperCase())}`);

    const handleIntervalChange = async (interval: string) => {
        if (!ticker || interval === chartInterval) return;
        setChartInterval(interval);
        await loadStock(ticker, interval, financialPeriod);
    };

    const handlePeriodChange = async (period: "annual" | "ttm" | "quarterly") => {
        if (!ticker || period === financialPeriod) return;
        const previous = financialPeriod;
        setFinancialPeriod(period);
        if (period === "quarterly" || previous === "quarterly") await loadStock(ticker, chartInterval, period);
    };

    const latest = stockData?.historical_data.filter((point) => point.close != null).at(-1);
    const previous = stockData?.historical_data.filter((point) => point.close != null).at(-2);
    const change = latest?.close != null && previous?.close != null ? latest.close - previous.close : null;
    const changePct = change != null && previous?.close ? change / previous.close : null;
    const isPositive = change != null && change >= 0;

    return (
        <div className="flex h-full w-full overflow-hidden bg-[var(--app-bg)]">
            <div className="hidden h-full md:block">
                <WatchlistSidebar currentTicker={ticker} onSelectTicker={selectTicker} watchlist={watchlist} onAdd={(symbol) => !watchlist.includes(symbol) && saveWatchlist([symbol, ...watchlist])} onRemove={(symbol) => saveWatchlist(watchlist.filter((item) => item !== symbol))} />
            </div>

            <div className="app-page min-w-0 flex-1">
                <div className="page-container">
                    <WatchlistSidebar compact currentTicker={ticker} onSelectTicker={selectTicker} watchlist={watchlist} onAdd={(symbol) => !watchlist.includes(symbol) && saveWatchlist([symbol, ...watchlist])} onRemove={(symbol) => saveWatchlist(watchlist.filter((item) => item !== symbol))} />

                    {!ticker && (
                        <section className="surface-panel flex min-h-[65vh] flex-col items-center justify-center px-6 py-16 text-center">
                            <span className="flex h-14 w-14 items-center justify-center rounded-2xl bg-emerald-50 text-emerald-600 dark:bg-emerald-950/40 dark:text-emerald-300"><Search size={26} /></span>
                            <p className="eyebrow mt-6">Single-security workspace</p>
                            <h1 className="mt-2 text-3xl font-black tracking-[-0.04em] sm:text-4xl">Start with a ticker</h1>
                            <p className="mt-3 max-w-xl text-sm leading-6 text-slate-500 sm:text-base">Review prices, fundamentals, quality-gated point-in-time factors, news, and an optional AI synthesis in one traceable workspace.</p>
                            <div className="mt-7 flex flex-wrap justify-center gap-2">
                                {watchlist.slice(0, 6).map((symbol) => <button key={symbol} type="button" className="secondary-button font-mono" onClick={() => selectTicker(symbol)}>{symbol.replace(".US", "")}</button>)}
                            </div>
                        </section>
                    )}

                    {loading && !stockData && (
                        <div className="grid gap-5" aria-label="Loading security analysis">
                            <div className="surface-panel h-64 animate-pulse bg-slate-100 dark:bg-slate-800" />
                            <div className="grid gap-5 xl:grid-cols-2"><div className="surface-panel h-96 animate-pulse bg-slate-100 dark:bg-slate-800" /><div className="surface-panel h-96 animate-pulse bg-slate-100 dark:bg-slate-800" /></div>
                            <p className="text-center text-sm text-slate-500">Loading cached data or synchronizing the latest available snapshot…</p>
                        </div>
                    )}

                    {error && !stockData && (
                        <div className="error-panel flex min-h-[320px] flex-col items-center justify-center text-center" role="alert">
                            <AlertCircle size={34} /><h2 className="mt-4 text-lg font-black">Analysis unavailable</h2><p className="mt-2 max-w-lg">{error}</p>
                            <button type="button" className="secondary-button mt-5" onClick={() => void loadStock(ticker, chartInterval, financialPeriod, true)}>Try again</button>
                        </div>
                    )}

                    {stockData && (
                        <>
                            {error && <div className="error-panel" role="alert">{error} The previous snapshot remains visible.</div>}
                            <section className="surface-panel overflow-hidden p-5 sm:p-7">
                                <div className="flex flex-col justify-between gap-6 lg:flex-row lg:items-start">
                                    <div className="min-w-0">
                                        <div className="flex flex-wrap items-center gap-3">
                                            <h1 className="text-3xl font-black tracking-[-0.04em] sm:text-4xl">{stockData.profile.name || stockData.profile.ticker}</h1>
                                            {watchlist.includes(stockData.profile.ticker) ? (
                                                <span className="status-pill"><Check size={14} /> In watchlist</span>
                                            ) : (
                                                <button type="button" onClick={() => saveWatchlist([stockData.profile.ticker, ...watchlist])} className="secondary-button min-h-9 px-3 py-1.5"><Plus size={15} /> Add</button>
                                            )}
                                        </div>
                                        <div className="mt-3 flex flex-wrap gap-2 text-xs font-semibold text-slate-500 dark:text-slate-400">
                                            <span className="rounded-lg border px-2.5 py-1 font-mono text-emerald-700 dark:text-emerald-300">{stockData.profile.ticker}</span>
                                            {stockData.profile.exchange && <span className="rounded-lg border px-2.5 py-1">{stockData.profile.exchange}</span>}
                                            {stockData.profile.sector && <span className="rounded-lg border px-2.5 py-1">{stockData.profile.sector}</span>}
                                            {stockData.profile.industry && <span className="hidden rounded-lg border px-2.5 py-1 sm:inline-flex">{stockData.profile.industry}</span>}
                                        </div>
                                    </div>
                                    <div className="surface-subtle min-w-[240px] rounded-xl border p-4">
                                        <p className="eyebrow">Latest adjusted close</p>
                                        <div className="mt-1 flex items-baseline gap-2"><span className="text-sm font-bold text-slate-400">{stockData.profile.currency || "USD"}</span><span className="font-mono text-3xl font-black">{latest?.close?.toFixed(2) ?? "—"}</span></div>
                                        {change != null && <p className={`mt-1 flex items-center gap-1 text-sm font-bold ${isPositive ? "text-emerald-600 dark:text-emerald-400" : "text-rose-500"}`}>{isPositive ? <ArrowUpRight size={15} /> : <ArrowDownRight size={15} />}{Math.abs(change).toFixed(2)} {changePct == null ? "" : `(${Math.abs(changePct * 100).toFixed(2)}%)`}</p>}
                                        {latest?.date && <p className="mt-2 flex items-center gap-1.5 text-xs text-slate-500"><Clock3 size={12} /> Price date {latest.date}</p>}
                                    </div>
                                </div>
                                {stockData.profile.description && <p className="mt-6 border-t pt-5 text-sm leading-6 text-slate-600 line-clamp-3 hover:line-clamp-none dark:text-slate-400">{stockData.profile.description}</p>}
                            </section>

                            <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1.25fr)_minmax(360px,0.75fr)]">
                                {stockData.valuation_metrics ? <ValuationDashboard metrics={stockData.valuation_metrics} /> : <section className="surface-panel p-8 text-sm text-slate-500">Fundamental valuation is not available for this security.</section>}
                                <PointInTimeFactorPanel snapshot={factorSnapshot} loading={factorLoading} error={factorError} />
                            </div>

                            <section className="surface-panel overflow-hidden">
                                <header className="surface-subtle flex flex-col justify-between gap-2 border-b px-4 py-3 sm:flex-row sm:items-center">
                                    <div><p className="eyebrow">Market history</p><h2 className="mt-0.5 font-black">Price & volume</h2></div>
                                    <span className="text-xs text-slate-500">Logarithmic price scale · adjusted data</span>
                                </header>
                                <div className="p-2 sm:p-4"><StockChart data={stockData.historical_data} interval={chartInterval} onIntervalChange={handleIntervalChange} isLoading={chartLoading} /></div>
                            </section>

                            <FinancialTrendChart data={stockData.historical_financials} ttmData={stockData.valuation_metrics?.ttm} currentPrice={stockData.valuation_metrics?.valuation.current_price} timePeriod={financialPeriod} onTimePeriodChange={handlePeriodChange} />

                            <div className="grid items-stretch gap-5 xl:grid-cols-2">
                                <AIReport ticker={stockData.profile.ticker} />
                                <div className="min-h-[420px]"><NewsFeed ticker={stockData.profile.ticker} /></div>
                            </div>

                            <footer className="flex flex-wrap items-center gap-2 rounded-xl border px-4 py-3 text-xs text-slate-500 dark:text-slate-400">
                                <ShieldCheck size={14} className="text-emerald-500" /> Published factor values are versioned and quality-gated. DCF and AI outputs are illustrative secondary views.
                            </footer>
                        </>
                    )}
                </div>
            </div>
        </div>
    );
}

export default function Home() {
    return <Suspense fallback={<div className="h-full animate-pulse bg-[var(--app-bg)]" />}><AnalysisPage /></Suspense>;
}
