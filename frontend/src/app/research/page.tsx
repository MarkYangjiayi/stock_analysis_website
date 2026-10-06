"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";
import { BarChart3, CalendarRange, FlaskConical, KeyRound, Loader2, ShieldCheck, TriangleAlert } from "lucide-react";
import {
    BacktestDetails,
    createBacktest,
    FactorResearchResult,
    fetchBacktest,
    fetchQuantCoverage,
    QuantCoverage,
    runFactorResearch,
} from "@/lib/api";

const BacktestEquityChart = dynamic(() => import("@/components/BacktestEquityChart"), {
    ssr: false,
    loading: () => <div className="h-[360px] animate-pulse rounded-lg bg-surface-muted" />,
});

type Mode = "research" | "backtest";

const FACTOR_LABELS: Record<string, string> = {
    composite: "Composite",
    value: "Value",
    quality: "Quality",
    growth: "Growth",
    momentum: "Momentum",
    low_volatility: "Low volatility",
};

const formatMetricLabel = (value: string) => value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
const isPercentMetric = (label: string) => ["return", "drawdown", "volatility", "error", "var", "cvar", "alpha", "turnover", "cost"].some((token) => label.includes(token));
const formatMetric = (value: number | undefined, percent = false) => value == null || !Number.isFinite(value) ? "—" : percent ? `${(value * 100).toFixed(2)}%` : value.toFixed(3);

