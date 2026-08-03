import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { makeMarketOverviewFixture } from "@/test/marketFixture";

vi.mock("next-themes", () => ({
    useTheme: () => ({ resolvedTheme: "light" }),
}));

vi.mock("echarts-for-react", () => ({
    default: ({ option }: { option: Record<string, unknown> }) => {
        const series = option.series as Array<{ name: string; xAxisIndex: number }>;
        const topNames = series
            .filter((item) => item.xAxisIndex === 0)
            .map((item) => item.name);
        const grids = option.grid as unknown[];
        const xAxes = option.xAxis as unknown[];
        const yAxes = option.yAxis as Array<{ min?: number; max?: number }>;
        const axisPointer = option.axisPointer as { link?: Array<{ xAxisIndex?: string }> };
        const tooltip = option.tooltip as {
            formatter?: (params: Array<{ dataIndex: number }>) => string;
        };
        return (
            <div
                data-testid="echarts"
                data-top-series={JSON.stringify(topNames)}
                data-grid-count={grids.length}
                data-x-axis-count={xAxes.length}
                data-breadth-range={`${yAxes[1].min}-${yAxes[1].max}`}
                data-linked={axisPointer.link?.[0]?.xAxisIndex}
                data-tooltip={tooltip.formatter?.([{ dataIndex: 0 }]) || ""}
            />
        );
    },
}));

import MarketOverviewChart from "./MarketOverviewChart";

describe("MarketOverviewChart", () => {
    it("renders eleven sector lines plus RSP/SPY on three linked timelines", () => {
        render(
            <MarketOverviewChart
                data={makeMarketOverviewFixture()}
                trendMode="relative"
                lowerMetric="net_advances"
            />,
        );

        const chart = screen.getByTestId("echarts");
        const topSeries = JSON.parse(chart.getAttribute("data-top-series") || "[]") as string[];
        expect(topSeries).toHaveLength(12);
        expect(topSeries).toContain("XLK.US");
        expect(topSeries).toContain("XLC.US");
        expect(topSeries).toContain("RSP/SPY");
        expect(topSeries).not.toContain("SPY");
        expect(chart).toHaveAttribute("data-grid-count", "3");
        expect(chart).toHaveAttribute("data-x-axis-count", "3");
        expect(chart).toHaveAttribute("data-breadth-range", "0-100");
        expect(chart).toHaveAttribute("data-linked", "all");
    });

    it("adds SPY without replacing the fixed RSP/SPY proxy in absolute mode", () => {
        render(
            <MarketOverviewChart
                data={makeMarketOverviewFixture()}
                trendMode="absolute"
                lowerMetric="mcclellan"
            />,
        );

        const topSeries = JSON.parse(
            screen.getByTestId("echarts").getAttribute("data-top-series") || "[]",
        ) as string[];
        expect(topSeries).toHaveLength(13);
        expect(topSeries).toContain("SPY");
        expect(topSeries).toContain("RSP/SPY");
        expect(screen.getByTestId("echarts").getAttribute("data-tooltip")).toContain(">SPY<");
    });
});
