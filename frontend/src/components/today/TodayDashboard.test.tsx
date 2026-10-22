import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, type MarketStatusResponse, type TodayResponse, type WatchlistQuotesResponse } from "@/lib/api";

const apiMocks = vi.hoisted(() => ({ fetchToday: vi.fn(), fetchWatchlistQuotes: vi.fn() }));

vi.mock("@/lib/api", async (importOriginal) => ({
    ...await importOriginal<typeof import("@/lib/api")>(),
    ...apiMocks,
}));

import TodayDashboard from "./TodayDashboard";

const today = (overrides: Partial<TodayResponse> = {}): TodayResponse => ({
    generated_at: "2026-10-19T15:00:00Z",
    report: {
        run_id: 65,
        report_type: "post_market_summary",
        report_date: "2026-10-16",
        captured_at: "2026-10-16T20:30:00+00:00",
        status: "delivered",
        delivered: true,
        regime: {
            items: [
                { key: "trend", label: "趋势", state: "震荡", tone: "grey", detail: "SPY 距 200 日线 +5.7%", as_of: "2026-10-16" },
                { key: "credit", label: "信用", state: "走阔", tone: "orange", detail: "高收益债利差 3.23%，一月 +0.47pt", as_of: "2026-10-15" },
            ],
            unavailable: ["估值"],
        },
        regime_captured_at: "2026-10-16T20:30:00+00:00",
        anomaly_clusters: {
            as_of: "2026-10-16",
            movers: 3,
            clusters: [{ kind: "theme", name: "AI 新云 / 算力租赁", slug: "neocloud", direction: "down",
                tickers: ["NBIS.US", "CRWV.US", "APLD.US"], average_move: -10.77 }],
        },
        movers: [
            { ticker: "VICR.US", company_name: "Vicor", price_change: -12.47, quote_timestamp: null, news_title: "Vicor slides after guidance", news_link: "https://example.com/vicr" },
            { ticker: "NBIS.US", company_name: "Nebius", price_change: -11.64, quote_timestamp: null, news_title: null, news_link: null },
        ],
    },
    watchlist: {
        target: "2026-10-16",
        items: [
            { ticker: "ASTS.US", tags: [{ text: "预期下修 4/8家", color: "carmine" }, { text: "空头占流通 22%", color: "purple" }], as_of: "截至 2026-10-16" },
            { ticker: "UNH.US", tags: [], as_of: "" },
        ],
    },
    events: {
        from: "2026-10-19",
        to: "2026-10-25",
        window_days: 7,
        economic_available: true,
        earnings_available: true,
        items: [
            { kind: "economic", name: "Retail Sales MoM", date: "2026-10-20 12:30:00", period: "Sep", estimate: 0.4 },
            { kind: "earnings", name: "ASTS.US", date: "2026-10-21", session: "AfterMarket" },
        ],
        timezone_note: "经济日历时间按供应商原文展示，接口未提供时区；财报日期按上市市场。",
    },
    unavailable: [],
    ...overrides,
});

const quotes = {
    "ASTS.US": { ticker: "ASTS.US", quote: { price: 50.12, previous_close: 52, change: -1.88, change_pct: -0.0362, as_of: "2026-10-19T19:00:00Z", session_date: "2026-10-19" }, sparkline: [] },
};

const indexQuote = (ticker: string, price: number, previous: number, sessionDate = "2026-10-19") => ({
    ticker,
    quote: { price, previous_close: previous, change: price - previous, change_pct: (price - previous) / previous,
        as_of: `${sessionDate}T15:22:00Z`, session_date: sessionDate },
    sparkline: [],
});

const indexQuotes = (phase: MarketStatusResponse["phase"] = "open", sessionDate = "2026-10-19"): WatchlistQuotesResponse => ({
    market: { phase, session_date: "2026-10-19", opens_at: "2026-10-19T13:30:00Z", closes_at: "2026-10-19T20:00:00Z",
        next_open: "2026-10-20T13:30:00Z", as_of: "2026-10-19T15:37:00Z" },
    delay_minutes: 15,
    // Mega caps fall while the equal-weight index edges up.
    items: [
        indexQuote("SPY.US", 764.26, 773.09, sessionDate),
        indexQuote("QQQ.US", 709.21, 725.2, sessionDate),
        indexQuote("RSP.US", 217.03, 216.64, sessionDate),
        indexQuote("VIX.INDX", 18.7, 16.68, sessionDate),
    ],
});

const renderDashboard = (props: Partial<Parameters<typeof TodayDashboard>[0]> = {}) => {
    const onSelectTicker = vi.fn();
    const onUnauthorized = vi.fn();
    render(<TodayDashboard adminKey="test-key" watchlist={["ASTS.US", "UNH.US"]} quotes={quotes}
        onSelectTicker={onSelectTicker} onUnauthorized={onUnauthorized} {...props} />);
    return { onSelectTicker, onUnauthorized };
};

