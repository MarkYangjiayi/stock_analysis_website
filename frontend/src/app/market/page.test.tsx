import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { makeMarketOverviewFixture } from "@/test/marketFixture";

const apiMocks = vi.hoisted(() => ({
    fetchMarketOverview: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => ({
    ...await importOriginal<typeof import("@/lib/api")>(),
    fetchMarketOverview: apiMocks.fetchMarketOverview,
}));

vi.mock("@/components/MarketOverviewChart", () => ({
    default: ({ data, trendMode, lowerMetric }: {
        data: ReturnType<typeof makeMarketOverviewFixture>;
        trendMode: string;
        lowerMetric: string;
    }) => (
        <div
            data-testid="market-chart"
            data-sector-count={data.sector_trends.length}
            data-rsp-points={data.rsp_spy_index.length}
            data-mode={trendMode}
            data-lower-metric={lowerMetric}
        />
    ),
}));

import MarketOverviewPage from "./page";

describe("MarketOverviewPage", () => {
    beforeEach(() => {
        vi.clearAllMocks();
        apiMocks.fetchMarketOverview.mockResolvedValue(makeMarketOverviewFixture());
    });

    it("loads the default view and only refetches for universe or period", async () => {
        render(<MarketOverviewPage />);

        const chart = await screen.findByTestId("market-chart");
        expect(chart).toHaveAttribute("data-sector-count", "11");
        expect(chart).toHaveAttribute("data-rsp-points", "3");
        expect(chart).toHaveAttribute("data-mode", "relative");
        expect(chart).toHaveAttribute("data-lower-metric", "net_advances");
        expect(apiMocks.fetchMarketOverview).toHaveBeenCalledWith(
            "SP500",
            "1y",
            expect.any(AbortSignal),
        );

        fireEvent.click(screen.getByRole("button", { name: "Absolute" }));
        expect(screen.getByTestId("market-chart")).toHaveAttribute("data-mode", "absolute");
        expect(apiMocks.fetchMarketOverview).toHaveBeenCalledTimes(1);

        fireEvent.click(screen.getByRole("button", { name: "New highs / lows" }));
        expect(screen.getByTestId("market-chart")).toHaveAttribute("data-lower-metric", "new_high_low");
        expect(apiMocks.fetchMarketOverview).toHaveBeenCalledTimes(1);

        fireEvent.click(screen.getByRole("button", { name: "Russell 2000" }));
        await waitFor(() => expect(apiMocks.fetchMarketOverview).toHaveBeenCalledWith(
            "RUSSELL2000",
            "1y",
            expect.any(AbortSignal),
        ));

        fireEvent.click(screen.getByRole("button", { name: "3M" }));
        await waitFor(() => expect(apiMocks.fetchMarketOverview).toHaveBeenCalledWith(
            "RUSSELL2000",
            "3m",
            expect.any(AbortSignal),
        ));
    });

    it("shows stale and quality states from the last complete publication", async () => {
        apiMocks.fetchMarketOverview.mockResolvedValue(makeMarketOverviewFixture({
            stale: true,
            as_of_date: "2026-07-30",
            expected_as_of_date: "2026-07-31",
            warnings: ["minimum long-window eligibility is 84.00%"],
        }));

        render(<MarketOverviewPage />);

        expect(await screen.findByText(/Showing the last complete publication/)).toBeInTheDocument();
        expect(screen.getByText(/minimum long-window eligibility/)).toBeInTheDocument();
    });

    it("renders an error and retries the same request", async () => {
        apiMocks.fetchMarketOverview
            .mockRejectedValueOnce(new Error("Market overview has not been published yet"))
            .mockResolvedValueOnce(makeMarketOverviewFixture());

        render(<MarketOverviewPage />);

        expect(await screen.findByRole("alert")).toHaveTextContent("Market overview has not been published yet");
        fireEvent.click(screen.getByRole("button", { name: /Retry/ }));
        expect(await screen.findByTestId("market-chart")).toBeInTheDocument();
        expect(apiMocks.fetchMarketOverview).toHaveBeenCalledTimes(2);
    });
});
