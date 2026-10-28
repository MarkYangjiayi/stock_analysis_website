"use client";

import { useEffect, useState } from "react";
import { fetchLiveQuote, type LiveQuoteResponse, type MarketStatusResponse } from "@/lib/api";

const LIVE_POLL_MS = 3_000;
const SETTLE_POLL_MS = 30_000;
const IDLE_POLL_MS = 10 * 60_000;
// The official close is published about 20 minutes after the bell (35 at worst, from the delayed feed).
const CLOSE_SETTLE_MS = 45 * 60_000;

/** Every 3 s in the session, every 30 s while the close settles, otherwise until the next open (at most 10 min). */
export const livePollInterval = (market: MarketStatusResponse | null, now = Date.now()) => {
    if (!market) return SETTLE_POLL_MS;
    if (market.phase === "open") return LIVE_POLL_MS;
    const closesAt = market.closes_at ? Date.parse(market.closes_at) : NaN;
    if (Number.isFinite(closesAt) && now >= closesAt && now - closesAt < CLOSE_SETTLE_MS) return SETTLE_POLL_MS;
    const nextOpen = Date.parse(market.next_open);
    if (Number.isFinite(nextOpen) && nextOpen > now) return Math.min(IDLE_POLL_MS, nextOpen - now + 1_000);
    return IDLE_POLL_MS;
};

/** Polled header quote for one ticker; each poll also renews the worker's stream lease. */
export function useLiveQuote(ticker: string | null): LiveQuoteResponse | null {
    const [result, setResult] = useState<{ ticker: string; data: LiveQuoteResponse | null }>({ ticker: "", data: null });

    useEffect(() => {
        if (!ticker) return;
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
            const current = new AbortController();
            controller = current;
            try {
                const data = await fetchLiveQuote(ticker, current.signal);
                market = data.market;
                setResult({ ticker, data });
            } catch (error) {
                if (error instanceof DOMException && error.name === "AbortError") return;
                // Keep the previous quote; the next poll retries.
            }
            schedule(livePollInterval(market));
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
    }, [ticker]);

    // Never show the previous ticker's price while the new one loads.
    return result.ticker === ticker ? result.data : null;
}