describe("TodayDashboard", () => {
    beforeEach(() => {
        apiMocks.fetchToday.mockReset();
        apiMocks.fetchWatchlistQuotes.mockReset();
        apiMocks.fetchWatchlistQuotes.mockResolvedValue(indexQuotes());
    });

    it("shows the report regime, grouped movers, watchlist tags and the next seven days", async () => {
        apiMocks.fetchToday.mockResolvedValue(today());
        const { onSelectTicker } = renderDashboard();

        const regime = await screen.findByRole("region", { name: "Market regime" });
        expect(within(regime).getByText("震荡")).toHaveClass("text-fg-muted");
        expect(within(regime).getByText("走阔")).toHaveClass("text-caution");
        expect(within(regime).getByText("Unavailable: 估值")).toBeInTheDocument();
        // The badge names the close behind the price readings, not the capture time.
        expect(within(regime).getByText("As of 10/16 close")).toBeInTheDocument();
        expect(within(regime).getByText(/Rule-based states on daily closes, captured 10\/16 16:30 ET/)).toBeInTheDocument();
        expect(apiMocks.fetchToday).toHaveBeenCalledWith("test-key", expect.any(AbortSignal));

        const movers = screen.getByRole("region", { name: "Movers by theme" });
        expect(within(movers).getByText("AI 新云 / 算力租赁")).toBeInTheDocument();
        expect(within(movers).getByText("−10.8%")).toHaveClass("text-down");
        // A grouped stock is listed once, inside its group.
        expect(within(movers).getAllByText("NBIS")).toHaveLength(1);
        expect(within(movers).getByText("Vicor slides after guidance")).toHaveAttribute("title", "Headline lead, not a verified cause");

        const table = screen.getByRole("table", { name: "Watchlist signals" });
        const asts = within(table).getByText("ASTS").closest("tr") as HTMLElement;
        expect(within(asts).getByText("预期下修 4/8家")).toHaveClass("text-down");
        expect(within(asts).getByText("空头占流通 22%")).toHaveClass("text-caution");
        expect(within(asts).getByText("−3.62%")).toHaveClass("text-down");
        expect(within(asts).getByText(/10\/21 · after close/)).toBeInTheDocument();

        const events = screen.getByRole("region", { name: "Next seven days" });
        expect(within(events).getByText("Retail Sales MoM")).toBeInTheDocument();
        expect(within(events).getByText("12:30 · Sep · est. 0.4")).toBeInTheDocument();
        expect(within(events).getByText(/接口未提供时区/)).toBeInTheDocument();

        fireEvent.click(within(movers).getByText("VICR"));
        expect(onSelectTicker).toHaveBeenCalledWith("VICR.US");
    });

    it("shows today's delayed index moves under the close-based states", async () => {
        apiMocks.fetchToday.mockResolvedValue(today());
        renderDashboard();

        const moves = await screen.findByRole("group", { name: "Index moves" });
        expect(within(moves).getByText("今日盘中")).toBeInTheDocument();
        expect(within(moves).getByText("11:22 ET")).toBeInTheDocument();
        expect(within(moves).getByText("−1.14%")).toHaveClass("text-down");
        expect(within(moves).getByText("−2.20%")).toHaveClass("text-down");
        expect(within(moves).getByText("+0.18%")).toHaveClass("text-up");
        // VIX shows its level; its rise is not coloured as a gain.
        expect(within(moves).getByText("+12.1%")).toHaveClass("text-fg-muted");
        expect(apiMocks.fetchWatchlistQuotes).toHaveBeenCalledWith(["SPY.US", "QQQ.US", "RSP.US", "VIX.INDX"], expect.any(AbortSignal));

        fireEvent.click(screen.getByRole("button", { name: /Refresh/ }));
        await waitFor(() => expect(apiMocks.fetchWatchlistQuotes).toHaveBeenCalledTimes(2));
        expect(apiMocks.fetchToday).toHaveBeenCalledTimes(2);
    });

    // Before the open, and just after it while the delayed feed still carries the previous close.
    it.each(["pre", "open"] as const)("labels an earlier session's index moves by their close date (%s)", async (phase) => {
        apiMocks.fetchToday.mockResolvedValue(today());
        apiMocks.fetchWatchlistQuotes.mockResolvedValue(indexQuotes(phase, "2026-10-16"));
        renderDashboard();

        const moves = await screen.findByRole("group", { name: "Index moves" });
        expect(within(moves).getByText("10/16 收盘")).toBeInTheDocument();
        expect(within(moves).queryByText(/ET$/)).not.toBeInTheDocument();
    });

    it("explains an empty start and names unavailable sections", async () => {
        apiMocks.fetchToday.mockResolvedValue(today({ report: null, events: null, unavailable: ["未来 7 天日历"] }));
        renderDashboard();

        expect(await screen.findByText("No report with a market regime has been collected yet.")).toBeInTheDocument();
        expect(screen.getByText("The calendar is unavailable right now.")).toBeInTheDocument();
        expect(screen.getByText("Unavailable: 未来 7 天日历")).toBeInTheDocument();
    });

    it("locks the workspace when the key is rejected", async () => {
        apiMocks.fetchToday.mockRejectedValue(new ApiError("Invalid API key", 401));
        const { onUnauthorized } = renderDashboard();
        await waitFor(() => expect(onUnauthorized).toHaveBeenCalledTimes(1));
    });
});
