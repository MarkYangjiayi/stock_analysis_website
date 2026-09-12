import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { makeValuationHistoryFixture } from "@/test/valuationHistoryFixture";
import type { HistoricalDataPoint, MultipleKey } from "@/lib/api";

const chart = vi.hoisted(() => ({ option: {} as Record<string, unknown>, onEvents: {} as Record<string, (event: { start: number; end: number }) => void> }));
vi.mock("next-themes", () => ({ useTheme: () => ({ resolvedTheme: "light" }) }));
vi.mock("echarts-for-react", () => ({ default: (props: typeof chart) => {
    chart.option = props.option;
    chart.onEvents = props.onEvents;
    return <div data-testid="chart" />;
} }));

import StockValuationChart, { valuationChartOption } from "./StockValuationChart";

const prices: HistoricalDataPoint[] = ["2024-11-04", "2024-11-05", "2024-11-06"].map((date) => ({ date, open: 40, high: 42, low: 39, close: 41, volume: 1000, MA20: 39, MA50: 38 }));

describe("StockValuationChart", () => {
    it("links all panes and preserves missing intervals without smoothing", () => {
        const history = makeValuationHistoryFixture(prices);
        history.points[1].values.pe = null;
        const option = valuationChartOption(prices, history, "pe", true, { start: 10, end: 90 });
        expect(option.grid).toHaveLength(3);
        expect(option.yAxis[0].type).toBe("log");
        expect(option.axisPointer.link[0].xAxisIndex).toBe("all");
        expect(option.dataZoom.every((zoom) => zoom.xAxisIndex.join() === "0,1,2")).toBe(true);
        const series = option.series.find((item) => item.id === "multiple")!;
        expect(series).toMatchObject({ data: [20, null, 22], smooth: false, connectNulls: false });
        expect(option.xAxis.every((axis) => axis.data === option.xAxis[0].data)).toBe(true);
    });

    it("offers six metrics and retains the zoom when switching", () => {
        const onIntervalChange = vi.fn();
        render(<StockValuationChart data={prices} history={makeValuationHistoryFixture(prices)} interval="1d" onIntervalChange={onIntervalChange} />);
        expect(screen.getAllByRole("option")).toHaveLength(6);
        act(() => chart.onEvents.datazoom({ start: 40, end: 80 }));
        fireEvent.change(screen.getByLabelText("Historical valuation multiple"), { target: { value: "ev_ebitda" } });
        expect(screen.getByLabelText("Latest EV/EBITDA")).toHaveTextContent("14×");
        expect((chart.option.dataZoom as Array<{ start: number }>)[0].start).toBe(40);
        fireEvent.click(screen.getByRole("button", { name: "W" }));
        expect(onIntervalChange).toHaveBeenCalledWith("1wk");
    });

    it("shows an unavailable latest value instead of carrying forward a valid one", () => {
        const history = makeValuationHistoryFixture(prices);
        history.metrics[0].latest_value = null;
        history.metrics[0].latest_reason = "EPS is zero or negative.";
        history.metrics[0].valid_points = 2;
        history.points[2].values.pe = null;
        render(<StockValuationChart data={prices} history={history} interval="1d" onIntervalChange={vi.fn()} />);
        expect(screen.getByLabelText("Latest P/E")).toHaveTextContent("N/M");
        expect(screen.getByRole("status")).toHaveTextContent("EPS is zero or negative.");
        expect(screen.getByText("21×")).toBeVisible();
        expect(screen.getByText("2 / 3 available observations")).toBeVisible();
    });

    it("uses the actual trading date and escapes evidence in the shared tooltip", () => {
        const history = makeValuationHistoryFixture(prices);
        history.points[0].price_date = "2024-11-01";
        history.points[0].values.pe = null;
        history.points[0].reason = "Missing <img src=x>";
        const option = valuationChartOption(prices, history, "pe", false, { start: 0, end: 100 });
        const tooltip = option.tooltip.formatter([{ dataIndex: 0 }]);
        expect(tooltip).toContain("2024-11-01");
        expect(tooltip).toContain("2024-09-30");
        expect(tooltip).toContain("P/E: N/M");
        expect(tooltip).toContain("&lt;img src=x&gt;");
        expect(tooltip).not.toContain("<img");
    });

    it.each<[MultipleKey, string]>([
        ["pe", "TTM earnings / share: USD 0.000000123456 per share"],
        ["ps", "TTM revenue: USD 1.25 billion"],
        ["pb", "Quarter-end equity: USD 750 million"],
        ["pfcf", "TTM free cash flow: USD 125 million"],
        ["ev_revenue", "TTM revenue: USD 1.25 billion"],
        ["ev_ebitda", "TTM EBITDA: USD 250 million"],
    ])("explains the %s denominator with its units and relevant quarters", (metric, expected) => {
        const history = makeValuationHistoryFixture(prices);
        history.bases[0].inputs = { eps: 0.000000123456, revenue: 1.25e9, book: 750e6, fcf: 125e6, ebitda: 250e6 };
        const option = valuationChartOption(prices, history, metric, false, { start: 0, end: 100 });
        const tooltip = option.tooltip.formatter([{ dataIndex: 0 }]);
        expect(tooltip).toContain(expected);
        if (metric === "pb") {
            expect(tooltip).not.toContain("TTM quarters");
            expect(tooltip).toContain("Statement: 2024-09-30");
        } else {
            const text = new DOMParser().parseFromString(tooltip, "text/html").body.textContent;
            expect(text).toContain("TTM quarters: 2024-09-30 · 2024-06-30 · 2024-03-31 · 2023-12-31");
        }
    });

    it("does not assign quote currency to a denominator whose inputs failed validation", () => {
        const history = makeValuationHistoryFixture(prices);
        history.points[0].values.pe = null;
        history.bases[0].inputs.eps = 15;
        history.bases[0].reasons.pe = "Matching price and statement currencies are required.";
        const option = valuationChartOption(prices, history, "pe", false, { start: 0, end: 100 });
        const tooltip = option.tooltip.formatter([{ dataIndex: 0 }]);
        expect(tooltip).toContain("TTM earnings / share: Unavailable");
        expect(tooltip).toContain("Matching price and statement currencies are required.");
        expect(tooltip).not.toContain("USD 15");
    });

    it("keeps the price chart available when valuation history is missing", () => {
        render(<StockValuationChart data={prices} interval="1d" onIntervalChange={vi.fn()} isLoading />);
        expect(screen.getByRole("status")).toHaveTextContent("Historical multiples are unavailable");
        expect(screen.getByRole("button", { name: "W" })).toBeDisabled();
        expect(screen.getByTestId("chart")).toBeVisible();
    });
});
