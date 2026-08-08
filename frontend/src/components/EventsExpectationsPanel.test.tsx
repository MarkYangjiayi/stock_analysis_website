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
});
