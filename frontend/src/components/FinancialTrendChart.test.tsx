import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import FinancialTrendChart from "@/components/FinancialTrendChart";
import type { EarningsQualityResponse } from "@/lib/api";

const chartState = vi.hoisted(() => ({ props: null as null | Record<string, unknown> }));

vi.mock("echarts-for-react", () => ({
    default: (props: Record<string, unknown>) => {
        chartState.props = props;
        return <div data-testid="chart" />;
    },
}));
vi.mock("next-themes", () => ({ useTheme: () => ({ resolvedTheme: "light" }) }));

const earningsQuality = (verified: boolean): EarningsQualityResponse => ({
    ticker: "AAA.US",
    currency: "USD",
    methodology: {
        materiality_base: "base",
        warning_threshold: 0.1,
        high_threshold: 0.25,
        reported_remains_primary: true,
        structured_flags_are_adjustments: false,
    },
    summary: {
        verdict: "flags_present",
        evaluated_periods: 1,
        flagged_periods: 1,
        data_quality_periods: 0,
        financial_industry_exemption: false,
        message: "signal",
    },
    annual: [{
        period_end: "2025-12-31",
        period_type: "annual",
        reported: {
            revenue: 1_000,
            net_income: 100,
            income_before_tax: 125,
            net_income_from_continuing_operations: 100,
            income_tax_expense: 25,
            non_recurring: -25,
            extraordinary_items: 0,
            discontinued_operations: 0,
            non_operating_income_net_other: 0,
        },
        materiality_base: 125,
        thresholds: { warning: 0.1, high: 0.25 },
        flags: [{
            category: "impairment",
            label: "Impairment",
            amount: -25,
            materiality_ratio: 0.2,
            severity: "warning",
            source: "structured_financial_data",
            source_field: "nonRecurring",
            detail: "Candidate only.",
            treatment: "flag_only",
            recurring_adjustment: false,
        }],
        data_quality_warnings: [],
        assessment: "material_candidates",
        statement_fingerprint: "a",
        analysis: null,
        verified_normalized: verified ? { net_income: 120, adjusted_eps: null } : null,
    }],
    quarterly: [],
    sec_analysis: { supported: true, cik: "1", reason: null, supported_forms: ["10-K"], unsupported_forms: [] },
});

const chartData = [{
    date: "2025-12-31",
    revenue: 1_000,
    net_income: 100,
    gross_margin: 0.5,
}];

describe("FinancialTrendChart earnings-quality overlay", () => {
    it("keeps reported bars and only adds a normalized line after verification", () => {
        const { rerender } = render(<FinancialTrendChart data={chartData} timePeriod="annual" onTimePeriodChange={vi.fn()} selectedMetric="net_income" earningsQuality={earningsQuality(false)} />);
        let option = chartState.props?.option as { series: Array<{ name: string; data?: unknown[] }> };
        expect(option.series.some((series) => series.name === "Net income")).toBe(true);
        expect(option.series.some((series) => series.name === "Verified normalized net income")).toBe(false);
        expect(option.series.some((series) => series.name === "Earnings-quality flag")).toBe(true);

        rerender(<FinancialTrendChart data={chartData} timePeriod="annual" onTimePeriodChange={vi.fn()} selectedMetric="net_income" earningsQuality={earningsQuality(true)} />);
        option = chartState.props?.option as { series: Array<{ name: string; data?: unknown[] }> };
        const normalized = option.series.find((series) => series.name === "Verified normalized net income");
        expect(normalized?.data).toEqual([120]);
    });

    it("opens period detail when a flag marker is clicked", () => {
        render(<FinancialTrendChart data={chartData} timePeriod="annual" onTimePeriodChange={vi.fn()} earningsQuality={earningsQuality(false)} />);
        const events = chartState.props?.onEvents as { click: (params: Record<string, unknown>) => void };
        act(() => events.click({ seriesName: "Earnings-quality flag", data: { periodEnd: "2025-12-31" } }));
        expect(screen.getByRole("complementary", { name: /2025-12-31/i })).toBeInTheDocument();
        expect(screen.getByText("Impairment")).toBeInTheDocument();
    });
});
