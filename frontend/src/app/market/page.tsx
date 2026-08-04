"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
    Activity,
    CalendarClock,
    Database,
    Loader2,
    RotateCcw,
    ShieldCheck,
    TriangleAlert,
} from "lucide-react";

import MarketOverviewChart, {
    type LowerMetric,
    type TrendMode,
} from "@/components/MarketOverviewChart";
import MarketTabs from "@/components/MarketTabs";
import {
    fetchMarketOverview,
    type MarketOverviewResponse,
    type MarketPeriod,
    type MarketUniverse,
} from "@/lib/api";

const UNIVERSES: Array<{ value: MarketUniverse; label: string; disabled?: boolean }> = [
    { value: "SP500", label: "S&P 500" },
    { value: "RUSSELL2000", label: "Russell 2000", disabled: true },
    { value: "SP500_RUSSELL2000", label: "Combined", disabled: true },
];

const PERIODS: Array<{ value: MarketPeriod; label: string }> = [
    { value: "3m", label: "3M" },
    { value: "6m", label: "6M" },
    { value: "1y", label: "1Y" },
];

const LOWER_METRICS: Array<{ value: LowerMetric; label: string }> = [
    { value: "net_advances", label: "Net advances" },
    { value: "new_high_low", label: "New highs / lows" },
    { value: "mcclellan", label: "McClellan" },
];

function SegmentedControl<T extends string>({
    label,
    value,
    options,
    onChange,
}: {
    label: string;
    value: T;
    options: Array<{ value: T; label: string; disabled?: boolean }>;
    onChange: (value: T) => void;
}) {
    return (
        <fieldset>
            <legend className="mb-1.5 text-[11px] font-bold uppercase tracking-wider text-slate-500 dark:text-slate-400">
                {label}
            </legend>
            <div className="flex w-fit max-w-full overflow-x-auto rounded-xl border bg-slate-50 p-1 dark:bg-slate-950/40">
                {options.map((option) => (
                    <button
                        key={option.value}
                        type="button"
                        aria-pressed={value === option.value}
                        disabled={option.disabled}
                        title={option.disabled ? "Temporarily unavailable pending strict historical membership data" : undefined}
                        onClick={() => onChange(option.value)}
                        className={`shrink-0 rounded-lg px-3 py-2 text-xs font-bold transition-colors sm:text-sm ${
                            value === option.value
                                ? "bg-white text-emerald-700 shadow-sm dark:bg-slate-800 dark:text-emerald-300"
                                : option.disabled
                                    ? "cursor-not-allowed text-slate-300 dark:text-slate-600"
                                : "text-slate-500 hover:text-slate-900 dark:text-slate-400 dark:hover:text-white"
                        }`}
                    >
                        {option.label}
                    </button>
                ))}
            </div>
        </fieldset>
    );
}

