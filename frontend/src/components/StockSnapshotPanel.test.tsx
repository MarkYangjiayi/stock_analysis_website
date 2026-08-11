import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import StockSnapshotPanel, { formatSnapshotValue } from "@/components/StockSnapshotPanel";
import type { MarketSnapshotMetric, MarketSnapshotResponse } from "@/lib/api";

const metric = (
    value: MarketSnapshotMetric["value"],
    unit: MarketSnapshotMetric["unit"],
    overrides: Partial<MarketSnapshotMetric> = {},
): MarketSnapshotMetric => ({
    value,
    unit,
    source_date: "2026-08-07",
    unavailable_reason: value == null ? "Value is unavailable." : null,
    secondary_value: null,
    secondary_unit: null,
    percentile: null,
    percentile_scope: null,
    ...overrides,
});

const data: MarketSnapshotResponse = {
    ticker: "NET.US",
    currency: "USD",
    source_dates: {
        price: "2026-08-07",
        screener: "2026-08-07",
        financials: "2026-03-31",
        provider: "2026-08-07",
    },
    coverage: { available: 4, total: 73, ratio: 4 / 73 },
    metrics: {
        market_cap: metric(107_060_000_000, "currency"),
        forward_pe: metric(180.57, "multiple", { percentile: 4, percentile_scope: "industry" }),
        eps_growth_next_year: metric("0.4534000000", "percent"),
        operating_margin: metric(-0.0788, "percent"),
        performance_1w: metric(0.0763, "percent"),
        pe_ratio: metric(null, "multiple"),
        sma20_distance: metric(0.0846, "percent", { secondary_value: 276.81, secondary_unit: "currency" }),
    },
};

afterEach(() => vi.unstubAllGlobals());

describe("StockSnapshotPanel", () => {
    it("formats compact financial values and ratios", () => {
        expect(formatSnapshotValue(107_060_000_000, "currency", "USD")).toBe("$107.06B");
        expect(formatSnapshotValue(42.61, "multiple", "USD")).toBe("42.61×");
        expect(formatSnapshotValue(-0.0788, "percent", "USD")).toBe("−7.88%");
        expect(formatSnapshotValue("0.4534000000", "percent", "USD")).toBe("45.34%");
        expect(formatSnapshotValue("0.1360915781", "percent", "USD")).toBe("13.61%");
        expect(formatSnapshotValue("35.3400000000", "multiple", "USD")).toBe("35.34×");
        expect(formatSnapshotValue("Nasdaq-100", "text", "USD")).toBe("Nasdaq-100");
        expect(formatSnapshotValue(null, "number", "USD")).toBe("—");
    });

    it("renders dates, coverage, unavailable reasons, secondary values and percentiles", () => {
        render(<StockSnapshotPanel data={data} loading={false} error="" onRetry={vi.fn()} />);

        expect(screen.getByText("Market Snapshot")).toBeInTheDocument();
        expect(screen.getByText("4/73 · 5% covered")).toBeInTheDocument();
        expect(screen.getByText("$107.06B")).toBeInTheDocument();
        expect(screen.getByText("180.57×")).toBeInTheDocument();
        expect(screen.getByText("P4")).toHaveAttribute("title", "industry desirability percentile");
        expect(screen.getByText("$276.81")).toBeInTheDocument();
        expect(screen.getByTestId("snapshot-metric-pe_ratio").querySelector("[title='Value is unavailable.']")).toBeTruthy();
    });

    it("uses semantic colors only for signed fields", () => {
        render(<StockSnapshotPanel data={data} loading={false} error="" onRetry={vi.fn()} />);

        expect(screen.getByText("7.63%")).toHaveClass("text-emerald-600");
        expect(screen.getByText("−7.88%")).toHaveClass("text-rose-500");
        expect(screen.getByText("180.57×")).not.toHaveClass("text-rose-500");
        expect(screen.getByText("45.34%")).toHaveClass("text-emerald-600");
    });

    it("allows mobile groups to expand and keeps desktop content mounted", async () => {
        const user = userEvent.setup();
        render(<StockSnapshotPanel data={data} loading={false} error="" onRetry={vi.fn()} />);

        const growth = screen.getByRole("button", { name: /Growth/ });
        expect(growth).toHaveAttribute("aria-expanded", "false");
        await user.click(growth);
        expect(growth).toHaveAttribute("aria-expanded", "true");
    });

    it("disables disclosure controls when desktop content is forced open", () => {
        vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({
            matches: true,
            media: "(min-width: 768px)",
            onchange: null,
            addListener: vi.fn(),
            removeListener: vi.fn(),
            addEventListener: vi.fn(),
            removeEventListener: vi.fn(),
            dispatchEvent: vi.fn(),
        }));
        render(<StockSnapshotPanel data={data} loading={false} error="" onRetry={vi.fn()} />);

        const growth = screen.getByRole("button", { name: /Growth/ });
        expect(growth).toBeDisabled();
        expect(growth).toHaveAttribute("aria-expanded", "true");
    });

    it("keeps snapshot failures isolated and retryable", async () => {
        const user = userEvent.setup();
        const onRetry = vi.fn();
        render(<StockSnapshotPanel data={null} loading={false} error="Snapshot failed." onRetry={onRetry} />);

        expect(screen.getByRole("alert")).toHaveTextContent("Snapshot failed.");
        await user.click(screen.getByRole("button", { name: /Retry/ }));
        expect(onRetry).toHaveBeenCalledOnce();
    });

    it("keeps a retry action when stale snapshot data remains visible", async () => {
        const user = userEvent.setup();
        const onRetry = vi.fn();
        render(<StockSnapshotPanel data={data} loading={false} error="Refresh failed." onRetry={onRetry} />);

        expect(screen.getByText(/previous snapshot remains visible/i)).toBeInTheDocument();
        await user.click(screen.getByRole("button", { name: /Retry/ }));
        expect(onRetry).toHaveBeenCalledOnce();
    });
});
