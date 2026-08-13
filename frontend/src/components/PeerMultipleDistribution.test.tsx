import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import PeerMultipleDistribution from "@/components/PeerMultipleDistribution";
import type { PeerMultiplesResponse } from "@/lib/api";

const apiMocks = vi.hoisted(() => ({
    fetchPeerMultiples: vi.fn(),
}));

vi.mock("@/lib/api", async (importOriginal) => ({
    ...(await importOriginal<typeof import("@/lib/api")>()),
    ...apiMocks,
}));

vi.mock("echarts-for-react", () => ({
    default: ({ option }: { option: unknown }) => <div data-testid="peer-bars">{JSON.stringify(option)}</div>,
}));

const available: PeerMultiplesResponse = {
    available: true,
    reason: null,
    metric: { key: "ps_ratio", label: "Price / sales", format: "multiple" },
    as_of_date: "2026-07-20",
    target: {
        ticker: "NET.US",
        name: "Cloudflare",
        value: 44.1,
        market_cap: 150_000_000_000,
        sales_growth_ttm: 0.2136,
        raw_percentile: 92,
        premium_to_median: 2.02,
    },
    cohort: {
        scope: "industry",
        name: "Software - Infrastructure",
        member_count: 36,
        valid_count: 32,
        excluded_count: 4,
        minimum_observations: 10,
    },
    distribution: { mean: 17.3, median: 14.6, p10: 4.8, p25: 8.7, p75: 21.2, p90: 31.5 },
    peers: [{ ticker: "SNOW.US", name: "Snowflake", value: 22.9, market_cap: 80_000_000_000, sales_growth_ttm: 0.1934 }],
};

describe("PeerMultipleDistribution", () => {
    beforeEach(() => {
        vi.clearAllMocks();
        apiMocks.fetchPeerMultiples.mockResolvedValue(available);
    });

    it("loads P/S with automatic scope and renders raw distribution context", async () => {
        render(<PeerMultipleDistribution ticker="NET.US" />);

        await waitFor(() => expect(apiMocks.fetchPeerMultiples).toHaveBeenCalledWith(
            "NET.US",
            "ps_ratio",
            "auto",
            expect.any(AbortSignal),
        ));
        expect(await screen.findByText("44.1×")).toBeInTheDocument();
        expect(screen.getByText("Higher than 92.0% of valid peers")).toBeInTheDocument();
        expect(screen.getByText("+202.0%")).toBeInTheDocument();
        expect(screen.getByText(/32 valid peers/)).toBeInTheDocument();
        expect(screen.getByTestId("peer-bars")).toBeInTheDocument();
    });

    it("aborts stale requests when the metric changes", async () => {
        const signals: AbortSignal[] = [];
        apiMocks.fetchPeerMultiples.mockImplementation((
            _ticker: string,
            _metric: string,
            _scope: string,
            signal: AbortSignal,
        ) => {
            signals.push(signal);
            return new Promise<PeerMultiplesResponse>(() => undefined);
        });
        const user = userEvent.setup();
        render(<PeerMultipleDistribution ticker="NET.US" />);
        await waitFor(() => expect(signals).toHaveLength(1));

        await user.selectOptions(screen.getByRole("combobox", { name: "Peer multiple" }), "pe_ratio");
        await waitFor(() => expect(signals).toHaveLength(2));
        expect(signals[0].aborted).toBe(true);
        expect(apiMocks.fetchPeerMultiples).toHaveBeenLastCalledWith(
            "NET.US",
            "pe_ratio",
            "auto",
            expect.any(AbortSignal),
        );
    });

    it("shows a bounded unavailable state without rendering a zero multiple", async () => {
        apiMocks.fetchPeerMultiples.mockResolvedValue({
            ...available,
            available: false,
            reason: "target_metric_unavailable",
            target: { ...available.target, value: null, raw_percentile: null, premium_to_median: null },
            distribution: null,
            peers: [],
        });
        render(<PeerMultipleDistribution ticker="LOSS.US" />);

        expect(await screen.findByText(/underlying denominator is not positive/)).toBeInTheDocument();
        expect(screen.queryByText("0.0×")).not.toBeInTheDocument();
    });
});