export default function MarketOverviewPage() {
    const [universe, setUniverse] = useState<MarketUniverse>("SP500");
    const [period, setPeriod] = useState<MarketPeriod>("1y");
    const [trendMode, setTrendMode] = useState<TrendMode>("relative");
    const [lowerMetric, setLowerMetric] = useState<LowerMetric>("net_advances");
    const [data, setData] = useState<MarketOverviewResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [retryKey, setRetryKey] = useState(0);

    const loadData = useCallback(async (signal?: AbortSignal) => {
        setLoading(true);
        setError("");
        setData(null);
        try {
            const response = await fetchMarketOverview(universe, period, signal);
            setData(response);
        } catch (caught) {
            if (caught instanceof DOMException && caught.name === "AbortError") return;
            setError(caught instanceof Error ? caught.message : "Unable to load market overview.");
        } finally {
            if (!signal?.aborted) setLoading(false);
        }
    }, [period, universe]);

    useEffect(() => {
        const controller = new AbortController();
        void loadData(controller.signal);
        return () => controller.abort();
    }, [loadData, retryKey]);

    const latestStats = useMemo(() => {
        if (!data?.dates.length) return null;
        const index = data.dates.length - 1;
        return {
            members: data.breadth.member_count[index],
            coverage: data.breadth.price_coverage_pct[index],
            ma200: data.breadth.pct_above_ma200[index],
        };
    }, [data]);

    return (
        <div className="app-page">
            <div className="page-container max-w-[1500px]">
                <header className="flex flex-col justify-between gap-4 border-b pb-5 xl:flex-row xl:items-end">
                    <div>
                        <p className="eyebrow">Market participation</p>
                        <h1 className="page-title mt-1">US Market Overview</h1>
                        <p className="page-description">
                            Compare sector leadership with point-in-time breadth, participation, and cross-sectional dispersion.
                        </p>
                    </div>
                    {data && (
                        <div className="flex flex-wrap gap-2">
                            <span className={data.meta.stale
                                ? "inline-flex items-center gap-1.5 rounded-full border border-amber-300 bg-amber-50 px-2.5 py-1 text-xs font-bold text-amber-800 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-200"
                                : "status-pill"
                            }>
                                <CalendarClock size={13} /> Data through {data.meta.as_of_date}
                            </span>
                            <span className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-semibold text-slate-500 dark:text-slate-400">
                                <ShieldCheck size={13} /> Point-in-time membership
                            </span>
                        </div>
                    )}
                </header>

                <MarketTabs active="overview" />

                <section className="surface-panel p-4 sm:p-5" aria-label="Market overview controls">
                    <div className="flex flex-col justify-between gap-5 xl:flex-row xl:items-end">
                        <div className="flex flex-col gap-4 sm:flex-row sm:flex-wrap">
                            <SegmentedControl label="Universe" value={universe} options={UNIVERSES} onChange={setUniverse} />
                            <SegmentedControl label="Period" value={period} options={PERIODS} onChange={setPeriod} />
                            <SegmentedControl
                                label="Sector mode"
                                value={trendMode}
                                options={[
                                    { value: "relative", label: "Relative to SPY" },
                                    { value: "absolute", label: "Absolute" },
                                ]}
                                onChange={setTrendMode}
                            />
                        </div>
                        <SegmentedControl label="Lower panel" value={lowerMetric} options={LOWER_METRICS} onChange={setLowerMetric} />
                    </div>
                    <p className="mt-3 text-xs text-slate-500 dark:text-slate-400">
                        Russell 2000 and Combined are temporarily unavailable until strict point-in-time membership history is available.
                    </p>
                </section>

                {data?.meta.stale && (
                    <div className="rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-200" role="status">
                        <TriangleAlert className="mr-2 inline" size={16} />
                        The latest successful snapshot is from {data.meta.as_of_date}; {data.meta.expected_as_of_date} was expected. Showing the last complete publication.
                    </div>
                )}

                {!!data?.meta.warnings.length && (
                    <div className="rounded-xl border border-amber-300/70 bg-amber-50/70 px-4 py-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/20 dark:text-amber-200" role="status">
                        <strong>Quality note:</strong> {data.meta.warnings.join(" · ")}
                    </div>
                )}

                {error && (
                    <div className="error-panel flex items-center justify-between gap-4" role="alert">
                        <span>{error}</span>
                        <button type="button" className="secondary-button shrink-0" onClick={() => setRetryKey((value) => value + 1)}>
                            <RotateCcw size={15} /> Retry
                        </button>
                    </div>
                )}

                {data && latestStats && (
                    <section className="grid gap-3 sm:grid-cols-3" aria-label="Latest breadth summary">
                        <div className="surface-panel flex items-center gap-3 px-4 py-3">
                            <Database className="text-emerald-600 dark:text-emerald-300" size={19} />
                            <div><p className="text-xs text-slate-500">Members</p><p className="font-mono text-lg font-black">{latestStats.members.toLocaleString()}</p></div>
                        </div>
                        <div className="surface-panel flex items-center gap-3 px-4 py-3">
                            <ShieldCheck className="text-emerald-600 dark:text-emerald-300" size={19} />
                            <div><p className="text-xs text-slate-500">Price coverage</p><p className="font-mono text-lg font-black">{latestStats.coverage == null ? "—" : `${latestStats.coverage.toFixed(1)}%`}</p></div>
                        </div>
                        <div className="surface-panel flex items-center gap-3 px-4 py-3">
                            <Activity className="text-emerald-600 dark:text-emerald-300" size={19} />
                            <div><p className="text-xs text-slate-500">Above MA200</p><p className="font-mono text-lg font-black">{latestStats.ma200 == null ? "—" : `${latestStats.ma200.toFixed(1)}%`}</p></div>
                        </div>
                    </section>
                )}

                <section className="surface-panel min-h-[600px] overflow-hidden" aria-label="Linked market overview chart">
                    {loading && (
                        <div className="flex h-[680px] flex-col items-center justify-center">
                            <Loader2 className="animate-spin text-emerald-500" size={34} />
                            <p className="mt-4 text-sm text-slate-500">Loading point-in-time market breadth…</p>
                        </div>
                    )}
                    {!loading && data && (
                        <MarketOverviewChart data={data} trendMode={trendMode} lowerMetric={lowerMetric} />
                    )}
                    {!loading && !data && !error && (
                        <div className="flex h-[600px] items-center justify-center text-sm text-slate-500">No market overview publication is available.</div>
                    )}
                </section>

                <footer className="grid gap-2 rounded-xl border px-4 py-3 text-xs text-slate-500 dark:text-slate-400 sm:grid-cols-2">
                    <span><strong className="text-slate-700 dark:text-slate-200">Price basis:</strong> Adjusted close, with close used only when adjusted close is unavailable.</span>
                    <span><strong className="text-slate-700 dark:text-slate-200">Membership:</strong> Strict historical S&P 500 intervals; Russell 2000 and Combined are temporarily disabled.</span>
                </footer>
            </div>
        </div>
    );
}
