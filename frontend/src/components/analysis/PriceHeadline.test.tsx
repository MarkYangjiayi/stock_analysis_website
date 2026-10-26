import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import PriceHeadline, { resolveHeadlinePrice } from "@/components/analysis/PriceHeadline";
import { livePollInterval } from "@/hooks/useLiveQuote";
import type { HistoricalDataPoint, LiveQuote, MarketStatusResponse } from "@/lib/api";

const bar = (date: string, close: number): HistoricalDataPoint => ({ date, open: close, high: close, low: close, close, volume: 1 });
const friday = bar("2026-10-23", 1810.15);
const thursday = bar("2026-10-22", 1824.955);

const quote = (overrides: Partial<LiveQuote>): LiveQuote => ({
    price: 1635.87,
    previous_close: 1810.15,
    change: 1635.87 - 1810.15,
    change_pct: 1635.87 / 1810.15 - 1,
    as_of: "2026-10-26T15:20:31Z",
    session_date: "2026-10-26",
    source: "realtime",
    ...overrides,
});

const status = (overrides: Partial<MarketStatusResponse>): MarketStatusResponse => ({
    phase: "closed",
    session_date: "2026-10-26",
    opens_at: "2026-10-26T13:30:00Z",
    closes_at: "2026-10-26T20:00:00Z",
    next_open: "2026-10-27T13:30:00Z",
    as_of: "2026-10-26T15:00:00Z",
    ...overrides,
});

describe("analysis header price", () => {
    it("shows the streamed trade with its New York time", () => {
        render(<PriceHeadline currency="USD" headline={resolveHeadlinePrice(friday, thursday, quote({}))} />);
        expect(screen.getByText("1635.87")).toBeInTheDocument();
        expect(screen.getByText("Real-time · 11:20:31 ET")).toBeInTheDocument();
        expect(screen.getByText(/−174\.28 \(−9\.63%\)/)).toHaveClass("text-down");
    });

    it("labels the delayed fallback and the session close", () => {
        expect(resolveHeadlinePrice(friday, thursday, quote({ source: "delayed", as_of: "2026-10-26T15:05:00Z" })).label)
            .toBe("Delayed 15 min · 11:05 ET");
        expect(resolveHeadlinePrice(friday, thursday, quote({ source: "close", as_of: "2026-10-26T19:59:00Z" })).label)
            .toBe("Close · 2026-10-26");
    });

    it("keeps the daily bar when the quote is not newer than it", () => {
        const headline = resolveHeadlinePrice(friday, thursday, quote({ session_date: "2026-10-23" }));
        expect(headline.price).toBe(1810.15);
        expect(headline.label).toBe("Adjusted close · 2026-10-23");
        expect(headline.change).toBeCloseTo(1810.15 - 1824.955);
        expect(resolveHeadlinePrice(friday, thursday, null).label).toBe("Adjusted close · 2026-10-23");
    });

    it("polls fast in the session, while the close settles, and otherwise waits for the open", () => {
        expect(livePollInterval(status({ phase: "open" }))).toBe(3_000);
        expect(livePollInterval(status({ phase: "post" }), Date.parse("2026-10-26T20:10:00Z"))).toBe(30_000);
        expect(livePollInterval(status({ phase: "post" }), Date.parse("2026-10-26T21:00:00Z"))).toBe(600_000);
        expect(livePollInterval(status({ phase: "pre" }), Date.parse("2026-10-27T13:25:00Z"))).toBe(301_000);
        expect(livePollInterval(null)).toBe(30_000);
    });
});
