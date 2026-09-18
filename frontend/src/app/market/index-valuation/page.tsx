"use client";

import { useCallback, useEffect, useState } from "react";
import {
    CalendarClock,
    Info,
    Landmark,
    Loader2,
    RotateCcw,
    TriangleAlert,
    Users,
} from "lucide-react";

import IndexValuationChart from "@/components/IndexValuationChart";
import MarketTabs from "@/components/MarketTabs";
import { fetchIndexValuation, type IndexValuationResponse } from "@/lib/api";

const multiple = (value: number | null | undefined) =>
    value == null || !Number.isFinite(value) ? "—" : `${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}×`;

function ValuationMetric({
    label,
    value,
    note,
}: {
    label: string;
    value: string;
    note: string;
}) {
    return (
        <article className="surface-panel px-4 py-3">
            <p className="text-xs font-bold uppercase tracking-wide text-slate-500 dark:text-slate-400">{label}</p>
            <p className="mt-1 font-mono text-2xl font-black tracking-tight">{value}</p>
            <p className="mt-1 text-[11px] text-slate-500 dark:text-slate-400">{note}</p>
        </article>
    );
}

export default function IndexValuationPage() {
    const [data, setData] = useState<IndexValuationResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [retryKey, setRetryKey] = useState(0);

    const loadData = useCallback(async (signal?: AbortSignal) => {
        setLoading(true);
        setError("");
        try {
            setData(await fetchIndexValuation(signal));
        } catch (caught) {
            if (caught instanceof DOMException && caught.name === "AbortError") return;
            setError(caught instanceof Error ? caught.message : "Unable to load index valuation data.");
        } finally {
            if (!signal?.aborted) setLoading(false);
        }
    }, []);

    useEffect(() => {
        const controller = new AbortController();
        void loadData(controller.signal);
        return () => controller.abort();
    }, [loadData, retryKey]);

    const latestPoint = data?.points.findLast((point) => point.index_pe != null) ?? null;
    const historyStartYear = data?.meta.history_start.slice(0, 4);

    return (
        <div className="app-page">
            <div className="page-container max-w-[1500px]">
                <header className="flex flex-col justify-between gap-4 border-b pb-5 xl:flex-row xl:items-end">
                    <div>
                        <p className="eyebrow">Valuation &amp; earnings</p>
                        <h1 className="page-title mt-1">S&amp;P 500 Historical P/E</h1>
                        <p className="page-description">
                            Month-end aggregate price-to-earnings reconstructed from point-in-time index membership,
                            reported quarterly earnings and split-only prices
                            {historyStartYear ? ` since ${historyStartYear}.` : " over the verified history window."}
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
                            <span className="status-pill">
                                <Users size={13} /> Point-in-time membership
                            </span>
                        </div>
                    )}
                </header>

                <MarketTabs active="valuation" />

                {!!data?.meta.warnings.length && (
                    <div className="rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-200" role="status">
                        <TriangleAlert className="mr-2 inline" size={16} />
                        {data.meta.warnings.slice(0, 4).join(" · ")}
                        {data.meta.warnings.length > 4 ? ` · +${data.meta.warnings.length - 4} more` : ""}
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
                    <section className="surface-panel flex min-h-[420px] flex-col items-center justify-center">
                        <Loader2 className="animate-spin text-emerald-500" size={34} />
                        <p className="mt-4 text-sm text-slate-500">Loading index valuation history…</p>
                    </section>
                )}

                {data && (
                    <>
                        <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5" aria-label="Index valuation summary">
                            <ValuationMetric
                                label="Latest index P/E"
                                value={multiple(data.stats.latest_index_pe)}
                                note={data.stats.latest_date ? `Month-end ${data.stats.latest_date}` : "No valid month yet"}
                            />
                            <ValuationMetric
                                label="Full-history median"
                                value={multiple(data.stats.median_index_pe)}
                                note="Dashed reference line"
                            />
                            <ValuationMetric
                                label="History range"
                                value={data.stats.min_index_pe != null && data.stats.max_index_pe != null
                                    ? `${multiple(data.stats.min_index_pe)} – ${multiple(data.stats.max_index_pe)}`
                                    : "—"}
                                note={`${data.meta.history_start} → ${data.meta.history_end}`}
                            />
                            <ValuationMetric
                                label="Earning companies only"
                                value={multiple(data.stats.latest_index_pe_earners)}
                                note={`Loss makers excluded · latest ${data.stats.latest_date ?? "—"}`}
                            />
                            <ValuationMetric
                                label="Valid months"
                                value={`${data.stats.months_valid} / ${data.stats.months_total}`}
                                note={data.stats.average_coverage_pct != null
                                    ? `Avg company coverage ${data.stats.average_coverage_pct.toFixed(1)}%`
                                    : "Coverage unavailable"}
                            />
                        </section>

                        <section className="surface-panel overflow-hidden" aria-labelledby="index-pe-chart-title">
                            <div className="border-b px-4 py-4 sm:px-5">
                                <h2 id="index-pe-chart-title" className="text-base font-black">Index P/E · month-end</h2>
                                <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                                    Aggregate = total member equity ÷ total trailing reported earnings. The dashed line is the
                                    full-history median; the secondary series repeats the ratio over profitable companies only.
                                    {latestPoint != null && latestPoint.loss_maker_count > 0 && ` ${latestPoint.loss_maker_count} loss-making companies currently reduce the aggregate denominator.`}
                                </p>
                            </div>
                            <IndexValuationChart data={data} />
                            <div className="border-t px-4 py-3 text-xs text-[var(--text-muted)] sm:px-5">
                                <p>
                                    <Landmark className="mr-1.5 inline" size={14} />
                                    Reconstructed estimates over point-in-time membership; gaps mean a month failed its
                                    coverage or member gate and are never interpolated.
                                </p>
                                <p className="mt-1">
                                    <Info className="mr-1.5 inline" size={14} />
                                    Forward P/E requires archived analyst expectations and is not inferred from today&apos;s forecasts.
                                </p>
                                <details className="mt-3">
                                    <summary className="cursor-pointer font-semibold text-[var(--text)]">Methodology &amp; data coverage</summary>
                                    {data.methodology.map((note) => <p key={note} className="mt-2 leading-5">{note}</p>)}
                                </details>
                            </div>
                        </section>
                    </>
                )}
            </div>
        </div>
    );
}
