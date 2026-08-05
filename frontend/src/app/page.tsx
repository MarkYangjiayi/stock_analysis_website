"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { useRouter, useSearchParams } from "next/navigation";
import { AlertCircle, ArrowDownRight, ArrowUpRight, Check, Clock3, Plus, Search, ShieldCheck } from "lucide-react";
import { ApiError, DecisionSupportResponse, DecisionWarning, fetchDecisionSupport, fetchLatestTickerFactors, fetchStockData, PublishedFactorSnapshot, StockDataResponse } from "@/lib/api";
import DecisionCockpit from "@/components/DecisionCockpit";
import NewsFeed from "@/components/NewsFeed";
import PersonalUnlockDialog from "@/components/PersonalUnlockDialog";
import PointInTimeFactorPanel from "@/components/PointInTimeFactorPanel";
import WatchlistSidebar from "@/components/WatchlistSidebar";
import { usePersonalWorkspace } from "@/hooks/usePersonalWorkspace";
import type { FinancialEvidenceMetric } from "@/components/FinancialTrendChart";

const StockChart = dynamic(() => import("@/components/StockChart"), {
    ssr: false,
    loading: () => <div className="h-[480px] animate-pulse rounded-xl bg-slate-100 dark:bg-slate-800" />,
});
const FinancialTrendChart = dynamic(() => import("@/components/FinancialTrendChart"), {
    ssr: false,
    loading: () => <div className="h-[520px] animate-pulse rounded-2xl bg-slate-100 dark:bg-slate-800" />,
});

