"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
    CalendarClock,
    ExternalLink,
    Landmark,
    Loader2,
    RotateCcw,
    TriangleAlert,
} from "lucide-react";

import MarketTabs from "@/components/MarketTabs";
import {
    CurrentYieldCurveChart,
    YieldHistoryChart,
    YieldSpreadChart,
} from "@/components/YieldCurveCharts";
import {
    fetchTreasuryYieldCurve,
    type TreasuryYieldCurveResponse,
    type YieldCurvePeriod,
} from "@/lib/api";

const PERIODS: Array<{ value: YieldCurvePeriod; label: string }> = [
    { value: "1y", label: "1Y" },
    { value: "3y", label: "3Y" },
    { value: "5y", label: "5Y" },
];

const HISTORY_MATURITIES = ["3m", "6m", "1y", "2y", "3y", "5y", "7y", "10y", "20y", "30y"];
const DEFAULT_MATURITIES = ["3m", "2y", "10y", "30y"];

const formatYield = (value: number | null | undefined) =>
    value == null || !Number.isFinite(value) ? "—" : `${value.toFixed(2)}%`;

const formatBasisPointChange = (current: number | null | undefined, previous: number | null | undefined) => {
    if (current == null || previous == null || !Number.isFinite(current) || !Number.isFinite(previous)) return "No 1M comparison";
    const change = Math.round((current - previous) * 100);
    return `${change > 0 ? "+" : ""}${change} bp vs 1M ago`;
};

function CurveMetric({
    label,
    value,
    comparison,
}: {
    label: string;
    value: number | null | undefined;
    comparison?: number | null;
}) {
    return (
        <article className="surface-panel px-4 py-3">
            <p className="text-xs font-semibold uppercase tracking-wide text-fg-muted">{label}</p>
            <p className="mt-1 font-mono text-2xl font-semibold tracking-tight">{formatYield(value)}</p>
            {comparison !== undefined && (
                <p className="mt-1 font-mono text-xs text-fg-muted">
                    {formatBasisPointChange(value, comparison)}
                </p>
            )}
        </article>
    );
}