export default function ResearchPage() {
    const [mode, setMode] = useState<Mode>("research");
    const [coverage, setCoverage] = useState<QuantCoverage | null>(null);
    const [coverageError, setCoverageError] = useState("");
    const [factorName, setFactorName] = useState("composite");
    const [startDate, setStartDate] = useState("");
    const [endDate, setEndDate] = useState("");
    const [horizonDays, setHorizonDays] = useState(21);
    const [quantiles, setQuantiles] = useState(5);
    const [adminKey, setAdminKey] = useState("");
    const [topN, setTopN] = useState(30);
    const [rebalance, setRebalance] = useState("monthly");
    const [costBps, setCostBps] = useState(5);
    const [research, setResearch] = useState<FactorResearchResult | null>(null);
    const [backtest, setBacktest] = useState<BacktestDetails | null>(null);
    const [loading, setLoading] = useState<Mode | "coverage" | null>("coverage");
    const [error, setError] = useState("");
    const requestRef = useRef<AbortController | null>(null);

    useEffect(() => {
        const controller = new AbortController();
        void fetchQuantCoverage(controller.signal)
            .then((data) => {
                setCoverage(data);
                setStartDate(data.factors.min_date || "");
                setEndDate(data.factors.max_date || "");
            })
            .catch((caught) => {
                if (caught instanceof DOMException && caught.name === "AbortError") return;
                setCoverageError(caught instanceof Error ? caught.message : "Unable to load research coverage.");
            })
            .finally(() => { if (!controller.signal.aborted) setLoading(null); });
        return () => controller.abort();
    }, []);

    useEffect(() => () => requestRef.current?.abort(), []);

    const coverageReady = Boolean(coverage && coverage.factors.date_count >= 2);
    const validDates = Boolean(startDate && endDate && startDate < endDate);
    const researchReady = coverageReady && validDates;
    const factorOptions = coverage?.factors.names.length ? coverage.factors.names : Object.keys(FACTOR_LABELS);

    const executeResearch = async () => {
        if (!researchReady) return;
        requestRef.current?.abort();
        const controller = new AbortController();
        requestRef.current = controller;
        setLoading("research");
        setError("");
        setResearch(null);
        try {
            setResearch(await runFactorResearch({
                start_date: startDate,
                end_date: endDate,
                factor_name: factorName,
                factor_version: "lfq-v1",
                horizon_days: horizonDays,
                quantiles,
            }, controller.signal));
        } catch (caught) {
            if (caught instanceof DOMException && caught.name === "AbortError") return;
            setError(caught instanceof Error ? caught.message : "Factor research failed.");
        } finally {
            if (!controller.signal.aborted) setLoading(null);
        }
    };

    const executeBacktest = async () => {
        if (!researchReady || !adminKey) return;
        requestRef.current?.abort();
        const controller = new AbortController();
        requestRef.current = controller;
        setLoading("backtest");
        setError("");
        setBacktest(null);
        try {
            const summary = await createBacktest({
                name: `${FACTOR_LABELS[factorName] || factorName} low-frequency strategy`,
                start_date: startDate,
                end_date: endDate,
                factor_name: factorName,
                factor_version: "lfq-v1",
                universe: "SP500",
                benchmark: "SPY.US",
                rebalance_frequency: rebalance,
                signal_lag_days: 1,
                top_n: topN,
                max_position_weight: 0.05,
                max_sector_weight: 0.30,
                transaction_cost_bps: costBps,
                slippage_bps: costBps,
                require_point_in_time_universe: true,
                missing_price_policy: "fail",
            }, adminKey, controller.signal);
            if (!controller.signal.aborted) setBacktest(await fetchBacktest(summary.id, adminKey, controller.signal));
        } catch (caught) {
            if (caught instanceof DOMException && caught.name === "AbortError") return;
            setError(caught instanceof Error ? caught.message : "Backtest failed.");
        } finally {
            if (!controller.signal.aborted) setLoading(null);
        }
    };

    const quantileEntries = useMemo(() => research ? Object.entries(research.quantile_returns).sort(([a], [b]) => Number(a) - Number(b)) : [], [research]);
    const maxQuantileMagnitude = Math.max(...quantileEntries.map(([, value]) => Math.abs(value)), 0.0001);

    return (
        <div className="app-page">
            <div className="page-container">
                <header className="flex flex-col justify-between gap-4 border-b pb-5 lg:flex-row lg:items-end">
                    <div>
                        <p className="eyebrow">Point-in-time research</p>
                        <h1 className="page-title mt-1">Factor Lab</h1>
                        <p className="page-description">Validate published cross-sectional signals, then run lagged and cost-aware portfolio simulations.</p>
                    </div>
                    {coverage && <div className="flex flex-wrap gap-2">
                        <span className="status-pill"><ShieldCheck size={13} /> lfq-v1</span>
                        <span className="rounded-full border px-2.5 py-1 font-mono text-xs font-medium text-fg-muted">{coverage.factors.ticker_count.toLocaleString()} securities</span>
                        <span className="rounded-full border px-2.5 py-1 font-mono text-xs font-medium text-fg-muted">{coverage.factors.date_count} published dates</span>
                    </div>}
                </header>

                {coverageError && <div className="error-panel" role="alert">{coverageError}</div>}

                <section className="surface-panel overflow-hidden">
                    <div className="flex border-b px-3 sm:px-5" role="tablist" aria-label="Factor Lab mode">
                        {(["research", "backtest"] as Mode[]).map((tab) => (
                            <button key={tab} type="button" role="tab" aria-selected={mode === tab} onClick={() => { setMode(tab); setError(""); }} className={`border-b-2 px-4 py-4 text-sm font-semibold capitalize transition-colors ${mode === tab ? "border-accent text-fg [&_svg]:text-accent" : "border-transparent text-fg-muted hover:text-fg"}`}>
                                {tab === "research" ? <><FlaskConical className="mr-2 inline" size={15} />Factor research</> : <><BarChart3 className="mr-2 inline" size={15} />Portfolio backtest</>}
                            </button>
                        ))}
                    </div>

                    <div className="grid gap-4 p-4 sm:grid-cols-2 sm:p-5 lg:grid-cols-4">
                        <label className="text-xs font-medium uppercase tracking-wide text-fg-muted">Start date<input type="date" className="control-field mt-1.5" value={startDate} min={coverage?.factors.min_date || undefined} max={endDate || undefined} onChange={(event) => setStartDate(event.target.value)} /></label>
                        <label className="text-xs font-medium uppercase tracking-wide text-fg-muted">End date<input type="date" className="control-field mt-1.5" value={endDate} min={startDate || undefined} max={coverage?.factors.max_date || undefined} onChange={(event) => setEndDate(event.target.value)} /></label>
                        <label className="text-xs font-medium uppercase tracking-wide text-fg-muted">Factor<select className="control-field mt-1.5" value={factorName} onChange={(event) => setFactorName(event.target.value)}>{factorOptions.map((name) => <option key={name} value={name}>{FACTOR_LABELS[name] || formatMetricLabel(name)}</option>)}</select></label>

                        {mode === "research" ? <>
                            <label className="text-xs font-medium uppercase tracking-wide text-fg-muted">Forward horizon<select className="control-field mt-1.5" value={horizonDays} onChange={(event) => setHorizonDays(Number(event.target.value))}><option value={5}>5 trading days</option><option value={21}>21 trading days</option><option value={63}>63 trading days</option></select></label>
                            <label className="text-xs font-medium uppercase tracking-wide text-fg-muted">Quantiles<select className="control-field mt-1.5" value={quantiles} onChange={(event) => setQuantiles(Number(event.target.value))}><option value={3}>3</option><option value={5}>5</option><option value={10}>10</option></select></label>
                        </> : <>
                            <label className="text-xs font-medium uppercase tracking-wide text-fg-muted">Rebalance<select className="control-field mt-1.5" value={rebalance} onChange={(event) => setRebalance(event.target.value)}><option value="weekly">Weekly</option><option value="monthly">Monthly</option><option value="all">Every signal date</option></select></label>
                            <label className="text-xs font-medium uppercase tracking-wide text-fg-muted">Top positions<input type="number" className="control-field mt-1.5" min={2} max={500} value={topN} onChange={(event) => setTopN(Number(event.target.value))} /></label>
                            <label className="text-xs font-medium uppercase tracking-wide text-fg-muted">Cost + slippage, each<input type="number" className="control-field mt-1.5" min={0} max={500} value={costBps} onChange={(event) => setCostBps(Number(event.target.value))} /><span className="mt-1 block normal-case tracking-normal text-fg-muted">Basis points per leg</span></label>
                            <label className="text-xs font-medium uppercase tracking-wide text-fg-muted"><span className="flex items-center gap-1.5"><KeyRound size={13} /> Admin key</span><input type="password" autoComplete="off" className="control-field mt-1.5" value={adminKey} onChange={(event) => setAdminKey(event.target.value)} placeholder="Session only" /></label>
                        </>}
                    </div>

                    <div className="surface-subtle flex flex-col justify-between gap-3 border-t px-4 py-3 sm:flex-row sm:items-center sm:px-5">
                        <p className="flex items-center gap-2 font-mono text-xs text-fg-muted"><CalendarRange size={14} /> Coverage: {coverage?.factors.min_date || "—"} to {coverage?.factors.max_date || "—"}</p>
                        <button type="button" className="primary-button" disabled={loading !== null || !researchReady || (mode === "backtest" && !adminKey)} onClick={mode === "research" ? executeResearch : executeBacktest}>
                            {loading === mode && <Loader2 className="animate-spin" size={16} />}{mode === "research" ? "Run factor research" : "Run backtest"}
                        </button>
                    </div>
                </section>

                {!coverageReady && loading !== "coverage" && (
                    <div className="rounded-lg border border-caution/30 bg-caution/6 px-4 py-4 text-sm text-fg">
                        <div className="flex gap-3"><TriangleAlert className="mt-0.5 shrink-0 text-caution" size={18} /><div><strong className="font-semibold text-caution">Historical research is not ready yet.</strong><p className="mt-1 leading-6">The database currently contains {coverage?.factors.date_count ?? 0} published factor date. At least two dated cross-sections plus forward prices are required. The controls will unlock automatically as the daily publication history grows.</p></div></div>
                    </div>
                )}

                {error && <div className="error-panel" role="alert">{error}</div>}

                {research && (
                    <section className="space-y-5">
                        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
                            {[["Mean Rank IC", research.mean_rank_ic, false], ["IC information ratio", research.ic_information_ratio, false], ["Positive IC rate", research.positive_ic_rate, true], ["Long–short spread", research.long_short_spread, true], ["Monotonicity", research.monotonicity, false], ["Top quantile turnover", research.top_quantile_turnover, true]].map(([label, value, percent]) => (
                                <div key={String(label)} className="surface-panel p-4"><p className="text-xs font-medium text-fg-muted">{label}</p><p className="mt-2 font-mono text-xl font-semibold">{formatMetric(value as number, percent as boolean)}</p></div>
                            ))}
                        </div>
                        <div className="surface-panel p-5 sm:p-6">
                            <div className="flex flex-col justify-between gap-2 border-b pb-4 sm:flex-row"><div><p className="eyebrow">Forward return by bucket</p><h2 className="mt-1 text-lg font-semibold">Quantile monotonicity</h2></div><p className="font-mono text-xs text-fg-muted">{research.observations.toLocaleString()} observations · {research.dates} dates</p></div>
                            <div className="mt-6 grid h-64 grid-cols-3 items-end gap-3 sm:grid-cols-5">
                                {quantileEntries.map(([quantile, value]) => {
                                    const height = Math.max(8, Math.abs(value) / maxQuantileMagnitude * 180);
                                    return <div key={quantile} className="flex h-full flex-col items-center justify-end"><span className={`mb-2 font-mono text-xs font-semibold ${value >= 0 ? "text-up" : "text-down"}`}>{formatMetric(value, true)}</span><div className={`w-full max-w-24 rounded-t-md ${value >= 0 ? "bg-up/70" : "bg-down/70"}`} style={{ height }} /><span className="mt-2 font-mono text-xs font-medium text-fg-muted">Q{quantile}</span></div>;
                                })}
                            </div>
                        </div>
                    </section>
                )}

                {backtest && (
                    <section className="space-y-5">
                        <div className="surface-panel overflow-hidden"><div className="flex flex-col justify-between gap-2 border-b p-5 sm:flex-row sm:items-center"><div><p className="eyebrow">Backtest #{backtest.id}</p><h2 className="mt-1 text-lg font-semibold">{backtest.name}</h2></div><span className="status-pill">{backtest.status}</span></div>{backtest.equity_curve?.length ? <BacktestEquityChart data={backtest.equity_curve} /> : <p className="p-8 text-sm text-fg-muted">No equity curve was stored for this run.</p>}</div>
                        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">{Object.entries(backtest.metrics || {}).map(([label, value]) => <div key={label} className="surface-panel p-4"><p className="text-xs text-fg-muted">{formatMetricLabel(label)}</p><p className="mt-2 font-mono text-lg font-semibold">{formatMetric(value, isPercentMetric(label))}</p></div>)}</div>
                        {backtest.attribution?.sector_return_contribution && <div className="surface-panel p-5"><p className="eyebrow">Attribution</p><h2 className="mt-1 text-lg font-semibold">Sector return contribution</h2><div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">{Object.entries(backtest.attribution.sector_return_contribution).sort(([, a], [, b]) => b - a).map(([sector, value]) => <div key={sector} className="flex items-center justify-between rounded-lg border px-3 py-2 text-sm"><span>{sector}</span><strong className={`font-mono font-semibold ${value >= 0 ? "text-up" : "text-down"}`}>{formatMetric(value, true)}</strong></div>)}</div></div>}
                        <details className="surface-panel p-5"><summary className="cursor-pointer font-semibold">Methodology diagnostics</summary><pre className="mt-4 overflow-x-auto rounded-lg border bg-surface-muted p-4 font-mono text-xs leading-5 text-fg-muted">{JSON.stringify(backtest.diagnostics, null, 2)}</pre></details>
                    </section>
                )}
            </div>
        </div>
    );
}
