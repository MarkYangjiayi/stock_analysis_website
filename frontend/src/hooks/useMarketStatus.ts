"use client";

import { useEffect, useState } from "react";
import { fetchMarketStatus, type MarketStatusResponse } from "@/lib/api";

const MAX_REFRESH_MS = 5 * 60_000;

/** Refresh at the next known session boundary (open, close, next open) or every five minutes. */
export const nextStatusRefresh = (status: MarketStatusResponse | null, now = Date.now()) => {
    const boundaries = [status?.opens_at, status?.closes_at, status?.next_open]
        .map((value) => (value ? Date.parse(value) : NaN))
        .filter((time) => Number.isFinite(time) && time > now)
        .map((time) => time - now + 1_000);
    return Math.min(MAX_REFRESH_MS, ...boundaries);
};

export function useMarketStatus() {
    const [status, setStatus] = useState<MarketStatusResponse | null>(null);

    useEffect(() => {
        let timer: ReturnType<typeof setTimeout> | undefined;
        let controller: AbortController | undefined;
        let latest: MarketStatusResponse | null = null;

        const load = async () => {
            controller?.abort();
            controller = new AbortController();
            try {
                latest = await fetchMarketStatus(controller.signal);
                setStatus(latest);
            } catch (error) {
                if (error instanceof DOMException && error.name === "AbortError") return;
            }
            clearTimeout(timer);
            if (!document.hidden) timer = setTimeout(() => void load(), nextStatusRefresh(latest));
        };
        const onVisibility = () => {
            if (document.hidden) clearTimeout(timer);
            else void load();
        };

        void load();
        document.addEventListener("visibilitychange", onVisibility);
        return () => {
            clearTimeout(timer);
            controller?.abort();
            document.removeEventListener("visibilitychange", onVisibility);
        };
    }, []);

    return status;
}
