"use client";

import { useEffect, useMemo, useState } from "react";
import { fetchWatchlistQuotes, type MarketStatusResponse, type WatchlistQuoteItem } from "@/lib/api";

const MAX_TICKERS = 20;
const LIVE_POLL_MS = 60_000;
const IDLE_POLL_MS = 10 * 60_000;
const CLOSE_SETTLE_MS = 30 * 60_000;

/** Poll fast only while delayed quotes can still move: market hours plus the settle window after close. */
export const quotePollInterval = (market: MarketStatusResponse | null, now = Date.now()) => {
    if (!market) return LIVE_POLL_MS;
    if (market.phase === "open") return LIVE_POLL_MS;
    const closesAt = market.closes_at ? Date.parse(market.closes_at) : NaN;
    if (Number.isFinite(closesAt) && now >= closesAt && now - closesAt < CLOSE_SETTLE_MS) return LIVE_POLL_MS;
    return IDLE_POLL_MS;
};

export function useWatchlistQuotes(tickers: string[]) {
    const key = useMemo(() => [...new Set(tickers)].slice(0, MAX_TICKERS).join(","), [tickers]);
    const [result, setResult] = useState<{ key: string; quotes: Record<string, WatchlistQuoteItem>; market: MarketStatusResponse | null }>({ key: "", quotes: {}, market: null });

    useEffect(() => {
        if (!key) return;
        const symbols = key.split(",");
        let timer: ReturnType<typeof setTimeout> | undefined;
        let controller: AbortController | undefined;
        let market: MarketStatusResponse | null = null;
        let stopped = false;

        const schedule = (delay: number) => {
            clearTimeout(timer);
            if (!stopped && !document.hidden) timer = setTimeout(() => void load(), delay);
        };
        const load = async () => {
            controller?.abort();
            controller = new AbortController();
            try {
                const response = await fetchWatchlistQuotes(symbols, controller.signal);
                market = response.market;
                setResult({ key, market, quotes: Object.fromEntries(response.items.map((item) => [item.ticker, item])) });
            } catch (error) {
                if (error instanceof DOMException && error.name === "AbortError") return;
                // Keep showing the previous snapshot; the next poll retries.
            }
            schedule(quotePollInterval(market));
        };
        const onVisibility = () => {
            if (document.hidden) clearTimeout(timer);
            else void load();
        };

        void load();
        document.addEventListener("visibilitychange", onVisibility);
        return () => {
            stopped = true;
            clearTimeout(timer);
            controller?.abort();
            document.removeEventListener("visibilitychange", onVisibility);
        };
    }, [key]);

    // Drop data from a previous ticker set instead of showing mismatched rows.
    return result.key === key ? result.quotes : {};
}
