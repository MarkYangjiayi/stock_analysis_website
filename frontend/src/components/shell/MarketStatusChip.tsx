"use client";

import { useMarketStatus } from "@/hooks/useMarketStatus";
import type { MarketPhase } from "@/lib/api";

const PHASES: Record<MarketPhase, { label: string; dot: string; text: string }> = {
    open: { label: "Market open", dot: "bg-up", text: "text-up" },
    pre: { label: "Pre-market", dot: "bg-accent", text: "text-accent" },
    post: { label: "After hours", dot: "bg-accent", text: "text-accent" },
    closed: { label: "Market closed", dot: "bg-flat", text: "text-fg-muted" },
};

const ET_TIME = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
});

export default function MarketStatusChip() {
    const status = useMarketStatus();
    if (!status) return null;
    const phase = PHASES[status.phase];
    const detail = status.phase === "open" && status.closes_at
        ? `Closes ${ET_TIME.format(new Date(status.closes_at))} ET`
        : `Opens ${ET_TIME.format(new Date(status.next_open))} ET`;
    return (
        <span className={`status-pill hidden shrink-0 border-line bg-surface md:inline-flex ${phase.text}`} title={`NYSE · ${detail}`} aria-label={`NYSE ${phase.label}. ${detail}`}>
            <span className={`h-1.5 w-1.5 rounded-full ${phase.dot}`} aria-hidden="true" />
            {phase.label}
        </span>
    );
}
