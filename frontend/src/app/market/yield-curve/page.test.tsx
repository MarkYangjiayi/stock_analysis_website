import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { TreasuryYieldCurveResponse } from "@/lib/api";

const apiMocks = vi.hoisted(() => ({
    fetchTreasuryYieldCurve: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => ({
    ...await importOriginal<typeof import("@/lib/api")>(),
    fetchTreasuryYieldCurve: apiMocks.fetchTreasuryYieldCurve,
}));

vi.mock("@/components/YieldCurveCharts", () => ({
    CurrentYieldCurveChart: () => <div data-testid="current-curve-chart" />,
    YieldHistoryChart: ({ selectedMaturities }: { selectedMaturities: string[] }) => (
        <div data-testid="yield-history-chart" data-selected={selectedMaturities.join(",")} />
    ),
    YieldSpreadChart: () => <div data-testid="yield-spread-chart" />,
}));

const yields = (short: number, two: number, ten: number, thirty: number) => ({
    "1m": short,
    "6w": short,
    "2m": short,
    "3m": short,
    "4m": short,
    "6m": short,
    "1y": two,
    "2y": two,
    "3y": two,
    "5y": ten,
    "7y": ten,
    "10y": ten,
    "20y": thirty,
    "30y": thirty,
});

const fixture = (overrides: Partial<TreasuryYieldCurveResponse["meta"]> = {}): TreasuryYieldCurveResponse => ({
    meta: {
        period: "1y",
        as_of_date: "2026-09-11",
        fetched_at: "2026-09-14T12:00:00Z",
        source_name: "U.S. Department of the Treasury",
        provider_name: "EODHD",
        source_url: "https://home.treasury.gov/example",
        stale: false,
        warnings: [],
        ...overrides,
    },
    maturities: [
        ["3m", "3M", 0.25],
        ["6m", "6M", 0.5],
        ["1y", "1Y", 1],
        ["2y", "2Y", 2],
        ["3y", "3Y", 3],
        ["5y", "5Y", 5],
        ["7y", "7Y", 7],
        ["10y", "10Y", 10],
        ["20y", "20Y", 20],
        ["30y", "30Y", 30],
    ].map(([key, label, years]) => ({ key: String(key), label: String(label), years: Number(years) })),
    latest: { date: "2026-09-11", yields: yields(4.07, 4.63, 4.96, 5.35) },
    snapshots: [
        { key: "latest", label: "Latest", date: "2026-09-11", yields: yields(4.07, 4.63, 4.96, 5.35) },
        { key: "1m", label: "1M ago", date: "2026-08-12", yields: yields(3.90, 4.40, 4.80, 5.10) },
        { key: "3m", label: "3M ago", date: "2026-06-12", yields: yields(3.80, 4.20, 4.60, 4.90) },
        { key: "1y", label: "1Y ago", date: "2025-09-11", yields: yields(4.10, 4.00, 4.30, 4.55) },
    ],
    observations: [
        { date: "2025-09-11", yields: yields(4.10, 4.00, 4.30, 4.55) },
        { date: "2026-09-11", yields: yields(4.07, 4.63, 4.96, 5.35) },
    ],
});

import TreasuryYieldCurvePage from "./page";

describe("TreasuryYieldCurvePage", () => {
    beforeEach(() => {
        vi.clearAllMocks();
        apiMocks.fetchTreasuryYieldCurve.mockResolvedValue(fixture());
    });

    it("loads current, historical, and spread views", async () => {
        render(<TreasuryYieldCurvePage />);

        expect(await screen.findByTestId("current-curve-chart")).toBeInTheDocument();
        expect(screen.getByTestId("yield-history-chart")).toHaveAttribute("data-selected", "3m,2y,10y,30y");
        expect(screen.getByTestId("yield-spread-chart")).toBeInTheDocument();
        expect(screen.getByText("+0.33 pp")).toBeInTheDocument();
        expect(screen.getByText("Upward sloping")).toBeInTheDocument();
        expect(apiMocks.fetchTreasuryYieldCurve).toHaveBeenCalledWith("1y", expect.any(AbortSignal));

        const fiveYearButtons = screen.getAllByRole("button", { name: "5Y" });
        fireEvent.click(fiveYearButtons[0]);
        await waitFor(() => expect(apiMocks.fetchTreasuryYieldCurve).toHaveBeenCalledWith(
            "5y",
            expect.any(AbortSignal),
        ));

        fireEvent.click(fiveYearButtons[1]);
        expect(screen.getByTestId("yield-history-chart")).toHaveAttribute("data-selected", "3m,2y,10y,30y,5y");
    });

    it("shows cached-data warnings and retries provider errors", async () => {
        apiMocks.fetchTreasuryYieldCurve.mockResolvedValueOnce(fixture({
            stale: true,
            warnings: ["Treasury refresh failed; showing the last cached observations."],
        }));
        const { unmount } = render(<TreasuryYieldCurvePage />);
        expect(await screen.findByText(/last cached observations/)).toBeInTheDocument();
        unmount();

        apiMocks.fetchTreasuryYieldCurve
            .mockRejectedValueOnce(new Error("Treasury is offline"))
            .mockResolvedValueOnce(fixture());
        render(<TreasuryYieldCurvePage />);
        expect(await screen.findByRole("alert")).toHaveTextContent("Treasury is offline");
        fireEvent.click(screen.getByRole("button", { name: /Retry/ }));
        expect(await screen.findByTestId("current-curve-chart")).toBeInTheDocument();
    });
});
