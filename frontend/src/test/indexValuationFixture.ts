import type { IndexValuationResponse } from "@/lib/api";

export const makeIndexValuationFixture = (
    overrides: Partial<IndexValuationResponse> = {},
): IndexValuationResponse => ({
    meta: {
        universe: "SP500",
        as_of_date: "2026-09-17",
        expected_as_of_date: "2026-09-17",
        published_at: "2026-09-18T04:15:00Z",
        stale: false,
        history_start: "2016-01-31",
        history_end: "2026-09-30",
        membership_mode: "point_in_time",
        history_basis: "reconstructed_estimates",
        price_basis: "split_only",
        warnings: [],
    },
    methodology: [
        "Membership is point-in-time: every month-end uses the index constituents whose provider membership interval covers that date.",
        "index_pe is the aggregate sum(equity) / sum(TTM earnings) including loss-makers.",
    ],
    points: [
        { date: "2026-07-31", index_pe: 27.8, index_pe_earners: 26.1, median_pe: 24.5, member_count: 503, covered_count: 498, loss_maker_count: 24, coverage_pct: 99.0, reason: null },
        { date: "2026-08-31", index_pe: null, index_pe_earners: null, median_pe: null, member_count: 503, covered_count: 210, loss_maker_count: 5, coverage_pct: 41.7, reason: "insufficient-coverage" },
        { date: "2026-09-30", index_pe: 28.4, index_pe_earners: 26.6, median_pe: 25.0, member_count: 503, covered_count: 499, loss_maker_count: 25, coverage_pct: 99.2, reason: null },
    ],
    stats: {
        months_total: 3,
        months_valid: 2,
        latest_date: "2026-09-30",
        latest_index_pe: 28.4,
        latest_index_pe_earners: 26.6,
        latest_median_pe: 25.0,
        median_index_pe: 28.1,
        min_index_pe: 27.8,
        max_index_pe: 28.4,
        average_coverage_pct: 80.0,
    },
    ...overrides,
});
