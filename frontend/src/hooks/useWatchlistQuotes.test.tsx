import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import WatchlistSidebar from "@/components/WatchlistSidebar";
import { nextStatusRefresh } from "@/hooks/useMarketStatus";
import { quotePollInterval } from "@/hooks/useWatchlistQuotes";
import type { MarketStatusResponse } from "@/lib/api";

const status = (overrides: Partial<MarketStatusResponse>): MarketStatusResponse => ({
    phase: "closed",
    session_date: "2026-10-06",
    opens_at: "2026-10-06T13:30:00Z",
    closes_at: "2026-10-06T20:00:00Z",
    next_open: "2026-10-07T13:30:00Z",
    as_of: "2026-10-06T15:00:00Z",
    ...overrides,
});

describe("watchlist quote polling", () => {
    it("polls every minute while quotes can move and slowly otherwise", () => {
        expect(quotePollInterval(status({ phase: "open" }))).toBe(60_000);
        expect(quotePollInterval(status({ phase: "post" }), Date.parse("2026-10-06T20:10:00Z"))).toBe(60_000);
        expect(quotePollInterval(status({ phase: "post" }), Date.parse("2026-10-06T21:00:00Z"))).toBe(600_000);
        expect(quotePollInterval(status({ phase: "closed" }), Date.parse("2026-10-07T02:00:00Z"))).toBe(600_000);
    });

    it("refreshes market status at the next session boundary", () => {
        const now = Date.parse("2026-10-06T13:29:00Z");
        expect(nextStatusRefresh(status({ phase: "pre" }), now)).toBe(61_000);
        expect(nextStatusRefresh(status({ phase: "closed" }), Date.parse("2026-10-07T02:00:00Z"))).toBe(300_000);
        expect(nextStatusRefresh(null)).toBe(300_000);
    });
});

describe("WatchlistSidebar quotes", () => {
    it("shows delayed change, price and trend for quoted tickers", () => {
        render(
            <WatchlistSidebar
                currentTicker="AAPL.US"
                onSelectTicker={() => undefined}
                watchlist={["AAPL.US", "UNH.US"]}
                onAdd={() => undefined}
                onRemove={() => undefined}
                readOnly
                quotes={{
                    "AAPL.US": {
                        ticker: "AAPL.US",
                        quote: { price: 150, previous_close: 139, change: 11, change_pct: 11 / 139, as_of: "2026-10-06T14:45:00Z", session_date: "2026-10-06" },
                        sparkline: [{ date: "2026-10-05", close: 139 }, { date: "2026-10-06", close: 150 }],
                    },
                    "UNH.US": { ticker: "UNH.US", quote: null, sparkline: [] },
                }}
            />,
        );
        expect(screen.getByText("+7.91%")).toHaveClass("text-up");
        expect(screen.getByText("150.00")).toBeInTheDocument();
        expect(screen.getByText("Quotes delayed ~15 min")).toBeInTheDocument();
        expect(screen.getByRole("button", { name: /UNH\.US/ })).toHaveTextContent("—");
    });
});
