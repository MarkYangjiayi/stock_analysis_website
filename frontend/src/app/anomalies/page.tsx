"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
    Activity,
    ArrowDownRight,
    ArrowUpRight,
    Clock3,
    ExternalLink,
    Loader2,
    Radar,
    RefreshCw,
    Sparkles,
} from "lucide-react";
import {
    fetchAnomalyScan,
    fetchLatestAnomalyScan,
    startAnomalyScan,
} from "@/lib/api";
import type { AnomalyReport, AnomalyScan } from "@/lib/api";
import { useAppStore } from "@/store/useAppStore";

const POLL_INTERVAL_MS = 1_200;
const POLL_BUDGET_MS = 120_000;

const isAbortError = (error: unknown) =>
    error instanceof DOMException && error.name === "AbortError";

const waitForNextPoll = (signal: AbortSignal) => new Promise<void>((resolve, reject) => {
    if (signal.aborted) {
        reject(new DOMException("Request aborted", "AbortError"));
        return;
    }
    const timer = globalThis.setTimeout(() => {
        signal.removeEventListener("abort", onAbort);
        resolve();
    }, POLL_INTERVAL_MS);
    const onAbort = () => {
        globalThis.clearTimeout(timer);
        reject(new DOMException("Request aborted", "AbortError"));
    };
    signal.addEventListener("abort", onAbort, { once: true });
});

const attributionLabel = (status: string) => {
    switch (status) {
        case "no_news":
            return "No recent catalyst";
        case "timed_out":
            return "Attribution timed out";
        case "news_unavailable":
            return "News unavailable";
        case "attribution_unavailable":
            return "AI attribution unavailable";
        default:
            return "";
    }
};

const marketCapFormatter = new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    notation: "compact",
    maximumFractionDigits: 2,
});

const formatMarketCap = (marketCap?: number | null) => {
    if (marketCap == null || !Number.isFinite(marketCap)) return "Unavailable";
    return marketCapFormatter.format(marketCap);
};

function TickerProfileHoverCard({ item }: { item: AnomalyReport }) {
    const tooltipId = `ticker-profile-${item.ticker.replace(/[^a-zA-Z0-9_-]/g, "-")}`;

    return (
        <span className="group relative inline-flex shrink-0">
            <Link
                href={`/?ticker=${encodeURIComponent(item.ticker)}`}
                aria-describedby={tooltipId}
                className="rounded-sm font-mono text-sm font-black text-emerald-600 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500 focus-visible:ring-offset-2 dark:text-emerald-400 dark:focus-visible:ring-offset-slate-900"
            >
                {item.ticker.replace(/\.US$/, "")}
            </Link>
            <span
                id={tooltipId}
                role="tooltip"
                className="pointer-events-none invisible absolute left-0 top-full z-50 mt-2 w-72 translate-y-1 rounded-xl border border-slate-200 bg-white p-4 text-left opacity-0 shadow-xl transition duration-150 group-hover:visible group-hover:translate-y-0 group-hover:opacity-100 group-focus-within:visible group-focus-within:translate-y-0 group-focus-within:opacity-100 sm:w-96 dark:border-slate-700 dark:bg-slate-900"
            >
                <span className="block text-[10px] font-bold uppercase tracking-[0.16em] text-slate-400">
                    Company snapshot
                </span>
                <span className="mt-1.5 flex items-start justify-between gap-4">
                    <span className="text-sm font-black text-slate-900 dark:text-slate-100">
                        {item.company_name}
                    </span>
                    <span className="shrink-0 rounded-md bg-emerald-50 px-2 py-1 text-[11px] font-black text-emerald-700 dark:bg-emerald-950/50 dark:text-emerald-300">
                        Market cap {formatMarketCap(item.market_cap)}
                    </span>
                </span>
                <span className="mt-3 block line-clamp-6 text-xs leading-5 text-slate-600 dark:text-slate-300">
                    {item.company_description || "Company profile is not available yet."}
                </span>
            </span>
        </span>
    );
}

