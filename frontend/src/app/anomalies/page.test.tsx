import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AnomalyScan } from "@/lib/api";

const apiMocks = vi.hoisted(() => ({
    fetchAnomalyScan: vi.fn(),
    fetchLatestAnomalyScan: vi.fn(),
    startAnomalyScan: vi.fn(),
}));

vi.mock("@/lib/api", () => apiMocks);

import AnomaliesPage from "./page";
import { useAppStore } from "@/store/useAppStore";

const completedScan: AnomalyScan = {
    id: 7,
    trigger: "manual",
    status: "completed",
    requested_limit: 20,
    threshold_pct: 4,
    universe_as_of: "2026-07-29",
    quote_as_of: "2026-07-30T15:30:00Z",
    results: [{
        ticker: "BE.US",
        company_name: "Bloom Energy",
        date: "2026-07-30",
        quote_timestamp: "2026-07-30T15:30:00Z",
        price_change: 25.73,
        ai_analysis: "Earnings and an analyst upgrade drove the move [1].",
        attribution_status: "completed",
        news: [{
            title: "Bloom Energy earnings",
            link: "https://finance.yahoo.com/bloom",
            pub_date: "2026-07-30T14:00:00Z",
            summary: "Results exceeded expectations.",
            publisher: "Example News",
        }],
        top_news_links: ["https://finance.yahoo.com/bloom"],
    }],
    error_message: null,
    created_at: "2026-07-30T15:29:00Z",
    started_at: "2026-07-30T15:29:01Z",
    finished_at: "2026-07-30T15:30:00Z",
};

describe("AnomaliesPage", () => {
    beforeEach(() => {
        vi.clearAllMocks();
        useAppStore.setState({
            data: [],
            lastFetchTime: null,
            latestScan: null,
        });
    });

    it("loads cached state cheaply and renders a completed manual scan", async () => {
        apiMocks.fetchLatestAnomalyScan.mockResolvedValue(null);
        apiMocks.startAnomalyScan.mockResolvedValue(completedScan);

        render(<AnomaliesPage />);

        expect(
            await screen.findByRole("heading", { name: "No completed scan yet" }),
        ).toBeInTheDocument();
        expect(apiMocks.startAnomalyScan).not.toHaveBeenCalled();

        const button = screen.getByRole("button", { name: "Run scan" });
        await waitFor(() => expect(button).toBeEnabled());
        fireEvent.click(button);

        expect(
            await screen.findByText("Bloom Energy"),
        ).toBeInTheDocument();
        expect(screen.getByText("+25.73%")).toBeInTheDocument();
        expect(
            screen.getByText(/Shows the 20 largest qualifying moves/),
        ).toBeInTheDocument();
        expect(
            screen.getByRole("link", { name: /Bloom Energy earnings/ }),
        ).toHaveAttribute("href", "https://finance.yahoo.com/bloom");
        expect(apiMocks.startAnomalyScan).toHaveBeenCalledTimes(1);
        expect(apiMocks.fetchAnomalyScan).not.toHaveBeenCalled();
    });
});