export default function TreasuryYieldCurvePage() {
    const [period, setPeriod] = useState<YieldCurvePeriod>("1y");
    const [selectedMaturities, setSelectedMaturities] = useState(DEFAULT_MATURITIES);
    const [data, setData] = useState<TreasuryYieldCurveResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [retryKey, setRetryKey] = useState(0);

    const loadData = useCallback(async (signal?: AbortSignal) => {
        setLoading(true);
        setError("");
        try {
            const response = await fetchTreasuryYieldCurve(period, signal);
            setData(response);
        } catch (caught) {
            if (caught instanceof DOMException && caught.name === "AbortError") return;
            setError(caught instanceof Error ? caught.message : "Unable to load Treasury yield-curve data.");
        } finally {
            if (!signal?.aborted) setLoading(false);
        }
    }, [period]);

    useEffect(() => {
        const controller = new AbortController();
        void loadData(controller.signal);
        return () => controller.abort();
    }, [loadData, retryKey]);

    const metrics = useMemo(() => {
        if (!data) return null;
        const oneMonthAgo = data.snapshots.find((snapshot) => snapshot.key === "1m");
        const latest = data.latest.yields;
        const tenTwo = latest["10y"] != null && latest["2y"] != null
            ? latest["10y"]! - latest["2y"]!
            : null;
        const tenThreeMonth = latest["10y"] != null && latest["3m"] != null
            ? latest["10y"]! - latest["3m"]!
            : null;
        const curveShape = tenTwo == null
            ? "Unavailable"
            : tenTwo < -0.05
                ? "Inverted"
                : tenTwo <= 0.25
                    ? "Near flat"
                    : "Upward sloping";
        return { latest, oneMonthAgo: oneMonthAgo?.yields, tenTwo, tenThreeMonth, curveShape };
    }, [data]);

    const toggleMaturity = (key: string) => {
        setSelectedMaturities((current) => {
            if (current.includes(key)) {
                return current.length === 1 ? current : current.filter((value) => value !== key);
            }
            return current.length >= 6 ? current : [...current, key];
        });
    };

    return (
        <div className="app-page">
            <div className="page-container max-w-[1500px]">
                <header className="flex flex-col justify-between gap-4 border-b pb-5 xl:flex-row xl:items-end">
                    <div>
                        <p className="eyebrow">Rates &amp; macro</p>
                        <h1 className="page-title mt-1">U.S. Treasury Yield Curve</h1>
                        <p className="page-description">
                            Read today&apos;s term structure, compare prior curve shapes, and trace how key Treasury maturities and recession-sensitive spreads have moved.
                        </p>
                    </div>
                    {data && (
                        <div className="flex flex-wrap gap-2">
                            <span className={data.meta.stale
                                ? "inline-flex items-center gap-1.5 rounded-md border border-caution/30 bg-caution/6 px-2 py-0.5 font-mono text-xs font-medium text-caution"
                                : "status-pill"
                            }>
                                <CalendarClock size={13} /> Data through {data.meta.as_of_date}
                            </span>
                            <a
                                href={data.meta.source_url}
                                target="_blank"
                                rel="noreferrer"
                                className="inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-xs font-medium text-fg-muted transition-colors hover:border-line-strong hover:text-accent"
                            >
                                <ExternalLink size={13} /> U.S. Treasury source
                            </a>
                        </div>
                    )}
                </header>

                <MarketTabs active="rates" />

                {!!data?.meta.warnings.length && (
                    <div className="rounded-lg border border-caution/30 bg-caution/6 px-4 py-3 text-sm text-caution" role="status">
                        <TriangleAlert className="mr-2 inline" size={16} />
                        {data.meta.warnings.join(" · ")}
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

                {loading && !data && (
                    <section className="surface-panel flex min-h-[520px] flex-col items-center justify-center">
                        <Loader2 className="animate-spin text-accent" size={34} />
                        <p className="mt-4 text-sm text-fg-muted">Loading Treasury term structure…</p>
                    </section>
                )}

                {data && metrics && (
                    <>
                        <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5" aria-label="Latest Treasury yields">
                            {["3m", "2y", "10y", "30y"].map((key) => {
                                const maturity = data.maturities.find((item) => item.key === key);
                                return (
                                    <CurveMetric
                                        key={key}
                                        label={`${maturity?.label ?? key} Treasury`}
                                        value={metrics.latest[key]}
                                        comparison={metrics.oneMonthAgo?.[key]}
                                    />
                                );
                            })}
                            <article className="surface-panel px-4 py-3">
                                <p className="text-xs font-semibold uppercase tracking-wide text-fg-muted">10Y − 2Y spread</p>
                                <p className={`mt-1 font-mono text-2xl font-semibold tracking-tight ${
                                    metrics.tenTwo != null && metrics.tenTwo < 0 ? "text-down" : "text-up"
                                }`}>
                                    {metrics.tenTwo == null ? "—" : `${metrics.tenTwo >= 0 ? "+" : ""}${metrics.tenTwo.toFixed(2)} pp`}
                                </p>
                                <p className="mt-1 text-xs font-semibold text-fg-muted">{metrics.curveShape}</p>
                            </article>
                        </section>

                        <section className="surface-panel overflow-hidden">
                            <div className="border-b px-4 py-4 sm:px-5">
                                <h2 className="text-base font-semibold">Curve shape comparison</h2>
                                <p className="mt-1 text-xs text-fg-muted">
                                    Latest curve against the nearest available observations around 1 month, 3 months, and 1 year earlier.
                                </p>
                            </div>
                            <CurrentYieldCurveChart data={data} />
                        </section>

                        <section className="surface-panel overflow-hidden">
                            <div className="flex flex-col justify-between gap-4 border-b px-4 py-4 sm:px-5 xl:flex-row xl:items-end">
                                <div>
                                    <h2 className="text-base font-semibold">Yield history by maturity</h2>
                                    <p className="mt-1 text-xs text-fg-muted">
                                        Select up to six maturities. This separates short-end policy repricing from long-end growth and inflation risk.
                                    </p>
                                </div>
                                <div className="flex shrink-0 flex-wrap gap-3">
                                    <fieldset>
                                        <legend className="mb-1 text-xs font-semibold uppercase tracking-wide text-fg-muted">History</legend>
                                        <div className="segmented-control">
                                            {PERIODS.map((option) => (
                                                <button
                                                    key={option.value}
                                                    type="button"
                                                    aria-pressed={period === option.value}
                                                    onClick={() => setPeriod(option.value)}
                                                    className="segmented-control-item font-mono"
                                                >
                                                    {option.label}
                                                </button>
                                            ))}
                                        </div>
                                    </fieldset>
                                </div>
                            </div>
                            <div className="flex flex-wrap gap-2 border-b px-4 py-3 sm:px-5" aria-label="Treasury maturity selection">
                                {HISTORY_MATURITIES.map((key) => {
                                    const maturity = data.maturities.find((item) => item.key === key);
                                    const selected = selectedMaturities.includes(key);
                                    const atLimit = selectedMaturities.length >= 6 && !selected;
                                    return (
                                        <button
                                            key={key}
                                            type="button"
                                            aria-pressed={selected}
                                            disabled={atLimit}
                                            title={atLimit ? "Select at most six maturities" : undefined}
                                            onClick={() => toggleMaturity(key)}
                                            className={`rounded-full border px-3 py-1.5 font-mono text-xs font-medium transition-colors ${selected
                                                ? "border-accent bg-accent-soft text-accent-strong"
                                                : atLimit
                                                    ? "cursor-not-allowed text-fg-muted opacity-40"
                                                    : "text-fg-muted hover:border-accent hover:text-accent"
                                            }`}
                                        >
                                            {maturity?.label ?? key}
                                        </button>
                                    );
                                })}
                            </div>
                            {loading && (
                                <div className="flex h-[440px] items-center justify-center">
                                    <Loader2 className="animate-spin text-accent" size={28} />
                                </div>
                            )}
                            {!loading && <YieldHistoryChart data={data} selectedMaturities={selectedMaturities} />}
                        </section>

                        <section className="surface-panel overflow-hidden">
                            <div className="border-b px-4 py-4 sm:px-5">
                                <h2 className="text-base font-semibold">Key curve spreads</h2>
                                <p className="mt-1 text-xs text-fg-muted">
                                    Values below zero indicate inversion. 10Y−2Y tracks the note curve; 10Y−3M contrasts long rates with the policy-sensitive front end.
                                </p>
                            </div>
                            <YieldSpreadChart data={data} />
                        </section>

                        <footer className="grid gap-3 rounded-lg border px-4 py-3 text-xs text-fg-muted sm:grid-cols-2">
                            <span><Landmark className="mr-1.5 inline" size={14} /><strong className="text-fg">Basis:</strong> Official daily par yields, not zero-coupon spot rates.</span>
                            <span><strong className="text-fg">Delivery:</strong> {data.meta.source_name} data via {data.meta.provider_name}; cached for resilience.</span>
                            <span><strong className="text-fg">Latest 10Y−3M:</strong> {metrics.tenThreeMonth == null ? "—" : `${metrics.tenThreeMonth >= 0 ? "+" : ""}${metrics.tenThreeMonth.toFixed(2)} percentage points`}.</span>
                            <span><strong className="text-fg">Timing:</strong> Treasury normally publishes after the U.S. market close; weekends and holidays carry forward the latest observation.</span>
                        </footer>
                    </>
                )}
            </div>
        </div>
    );
}
