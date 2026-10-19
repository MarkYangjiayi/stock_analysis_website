import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, type TodayResponse } from "@/lib/api";

const apiMocks = vi.hoisted(() => ({ fetchToday: vi.fn() }));

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
    });

    it("shows the report regime, grouped movers, watchlist tags and the next seven days", async () => {
        apiMocks.fetchToday.mockResolvedValue(today());
        const { onSelectTicker } = renderDashboard();

        const regime = await screen.findByRole("region", { name: "Market regime" });
        expect(within(regime).getByText("震荡")).toHaveClass("text-fg-muted");
        expect(within(regime).getByText("走阔")).toHaveClass("text-caution");
        expect(within(regime).getByText("Unavailable: 估值")).toBeInTheDocument();
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
