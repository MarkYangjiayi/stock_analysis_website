import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { chartTheme } from "@/lib/chartTheme";
import { makeThemeHistory } from "@/test/themesFixture";

vi.mock("next-themes", () => ({
    useTheme: () => ({ resolvedTheme: "dark" }),
}));

interface SeriesOption {
    name: string;
    type: string;
    stack?: string;
    xAxisIndex: number;
    data: Array<number | null>;
    markArea?: { data: Array<[{ xAxis: string; name?: string; itemStyle: { color: string } }, { xAxis: string }]> };
}

vi.mock("echarts-for-react", () => ({
    default: ({ option }: {
        option: {
            grid: unknown[];
            series: SeriesOption[];
            axisPointer: { link: Array<{ xAxisIndex: string }> };
            dataZoom: Array<{ zoomOnMouseWheel?: boolean }>;
            tooltip: { formatter: (params: Array<{ dataIndex: number }>) => string };
        };
    }) => {
        const byName = Object.fromEntries(option.series.map((series) => [series.name, series]));
        const bands = byName["Heat now"].markArea?.data ?? [];
        return (
            <div
                data-testid="echarts"
                data-grids={option.grid.length}
                data-series={JSON.stringify(option.series.map((series) => [series.name, series.type, series.xAxisIndex, series.stack ?? null]))}
                data-price={JSON.stringify(byName.Price.data)}
                data-attention={JSON.stringify(byName.Attention.data)}
                data-basket={JSON.stringify(byName["Theme basket"].data)}
                data-proxy={JSON.stringify(byName.SMH?.data ?? null)}
                data-b50={JSON.stringify(byName["% > MA50"].data)}
                data-bands={JSON.stringify(bands.map(([start, end]) => [start.name, start.xAxis, end.xAxis, start.itemStyle.color]))}
                data-linked={option.axisPointer.link[0].xAxisIndex}
                data-wheel-zoom={String(option.dataZoom[0].zoomOnMouseWheel)}
                data-tooltip-first={option.tooltip.formatter([{ dataIndex: 0 }])}
                data-tooltip-last={option.tooltip.formatter([{ dataIndex: 5 }])}
            />
        );
    },
}));

import ThemeDetailChart from "./ThemeDetailChart";

const attr = (name: string) => JSON.parse(screen.getByTestId("echarts").getAttribute(name) ?? "null");

describe("ThemeDetailChart", () => {
    it("links four panels with weighted, stacked components and keeps gaps as gaps", () => {
        const history = makeThemeHistory(6);
        render(<ThemeDetailChart history={history} proxyEtf="SMH.US" />);

        const chart = screen.getByTestId("echarts");
        expect(chart).toHaveAttribute("data-grids", "4");
        expect(chart).toHaveAttribute("data-linked", "all");
        expect(chart).toHaveAttribute("data-wheel-zoom", "false");
        expect(attr("data-series")).toEqual([
            ["Heat now", "line", 0, null],
            ["Heat trend", "line", 0, null],
            ["Price", "bar", 1, "components"],
            ["Breadth", "bar", 1, "components"],
            ["Volume", "bar", 1, "components"],
            ["Attention", "bar", 1, "components"],
            ["Theme basket", "line", 2, null],
            ["SPY", "line", 2, null],
            ["SMH", "line", 2, null],
            ["% > MA50", "line", 3, null],
            ["Near 52W high", "line", 3, null],
            ["Ignitions", "bar", 3, null],
        ]);

        // Components are weighted by their share of heat (P 40%, A 15%); missing stays null.
        const price = attr("data-price") as Array<number | null>;
        expect(price[0]).toBeCloseTo(0.2);
        expect(price[1]).toBeNull();
        expect((attr("data-attention") as Array<number | null>).at(-1)).toBeNull();

        // Indices are rebased returns in percent; fractions become percentage points.
        const basket = attr("data-basket") as number[];
        expect(basket[0]).toBeCloseTo(0);
        expect(basket[5]).toBeCloseTo((history.basket_index[5]! - 1) * 100);
        expect(basket[5]).toBeGreaterThan(10);
        expect((attr("data-proxy") as Array<number | null>)[2]).toBeNull();
        expect((attr("data-b50") as number[])[0]).toBeCloseTo(60);
    });

    it("shades stage runs and flags backfilled sessions in the tooltip", () => {
        const history = makeThemeHistory(6);
        const colors = chartTheme(true);
        render(<ThemeDetailChart history={history} proxyEtf={null} />);

        // Each band starts one step early, so neighbouring runs tile without an unshaded strip.
        expect(attr("data-bands")).toEqual([
            ["Accelerating", history.dates[1], history.dates[3], colors.positive],
            ["Leading", history.dates[3], history.dates[5], colors.brand],
        ]);

        const first = screen.getByTestId("echarts").getAttribute("data-tooltip-first") ?? "";
        expect(first).toContain(history.dates[0]);
        expect(first).toContain("backfilled");
        expect(first).toContain("Neutral");

        const last = screen.getByTestId("echarts").getAttribute("data-tooltip-last") ?? "";
        expect(last).not.toContain("backfilled");
        expect(last).toMatch(/Attention<\/span><b>—/);
        expect(last).toContain("Proxy ETF");
    });

    it("gives a one-session stage run a visible band, including today's stage change", () => {
        const base = makeThemeHistory(6);
        const colors = chartTheme(true);
        const history = { ...base, stage: [...Array(5).fill("neutral"), "emerging"] as typeof base.stage };
        const { rerender } = render(<ThemeDetailChart history={history} proxyEtf={null} />);

        // A start equal to the end would be a zero-width markArea on the category axis.
        expect(attr("data-bands")).toEqual([["Emerging", history.dates[4], history.dates[5], colors.info]]);

        // A run from the first session has no earlier step, so it starts at the first date rather than before the window.
        rerender(<ThemeDetailChart history={{ ...history, stage: ["crowded", "crowded", ...Array(4).fill("neutral")] as typeof base.stage }} proxyEtf={null} />);
        expect(attr("data-bands")).toEqual([["Crowded", history.dates[0], history.dates[1], colors.caution]]);
    });

    it("shows an empty state before any publication", () => {
        render(<ThemeDetailChart history={makeThemeHistory(0)} proxyEtf={null} />);

        expect(screen.queryByTestId("echarts")).not.toBeInTheDocument();
        expect(screen.getByText("No published history for this theme yet.")).toBeInTheDocument();
    });
});