export default function AnomaliesPage() {
    const {
        data,
        lastFetchTime,
        latestScan,
        setAnomalyScan,
    } = useAppStore();
    const [loading, setLoading] = useState(false);
    const [hydrating, setHydrating] = useState(true);
    const [activeScan, setActiveScan] = useState<AnomalyScan | null>(null);
    const [error, setError] = useState("");
    const controllerRef = useRef<AbortController | null>(null);

    useEffect(() => {
        const controller = new AbortController();
        controllerRef.current = controller;

        const loadLatest = async () => {
            try {
                const scan = await fetchLatestAnomalyScan(controller.signal);
                if (scan && !controller.signal.aborted) setAnomalyScan(scan);
            } catch (caught) {
                if (!isAbortError(caught)) {
                    setError(
                        caught instanceof Error
                            ? caught.message
                            : "Unable to load the latest anomaly scan.",
                    );
                }
            } finally {
                if (!controller.signal.aborted) setHydrating(false);
            }
        };

        void loadLatest();
        return () => {
            controller.abort();
            controllerRef.current?.abort();
        };
    }, [setAnomalyScan]);

    const runScan = async () => {
        controllerRef.current?.abort();
        const controller = new AbortController();
        controllerRef.current = controller;
        setLoading(true);
        setError("");
        const pollStartedAt = Date.now();

        try {
            let scan = await startAnomalyScan(controller.signal);
            setActiveScan(scan);

            while (scan.status === "queued" || scan.status === "running") {
                if (Date.now() - pollStartedAt > POLL_BUDGET_MS) {
                    throw new Error(
                        "The scan is still running in the background. Reload this page shortly to see the latest result.",
                    );
                }
                await waitForNextPoll(controller.signal);
                scan = await fetchAnomalyScan(scan.id, controller.signal);
                setActiveScan(scan);
            }

            if (scan.status === "failed") {
                throw new Error(scan.error_message || "Unable to complete the anomaly scan.");
            }
            setAnomalyScan(scan);
        } catch (caught) {
            if (isAbortError(caught)) return;
            setError(
                caught instanceof Error
                    ? caught.message
                    : "Unable to complete the anomaly scan.",
            );
        } finally {
            if (!controller.signal.aborted) {
                setLoading(false);
                setActiveScan(null);
            }
        }
    };

    const threshold = activeScan?.threshold_pct
        ?? latestScan?.threshold_pct
        ?? 4;
    const resultLimit = activeScan?.requested_limit
        ?? latestScan?.requested_limit
        ?? 20;

    return (
        <div className="app-page">
            <div className="mx-auto w-full max-w-5xl space-y-6 pb-12">
                <header className="flex flex-col justify-between gap-4 border-b pb-5 sm:flex-row sm:items-end">
                    <div>
                        <p className="eyebrow">On-demand market monitor</p>
                        <h1 className="page-title mt-1">Market Anomalies</h1>
                        <p className="page-description">
                            Scan large daily moves, then review a model-generated catalyst summary and its linked sources.
                        </p>
                    </div>
                    {lastFetchTime && (
                        <span className="status-pill">
                            <Clock3 size={13} />
                            Scanned {new Date(lastFetchTime).toLocaleTimeString([], {
                                hour: "2-digit",
                                minute: "2-digit",
                            })}
                        </span>
                    )}
                </header>

                <section className="surface-panel flex flex-col justify-between gap-5 p-5 sm:flex-row sm:items-center sm:p-6">
                    <div className="flex gap-4">
                        <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-emerald-50 text-emerald-600 dark:bg-emerald-950/40 dark:text-emerald-300">
                            <Radar size={22} />
                        </span>
                        <div>
                            <h2 className="font-black">Run a fresh scan</h2>
                            <p className="mt-1 max-w-2xl text-sm leading-6 text-slate-500">
                                Checks the tracked universe for moves of at least {threshold.toFixed(1)}%.
                                Shows the {resultLimit} largest qualifying moves. The scan runs in the
                                background and this page updates when it finishes.
                            </p>
                        </div>
                    </div>
                    <button
                        type="button"
                        className="primary-button shrink-0"
                        disabled={loading || hydrating}
                        onClick={runScan}
                    >
                        {loading
                            ? <Loader2 className="animate-spin" size={16} />
                            : latestScan
                                ? <RefreshCw size={16} />
                                : <Activity size={16} />}
                        {loading ? "Scanning…" : latestScan ? "Refresh scan" : "Run scan"}
                    </button>
                </section>

                {error && <div className="error-panel" role="alert">{error}</div>}

                {hydrating && !data.length && (
                    <section className="surface-panel flex min-h-[360px] flex-col items-center justify-center p-8 text-center">
                        <Loader2 className="animate-spin text-emerald-500" size={38} />
                        <h2 className="mt-4 text-lg font-black">Loading latest scan</h2>
                        <p className="mt-2 max-w-md text-sm leading-6 text-slate-500">
                            Reading the most recent completed result.
                        </p>
                    </section>
                )}

                {!hydrating && !loading && !data.length && !error && (
                    <section className="surface-panel flex min-h-[360px] flex-col items-center justify-center p-8 text-center">
                        <Activity className="text-slate-300 dark:text-slate-600" size={38} />
                        <h2 className="mt-4 text-lg font-black">
                            {latestScan ? "No qualifying moves in the latest scan" : "No completed scan yet"}
                        </h2>
                        <p className="mt-2 max-w-md text-sm leading-6 text-slate-500">
                            {latestScan
                                ? `No tracked stock moved at least ${threshold.toFixed(1)}% in the latest quote set.`
                                : "Run the scanner when you need a current anomaly review."}
                        </p>
                    </section>
                )}

                {loading && !data.length && (
                    <section className="surface-panel flex min-h-[360px] flex-col items-center justify-center p-8 text-center">
                        <Loader2 className="animate-spin text-emerald-500" size={38} />
                        <h2 className="mt-4 text-lg font-black">Scanning market moves</h2>
                        <p className="mt-2 max-w-md text-sm leading-6 text-slate-500">
                            {activeScan?.status === "queued"
                                ? "The scan is queued and will start shortly."
                                : "Comparing current quotes and preparing bounded, source-backed attribution."}
                        </p>
                    </section>
                )}

                {data.length > 0 && (
                    <section className="space-y-4" aria-busy={loading}>
                        {data.map((item) => {
                            const positive = item.price_change >= 0;
                            const statusLabel = attributionLabel(item.attribution_status);
                            return (
                                <article
                                    key={`${item.ticker}-${item.quote_timestamp}`}
                                    className="surface-panel overflow-visible"
                                >
                                    <header className="surface-subtle flex flex-col justify-between gap-3 rounded-t-2xl border-b p-4 sm:flex-row sm:items-center sm:px-5">
                                        <div className="min-w-0">
                                            <div className="flex items-center gap-3">
                                                <TickerProfileHoverCard item={item} />
                                                <span className="truncate text-sm font-semibold text-slate-600 dark:text-slate-300">
                                                    {item.company_name}
                                                </span>
                                            </div>
                                            <p className="mt-1 text-xs text-slate-500">
                                                Quote {new Date(item.quote_timestamp).toLocaleString()}
                                            </p>
                                        </div>
                                        <span className={`inline-flex w-fit items-center gap-1 rounded-lg px-3 py-1.5 font-mono text-base font-black ${positive ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300" : "bg-rose-50 text-rose-600 dark:bg-rose-950/30 dark:text-rose-300"}`}>
                                            {positive ? <ArrowUpRight size={17} /> : <ArrowDownRight size={17} />}
                                            {positive ? "+" : ""}
                                            {item.price_change.toFixed(2)}%
                                        </span>
                                    </header>
                                    <div className="p-5 sm:p-6">
                                        <div className="flex gap-3">
                                            <Sparkles className="mt-0.5 shrink-0 text-indigo-500" size={18} />
                                            <div className="min-w-0 flex-1">
                                                {statusLabel && (
                                                    <span className="status-pill mb-3">{statusLabel}</span>
                                                )}
                                                <p className="whitespace-pre-line text-sm leading-7 text-slate-700 dark:text-slate-300">
                                                    {item.ai_analysis}
                                                </p>
                                                {item.news?.length > 0 && (
                                                    <div className="mt-5 space-y-2 border-t pt-4">
                                                        {item.news.map((source, index) => (
                                                            <a
                                                                key={`${source.link}-${index}`}
                                                                href={source.link}
                                                                target="_blank"
                                                                rel="noopener noreferrer"
                                                                className="secondary-button flex min-h-10 w-full justify-between px-3 py-2 text-left text-xs"
                                                            >
                                                                <span className="min-w-0">
                                                                    <span className="block truncate font-bold">
                                                                        [{index + 1}] {source.title}
                                                                    </span>
                                                                    <span className="mt-0.5 block text-slate-500">
                                                                        {source.publisher} · {new Date(source.pub_date).toLocaleString()}
                                                                    </span>
                                                                </span>
                                                                <ExternalLink className="shrink-0" size={12} />
                                                            </a>
                                                        ))}
                                                    </div>
                                                )}
                                            </div>
                                        </div>
                                    </div>
                                </article>
                            );
                        })}
                    </section>
                )}
            </div>
        </div>
    );
}
