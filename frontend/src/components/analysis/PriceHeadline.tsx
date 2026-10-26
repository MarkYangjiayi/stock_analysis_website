import { ArrowDownRight, ArrowUpRight } from "lucide-react";
import type { HistoricalDataPoint, LiveQuote } from "@/lib/api";
import { formatSignedPercent } from "@/lib/format";

export interface HeadlinePrice {
    price: number | null;
    change: number | null;
    changePct: number | null;
    label: string;
}

const newYorkTime = (iso: string, withSeconds: boolean) => {
    const parsed = new Date(iso);
    if (Number.isNaN(parsed.getTime())) return iso;
    return `${new Intl.DateTimeFormat("en-US", {
        timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", ...(withSeconds ? { second: "2-digit" } : {}), hourCycle: "h23",
    }).format(parsed)} ET`;
};

const quoteLabel = (quote: LiveQuote) => {
    if (quote.source === "realtime") return `Real-time · ${newYorkTime(quote.as_of, true)}`;
    if (quote.source === "delayed") return `Delayed 15 min · ${newYorkTime(quote.as_of, false)}`;
    return `Close · ${quote.session_date}`;
};

/** The live quote replaces the last daily bar only when it belongs to a later session. */
export const resolveHeadlinePrice = (
    latest: HistoricalDataPoint | undefined,
    previous: HistoricalDataPoint | undefined,
    quote: LiveQuote | null,
): HeadlinePrice => {
    if (quote && (!latest?.date || quote.session_date > latest.date)) {
        return { price: quote.price, change: quote.change, changePct: quote.change_pct, label: quoteLabel(quote) };
    }
    const change = latest?.close != null && previous?.close != null ? latest.close - previous.close : null;
    return {
        price: latest?.close ?? null,
        change,
        changePct: change != null && previous?.close ? change / previous.close : null,
        label: `Adjusted close · ${latest?.date || "Date unavailable"}`,
    };
};

export default function PriceHeadline({ currency, headline }: { currency: string; headline: HeadlinePrice }) {
    const { price, change, changePct, label } = headline;
    return (
        <div className="sm:text-right">
            <div className="flex items-baseline gap-1.5 sm:justify-end"><span className="font-mono text-xs text-fg-muted">{currency}</span><span className="font-mono text-2xl font-medium tracking-tight sm:text-[28px]">{price?.toFixed(2) ?? "—"}</span></div>
            {change != null && <p className={`mt-0.5 flex items-center gap-1 font-mono text-sm font-medium sm:justify-end ${change > 0 ? "text-up" : change < 0 ? "text-down" : "text-flat"}`}>{change > 0 ? <ArrowUpRight size={14} /> : change < 0 ? <ArrowDownRight size={14} /> : null}{change > 0 ? "+" : change < 0 ? "−" : ""}{Math.abs(change).toFixed(2)} {changePct == null ? "" : `(${formatSignedPercent(changePct)})`}</p>}
            <p className="mt-1 font-mono text-xs text-fg-muted">{label}</p>
        </div>
    );
}
