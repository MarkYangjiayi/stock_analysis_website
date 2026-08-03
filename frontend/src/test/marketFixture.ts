import type { MarketOverviewResponse } from "@/lib/api";

const SECTORS = [
    ["XLK.US", "Technology"],
    ["XLF.US", "Financials"],
    ["XLV.US", "Health Care"],
    ["XLY.US", "Cons. Discret."],
    ["XLP.US", "Cons. Staples"],
    ["XLE.US", "Energy"],
    ["XLI.US", "Industrials"],
    ["XLB.US", "Materials"],
    ["XLU.US", "Utilities"],
    ["XLRE.US", "Real Estate"],
    ["XLC.US", "Comm. Svcs"],
] as const;

export function makeMarketOverviewFixture(
    overrides: Partial<MarketOverviewResponse["meta"]> = {},
): MarketOverviewResponse {
    const dates = ["2026-07-29", "2026-07-30", "2026-07-31"];
    return {
        meta: {
            universe: "SP500",
            period: "1y",
            as_of_date: "2026-07-31",
            expected_as_of_date: "2026-07-31",
            published_at: "2026-08-01T07:30:00Z",
            stale: false,
            membership_mode: "point_in_time",
            data_complete: true,
            warnings: [],
            ...overrides,
        },
        dates,
        benchmark: { ticker: "SPY.US", absolute_index: [100, 101, 102] },
        sector_trends: SECTORS.map(([ticker, label], index) => ({
            ticker,
            label,
            absolute_index: [100, 100.5 + index / 10, 101 + index / 10],
            relative_to_spy_index: [100, 99.5 + index / 10, 99 + index / 10],
        })),
        rsp_spy_index: [100, 100.2, 100.4],
        breadth: {
            pct_above_ma20: [51, 52, 53],
            pct_above_ma50: [50, 51, 52],
            pct_above_ma200: [48, 49, 50],
            net_advances_pct: [12, -8, 20],
            new_high_low_pct: [2, -1, 3],
            new_high_pct: [4, 2, 5],
            new_low_pct: [2, 3, 2],
            mcclellan: [null, 1.2, 2.4],
            dispersion_1d: [0.012, 0.014, 0.011],
            dispersion_20d: [0.013, 0.0131, 0.0129],
            member_count: [500, 500, 500],
            price_coverage_pct: [99.2, 99.4, 99.6],
        },
    };
}
