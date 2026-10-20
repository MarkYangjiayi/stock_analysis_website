import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { QualityScorecardResponse } from "@/lib/api";

const apiMocks = vi.hoisted(() => ({ fetchQualityScorecard: vi.fn() }));

vi.mock("@/lib/api", async (importOriginal) => ({
    ...await importOriginal<typeof import("@/lib/api")>(),
    ...apiMocks,
}));

import QualityScorecardPanel from "./QualityScorecardPanel";

const metric = (label: string, value: number | null, score: number | null, unit: "percent" | "ratio" = "percent") =>
    ({ label, value, score, unit, weak: 0, strong: 1 });

const card = (overrides: Partial<QualityScorecardResponse> = {}): QualityScorecardResponse => ({
    ticker: "AAPL.US",
    available: true,
    template: "standard",
    industry: "Consumer Electronics",
    score: 81,
    grade: "优",
    pillars: [
        { key: "profitability", label: "盈利能力", score: 100, metrics: ["roic_avg"], applicable: true },
        { key: "growth", label: "增长持续性", score: 66.2, metrics: ["revenue_cagr"], applicable: true },
        { key: "cash_conversion", label: "现金转化", score: 62.4, metrics: ["fcf_conversion"], applicable: true },
        { key: "balance_sheet", label: "资产负债表", score: 90, metrics: ["interest_coverage"], applicable: true },
        { key: "capital_allocation", label: "资本配置", score: 88.4, metrics: ["share_change"], applicable: true },
    ],
    metrics: {
        roic_avg: metric("ROIC 均值", 0.5372, 100),
        revenue_cagr: metric("收入复合增速", 0.0868, 72.3),
        fcf_conversion: metric("FCF / 净利润", 1.0546, 92),
        interest_coverage: metric("利息覆盖倍数", null, null, "ratio"),
        share_change: metric("股本变化", -0.144, 100),
    },
    piotroski: {
        score: 8,
        available: 9,
        checks: [
            { key: "roa_positive", label: "资产收益率为正", passed: true },
            { key: "cash_above_earnings", label: "经营现金流高于净利润", passed: false },
        ],
    },
    altman: { z: 12.59, zone: "safe", zone_label: "安全", fiscal_year: "2025-09-30" },
    fiscal_years: ["2020-09-30", "2025-09-30"],
    source: "EODHD fundamentals",
    fetched_at: "2026-10-16T08:00:00",
    notes: [],
    ...overrides,
});

describe("QualityScorecardPanel", () => {
    beforeEach(() => {
        apiMocks.fetchQualityScorecard.mockReset();
    });

    it("shows the score, pillars, F-score checks and Altman zone", async () => {
        apiMocks.fetchQualityScorecard.mockResolvedValue(card());
        render(<QualityScorecardPanel ticker="AAPL.US" />);

        const panel = await screen.findByRole("region", { name: "Quality scorecard" });
        expect(within(panel).getByText("81")).toHaveClass("text-up");
        expect(within(panel).getByText("优")).toBeInTheDocument();
        expect(within(panel).getByText("53.7%")).toBeInTheDocument();
        expect(within(panel).getByText("+8.7%")).toBeInTheDocument();
        expect(within(panel).getByText("−14.4%")).toBeInTheDocument();
        expect(within(panel).getByText("8 / 9")).toBeInTheDocument();
        expect(within(panel).getByText("12.59 · 安全")).toHaveClass("text-up");
        expect(apiMocks.fetchQualityScorecard).toHaveBeenCalledWith("AAPL.US", expect.any(AbortSignal));
    });

    it("marks pillars that do not apply and explains the template", async () => {
        apiMocks.fetchQualityScorecard.mockResolvedValue(card({
            template: "financial",
            industry: "Banks - Regional",
            pillars: [
                { key: "profitability", label: "盈利能力", score: 95.5, metrics: ["roic_avg"], applicable: true },
                { key: "cash_conversion", label: "现金转化", score: null, metrics: [], applicable: false },
            ],
            piotroski: null,
            altman: null,
            notes: ["银行、保险和资本市场公司用 ROE 衡量盈利能力。"],
        }));
        render(<QualityScorecardPanel ticker="JPM.US" />);

        expect(await screen.findByText("Financials template · Banks - Regional")).toBeInTheDocument();
        expect(screen.getAllByText("n/a")).toHaveLength(3);
        expect(screen.getByText("银行、保险和资本市场公司用 ROE 衡量盈利能力。")).toBeInTheDocument();
    });

    it("says when the scorecard cannot be loaded", async () => {
        apiMocks.fetchQualityScorecard.mockRejectedValue(new Error("Network down"));
        render(<QualityScorecardPanel ticker="AAPL.US" />);
        expect(await screen.findByText("Network down")).toBeInTheDocument();
    });
});