function AnalysisPage() {
    const searchParams = useSearchParams();
    const router = useRouter();
    const requestedSymbol = searchParams.get("ticker")?.trim().toUpperCase() || "";
    const requestedTicker = requestedSymbol && !requestedSymbol.includes(".") ? `${requestedSymbol}.US` : requestedSymbol;
    const [ticker, setTicker] = useState("");
    const [stockData, setStockData] = useState<StockDataResponse | null>(null);
    const [factorSnapshot, setFactorSnapshot] = useState<PublishedFactorSnapshot | null>(null);
    const [decision, setDecision] = useState<DecisionSupportResponse | null>(null);
    const [loading, setLoading] = useState(false);
    const [factorLoading, setFactorLoading] = useState(false);
    const [decisionLoading, setDecisionLoading] = useState(false);
    const [decisionRefreshVersion, setDecisionRefreshVersion] = useState(0);
    const [chartLoading, setChartLoading] = useState(false);
    const [error, setError] = useState("");
    const [factorError, setFactorError] = useState("");
    const [decisionError, setDecisionError] = useState("");
    const [chartInterval, setChartInterval] = useState("1d");
    const [financialPeriod, setFinancialPeriod] = useState<"annual" | "ttm" | "quarterly">("annual");
    const [financialMetric, setFinancialMetric] = useState<FinancialEvidenceMetric>("overview");
    const [unlockOpen, setUnlockOpen] = useState(false);
    const personal = usePersonalWorkspace();
    const watchlist = personal.watchlist;
    const handlePersonalUnauthorized = personal.handleUnauthorized;
    const stockRequestRef = useRef<AbortController | null>(null);
    const factorRequestRef = useRef<AbortController | null>(null);
    const decisionRequestRef = useRef<AbortController | null>(null);
    const financialEvidenceRef = useRef<HTMLDivElement | null>(null);

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
            if (!controller.signal.aborted) {
                setStockData(data);
                return true;
            }
            return false;
        } catch (caught) {
            if (caught instanceof DOMException && caught.name === "AbortError") return false;
            if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : "Unable to load this security.");
            return false;
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

    const loadDecision = useCallback(async (symbol: string, adminKey?: string | null) => {
        decisionRequestRef.current?.abort();
        const controller = new AbortController();
        decisionRequestRef.current = controller;
        setDecisionLoading(true);
        setDecisionError("");
        try {
            const result = await fetchDecisionSupport(symbol, adminKey || undefined, controller.signal);
            if (!controller.signal.aborted) setDecision(result);
        } catch (caught) {
            if (caught instanceof DOMException && caught.name === "AbortError") return;
            if (caught instanceof ApiError && caught.status === 401 && adminKey) {
                handlePersonalUnauthorized();
                try {
                    const publicResult = await fetchDecisionSupport(symbol, undefined, controller.signal);
                    if (!controller.signal.aborted) setDecision(publicResult);
                    return;
                } catch (fallbackError) {
                    if (!controller.signal.aborted) setDecisionError(fallbackError instanceof Error ? fallbackError.message : "Decision support is unavailable.");
                    return;
                }
            }
            if (!controller.signal.aborted) setDecisionError(caught instanceof Error ? caught.message : "Decision support is unavailable.");
        } finally {
            if (!controller.signal.aborted) setDecisionLoading(false);
        }
    }, [handlePersonalUnauthorized]);

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
        setDecision(null);
        setChartInterval("1d");
        setFinancialPeriod("annual");
        setFinancialMetric("overview");
        void loadStock(requestedTicker, "1d", "annual", true).then((loaded) => {
            // The stock endpoint can populate a cold local snapshot. Refresh the
            // cockpit after that read-through completes so it sees the new data.
            if (loaded) setDecisionRefreshVersion((version) => version + 1);
        });
        void loadFactors(requestedTicker);
        return () => {
            stockRequestRef.current?.abort();
            factorRequestRef.current?.abort();
        };
    }, [loadFactors, loadStock, requestedTicker]);

    useEffect(() => {
        if (!requestedTicker) {
            setDecision(null);
            setDecisionError("");
            return;
        }
        void loadDecision(requestedTicker, personal.adminKey);
        return () => decisionRequestRef.current?.abort();
    }, [decisionRefreshVersion, loadDecision, personal.adminKey, requestedTicker]);

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

    const addToWatchlist = (symbol: string) => {
        if (!personal.isUnlocked) {
            setUnlockOpen(true);
            return;
        }
        if (!watchlist.includes(symbol)) void personal.replaceWatchlist([symbol, ...watchlist]);
    };

    const removeFromWatchlist = (symbol: string) => {
        if (!personal.isUnlocked) {
            setUnlockOpen(true);
            return;
        }
        void personal.replaceWatchlist(watchlist.filter((item) => item !== symbol));
    };

    const refreshDecision = useCallback(async () => {
        if (!ticker) return;
        await loadDecision(ticker, personal.adminKey);
    }, [loadDecision, personal.adminKey, ticker]);

    const showFinancialEvidence = async (metric: DecisionWarning["evidence_metric"]) => {
        const metricMap: Record<DecisionWarning["evidence_metric"], FinancialEvidenceMetric> = {
            revenue: "revenue",
            gross_margin: "gross_margin",
            operating_margin: "operating_margin",
            fcf: "free_cash_flow",
            cash: "cash_and_short_term_investments",
            debt: "total_debt",
            shares: "shares_outstanding",
        };
        setFinancialMetric(metricMap[metric]);
        if (financialPeriod !== "quarterly") await handlePeriodChange("quarterly");
        window.requestAnimationFrame(() => financialEvidenceRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }));
    };

    const latest = stockData?.historical_data.filter((point) => point.close != null).at(-1);
    const previous = stockData?.historical_data.filter((point) => point.close != null).at(-2);
    const change = latest?.close != null && previous?.close != null ? latest.close - previous.close : null;
    const changePct = change != null && previous?.close ? change / previous.close : null;
    const isPositive = change != null && change >= 0;

    return (
        <div className="flex h-full w-full overflow-hidden bg-[var(--app-bg)]">
            <div className="hidden h-full md:block">
                <WatchlistSidebar currentTicker={ticker} onSelectTicker={selectTicker} watchlist={watchlist} onAdd={addToWatchlist} onRemove={removeFromWatchlist} readOnly={!personal.isUnlocked} onUnlock={() => setUnlockOpen(true)} />
            </div>

            <div className="app-page min-w-0 flex-1">
                <div className="page-container">
                    <WatchlistSidebar compact currentTicker={ticker} onSelectTicker={selectTicker} watchlist={watchlist} onAdd={addToWatchlist} onRemove={removeFromWatchlist} readOnly={!personal.isUnlocked} onUnlock={() => setUnlockOpen(true)} />

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
                                                <button type="button" onClick={() => addToWatchlist(stockData.profile.ticker)} className="secondary-button min-h-9 px-3 py-1.5"><Plus size={15} /> {personal.isUnlocked ? "Add" : "Unlock to add"}</button>
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

                            <DecisionCockpit
                                ticker={stockData.profile.ticker}
                                decision={decision}
                                loading={decisionLoading}
                                error={decisionError}
                                adminKey={personal.adminKey}
                                onUnlock={() => setUnlockOpen(true)}
                                onUnauthorized={personal.handleUnauthorized}
                                onRetry={() => void refreshDecision()}
                                onRefresh={refreshDecision}
                                onShowEvidence={(metric) => void showFinancialEvidence(metric)}
                            />

                            <PointInTimeFactorPanel snapshot={factorSnapshot} loading={factorLoading} error={factorError} />

                            <section className="surface-panel overflow-hidden">
                                <header className="surface-subtle flex flex-col justify-between gap-2 border-b px-4 py-3 sm:flex-row sm:items-center">
                                    <div><p className="eyebrow">Market history</p><h2 className="mt-0.5 font-black">Price & volume</h2></div>
                                    <span className="text-xs text-slate-500">Logarithmic price scale · adjusted data</span>
                                </header>
                                <div className="p-2 sm:p-4"><StockChart data={stockData.historical_data} interval={chartInterval} onIntervalChange={handleIntervalChange} isLoading={chartLoading} /></div>
                            </section>

                            <div ref={financialEvidenceRef} className="scroll-mt-5">
                                <FinancialTrendChart data={stockData.historical_financials} ttmData={stockData.valuation_metrics?.ttm} currentPrice={stockData.valuation_metrics?.valuation.current_price} timePeriod={financialPeriod} onTimePeriodChange={handlePeriodChange} selectedMetric={financialMetric} onMetricChange={setFinancialMetric} />
                            </div>

                            <div className="min-h-[420px]"><NewsFeed ticker={stockData.profile.ticker} /></div>

                            <footer className="flex flex-wrap items-center gap-2 rounded-xl border px-4 py-3 text-xs text-slate-500 dark:text-slate-400">
                                <ShieldCheck size={14} className="text-emerald-500" /> Published factor values are versioned and quality-gated. Cockpit calculations are transparent decision support, not investment advice.
                            </footer>
                        </>
                    )}
                </div>
            </div>
            <PersonalUnlockDialog
                open={unlockOpen}
                loading={personal.unlocking || personal.restoring}
                error={personal.error}
                onClose={() => setUnlockOpen(false)}
                onUnlock={(key) => personal.unlock(key)}
            />
        </div>
    );
}

export default function Home() {
    return <Suspense fallback={<div className="h-full animate-pulse bg-[var(--app-bg)]" />}><AnalysisPage /></Suspense>;
}
