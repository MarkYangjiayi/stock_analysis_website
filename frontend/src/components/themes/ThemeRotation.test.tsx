import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { RRGResponse } from "@/components/RRGChart";
import { makeInsufficientTheme, makeThemeList, makeThemeListItem } from "@/test/themesFixture";

const apiMocks = vi.hoisted(() => ({ apiRequest: vi.fn() }));

vi.mock("@/lib/api", async (importOriginal) => ({
    ...await importOriginal<typeof import("@/lib/api")>(),
    apiRequest: apiMocks.apiRequest,
}));

vi.mock("@/components/RRGChart", () => ({
    default: ({ data, tailLength }: { data: RRGResponse; tailLength: number }) => (
        <div data-testid="rrg-chart" data-tickers={Object.keys(data.data).join(",")} data-tail={tailLength} />
    ),
}));

import ThemeRotation from "./ThemeRotation";

const point = { date: "2026-10-09", rs_ratio: 101.2, rs_momentum: 99.4 };

describe("ThemeRotation", () => {
    beforeEach(() => {
        vi.clearAllMocks();
    });

    it("plots each distinct proxy ETF once and warns about proxies without history", async () => {
        apiMocks.apiRequest.mockResolvedValue({
            benchmark: "SPY.US",
            update_time: "2026-10-10T09:00:00Z",
            data_complete: false,
            missing_tickers: ["QTUM.US"],
            data: { "SMH.US": [point], "NLR.US": [point] },
        } satisfies RRGResponse);
        const themes = [
            ...makeThemeList(),
            makeThemeListItem({ slug: "ai-networking", name_en: "AI networking", proxy_etf: "SMH.US" }),
        ];
        render(<ThemeRotation themes={themes} />);

        expect(await screen.findByTestId("rrg-chart")).toHaveAttribute("data-tickers", "SMH.US,NLR.US");
        expect(apiMocks.apiRequest).toHaveBeenCalledTimes(1);
        expect(apiMocks.apiRequest).toHaveBeenCalledWith(
            `/api/v1/rrg?tickers=${encodeURIComponent("SMH.US,NLR.US,QTUM.US")}&benchmark=SPY.US&history_days=252`,
            { signal: expect.any(AbortSignal) },
            90_000,
        );
        expect(screen.getByRole("status")).toHaveTextContent("No rotation history for QTUM. Those proxies are left off the chart.");

        const key = screen.getByRole("list", { name: "Proxy ETF key" });
        expect(within(key).getByText("SMH").closest("li")).toHaveTextContent("AI compute · AI networking");
        expect(within(key).getByText("QTUM").closest("li")).toHaveClass("opacity-50");
    });

    it("does not request rotation data when no theme has a proxy", () => {
        render(<ThemeRotation themes={[makeInsufficientTheme()]} />);

        expect(screen.getByText("No theme in this view has a proxy ETF with prices.")).toBeInTheDocument();
        expect(apiMocks.apiRequest).not.toHaveBeenCalled();
    });

    it("retries after a failed request", async () => {
        apiMocks.apiRequest
            .mockRejectedValueOnce(new Error("Failed to calculate RRG data"))
            .mockResolvedValueOnce({ benchmark: "SPY.US", update_time: "", data: { "SMH.US": [point] } });
        render(<ThemeRotation themes={[makeThemeListItem()]} />);

        expect(await screen.findByRole("alert")).toHaveTextContent("Failed to calculate RRG data");
        fireEvent.click(screen.getByRole("button", { name: /Retry/ }));
        expect(await screen.findByTestId("rrg-chart")).toHaveAttribute("data-tickers", "SMH.US");
        expect(apiMocks.apiRequest).toHaveBeenCalledTimes(2);
    });
});
