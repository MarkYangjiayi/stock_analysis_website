import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import EventsExpectationsPanel from "@/components/EventsExpectationsPanel";
import type { EventsExpectationsResponse } from "@/lib/api";

const data: EventsExpectationsResponse = {
    ticker: "AAA.US",
    source: "EODHD",
    as_of: "2026-01-02T00:00:00",
    available: true,
    next_event: {
        id: "earnings-2026-03-31",
        kind: "earnings",
        status: "upcoming",
        title: "Upcoming earnings",
        event_date: "2026-04-21",
        period_end: "2026-03-31",
        timing: "AfterMarket",
        eps_estimate: 1.5,
    },
    upcoming_events: [
        {
            id: "earnings-2026-03-31",
            kind: "earnings",
            status: "upcoming",
            title: "Upcoming earnings",
            event_date: "2026-04-21",
            period_end: "2026-03-31",
            timing: "AfterMarket",
            eps_estimate: 1.5,
        },
    ],
    recent_earnings: [
        {
            id: "earnings-2025-12-31",
            kind: "earnings",
            status: "reported",
            title: "Earnings reported",
            event_date: "2026-01-20",
            period_end: "2025-12-31",
            eps_actual: 1.4,
            eps_estimate: 1.3,
            eps_surprise_percent: 7.69,
        },
    ],
    expectations: [
        {
            period: "+1q",
            label: "Next quarter",
            period_end: "2026-06-30",
            eps_average: 1.5,
            eps_low: 1.3,
            eps_high: 1.7,
            eps_growth: 0.12,
            revenue_average: 1_000_000_000,
            revenue_low: 950_000_000,
            revenue_high: 1_050_000_000,
            revenue_growth: 0.1,
            eps_analyst_count: 12,
            revenue_analyst_count: 10,
            eps_revisions_up_30d: 4,
            eps_revisions_down_30d: 1,
            eps_trend_current: 1.5,
            eps_trend_30d: 1.45,
        },
    ],
    wall_street_target_price: 140.5,
    dividend_yield: 0.02,
    annual_dividend_per_share: 1.2,
    data_quality_notes: [],
};

describe("EventsExpectationsPanel", () => {
    it("shows the next catalyst and consensus snapshot in the overview variant", () => {
        render(<EventsExpectationsPanel data={data} currency="USD" />);

        expect(screen.getByText("Events & expectations")).toBeInTheDocument();
        expect(screen.getByText("Upcoming earnings")).toBeInTheDocument();
        expect(screen.getByText("Consensus snapshot")).toBeInTheDocument();
        expect(screen.getByText("EPS 1.50")).toBeInTheDocument();
        expect(screen.getByText("Target $140.5")).toBeInTheDocument();
        expect(screen.queryByText("Forward consensus")).not.toBeInTheDocument();
    });

    it("shows event history and forward estimates in the evidence variant", () => {
        render(<EventsExpectationsPanel data={data} currency="USD" detail />);

        expect(screen.getByText("Upcoming events")).toBeInTheDocument();
        expect(screen.getByText("Recent earnings history")).toBeInTheDocument();
        expect(screen.getByText("Forward consensus")).toBeInTheDocument();
        expect(screen.getByText("Earnings reported")).toBeInTheDocument();
        expect(screen.getAllByText("Next quarter")).toHaveLength(2);
        expect(screen.getAllByText("Raised")).toHaveLength(2);
    });

    it("keeps missing provider data non-blocking", () => {
        render(<EventsExpectationsPanel data={{ ...data, available: false, next_event: null, expectations: [], upcoming_events: [], recent_earnings: [] }} />);

        expect(screen.getByText("No event or forward-consensus data is available for this ticker yet.")).toBeInTheDocument();
    });

    it("shows the Key-risk verdict when the shared signal block is present", () => {
        // ASTS on finbrain.icu (2026-10-11): a 30-day trend of 0.0% used to read "Flat".
        const signals = {
            as_of: "2026-10-11", eps_period_end: "2026-12-31", eps_estimate: -2.29, eps_estimate_30d_ago: -2.29, eps_estimate_90d_ago: -1.51,
            eps_revision_30d: 0.0001, eps_revision_90d: -0.517, eps_revisions_up_30d: 0, eps_revisions_down_30d: 4,
            eps_estimate_analysts: 8, eps_revision_breadth_30d: -0.5, eps_next_year_revision_30d: -0.084, eps_next_year_revision_90d: -0.843,
            shares_short: 64_828_174, shares_short_prior_month: 65_261_118, short_interest_change: -0.007, short_float: 0.217, short_ratio: 6.39,
        };
        render(<EventsExpectationsPanel data={{
            ...data,
            expectation_signals: {
                available: true, source: "EODHD fundamentals", as_of: "2026-10-11", max_age_days: 14, signals,
                estimate_assessed: true,
                estimate_signal: { direction: "cut", basis: "breadth", severity: "high" },
                short_signal: { kind: "crowded", severity: "warning" },
                notes: [],
            },
        }} currency="USD" />);

        expect(screen.getByText("Estimates cut · high")).toBeInTheDocument();
        expect(screen.getByText("4 of 8 analysts cut in 30 days")).toBeInTheDocument();
        expect(screen.getByText("Current FY 0.0% 30d · −51.7% 90d")).toBeInTheDocument();
        expect(screen.getByText("Next FY −8.4% 30d · −84.3% 90d")).toBeInTheDocument();
        expect(screen.getByText("Same rule as Key risks · as of Oct 11, 2026")).toBeInTheDocument();
        expect(screen.queryByText(/EPS trend vs 30 days ago/)).not.toBeInTheDocument();
    });

    it("says why the verdict is missing instead of calling it flat", () => {
        render(<EventsExpectationsPanel data={{
            ...data,
            expectation_signals: {
                available: false, source: "EODHD fundamentals", as_of: "2026-09-01", max_age_days: 14, signals: null,
                estimate_assessed: false, estimate_signal: null, short_signal: null,
                notes: ["Consensus and short-interest data are 47 days old; flags need data from the last 14 days."],
            },
        }} />);

        expect(screen.getByText("Not assessed")).toBeInTheDocument();
        expect(screen.getByText(/47 days old/)).toBeInTheDocument();
        expect(screen.queryByText("Flat")).not.toBeInTheDocument();
    });

    it("reads a wider expected loss as a lowered estimate", () => {
        render(<EventsExpectationsPanel data={{
            ...data,
            expectations: [{ ...data.expectations[0], eps_trend_current: -2.29, eps_trend_30d: -2.0 }],
        }} detail />);

        expect(screen.getAllByText("Lowered")).toHaveLength(2);
        expect(screen.getByText("EPS trend vs 30 days ago -14.5%")).toBeInTheDocument();
    });

    it("surfaces provider freshness notes in both cockpit views", () => {
        const staleData = {
            ...data,
            data_quality_notes: ["Provider snapshot is 9 days old; verify event and consensus data before relying on it."],
        };
        const { rerender } = render(<EventsExpectationsPanel data={staleData} />);
        expect(screen.getByRole("note")).toHaveTextContent("Provider snapshot is 9 days old");

        rerender(<EventsExpectationsPanel data={staleData} detail />);
        expect(screen.getByRole("note")).toHaveTextContent("Provider snapshot is 9 days old");
    });
});
