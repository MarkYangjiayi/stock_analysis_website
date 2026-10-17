"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Loader2, RotateCcw, TriangleAlert } from "lucide-react";

import RRGChart, { type RRGResponse } from "@/components/RRGChart";
import { apiRequest, type ThemeListItem } from "@/lib/api";
import { displayTicker } from "./themeFormat";

// /api/v1/rrg accepts at most 50 tickers per request.
const MAX_TICKERS = 50;

/** Relative rotation of each theme's primary proxy ETF against SPY. */
export default function ThemeRotation({ themes }: { themes: ThemeListItem[] }) {
    const proxies = useMemo(() => {
        const byEtf = new Map<string, string[]>();
        for (const theme of themes) {
            if (!theme.proxy_etf) continue;
            byEtf.set(theme.proxy_etf, [...(byEtf.get(theme.proxy_etf) ?? []), theme.name_en]);
        }
        return [...byEtf.entries()].slice(0, MAX_TICKERS);
    }, [themes]);
    const tickers = proxies.map(([etf]) => etf).join(",");
    const [data, setData] = useState<RRGResponse | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");
    const [retryKey, setRetryKey] = useState(0);

    const load = useCallback(async (signal?: AbortSignal) => {
        if (!tickers) return;
        setLoading(true);
        setError("");
        try {
            setData(await apiRequest<RRGResponse>(
                `/api/v1/rrg?tickers=${encodeURIComponent(tickers)}&benchmark=SPY.US&history_days=252`,
                { signal },
                90_000,
            ));
        } catch (caught) {
            if (caught instanceof DOMException && caught.name === "AbortError") return;
            setData(null);
            setError(caught instanceof Error ? caught.message : "Unable to load proxy ETF rotation.");
        } finally {
            if (!signal?.aborted) setLoading(false);
        }
    }, [tickers]);

    useEffect(() => {
        const controller = new AbortController();
        void load(controller.signal);
        return () => controller.abort();
    }, [load, retryKey]);

    if (!tickers) {
        return <div className="flex h-[360px] items-center justify-center px-6 text-center text-sm text-fg-muted">No theme in this view has a proxy ETF with prices.</div>;
    }

    // Proxy ETFs can legitimately lack history (new launches), so a partial response still renders.
    const missing = data?.missing_tickers ?? [];
    const plotted = new Set(Object.keys(data?.data ?? {}).map(displayTicker));

    return (
        <div>
            {missing.length > 0 && (
                <div className="m-4 rounded-lg border border-caution/20 bg-caution/5 px-4 py-3 text-sm text-caution" role="status">
                    <TriangleAlert className="mr-2 inline" size={16} aria-hidden="true" />
                    No rotation history for {missing.map(displayTicker).join(", ")}. Those proxies are left off the chart.
                </div>
            )}
            {error && (
                <div className="error-panel m-4 flex items-center justify-between gap-4" role="alert">
                    <span>{error}</span>
                    <button type="button" className="secondary-button shrink-0" onClick={() => setRetryKey((value) => value + 1)}>
                        <RotateCcw size={15} /> Retry
                    </button>
                </div>
            )}
            {loading ? (
                <div className="flex h-[520px] flex-col items-center justify-center">
                    <Loader2 className="animate-spin text-accent" size={34} />
                    <p className="mt-4 text-sm text-fg-muted">Calculating proxy ETF trajectories…</p>
                </div>
            ) : data && !error && <RRGChart data={data} tailLength={10} />}
            <ul className="grid gap-x-6 gap-y-1 border-t px-4 py-3 text-xs text-fg-muted sm:grid-cols-2 xl:grid-cols-3" aria-label="Proxy ETF key">
                {proxies.map(([etf, names]) => (
                    <li key={etf} className={`flex min-w-0 gap-2 ${data && !plotted.has(displayTicker(etf)) ? "opacity-50" : ""}`}>
                        <span className="w-12 shrink-0 font-mono font-semibold text-fg">{displayTicker(etf)}</span>
                        <span className="truncate">{names.join(" · ")}</span>
                    </li>
                ))}
            </ul>
        </div>
    );
}
