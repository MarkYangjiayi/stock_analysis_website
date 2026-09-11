import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { makeValuationHistoryFixture } from "@/test/valuationHistoryFixture";
import type { HistoricalDataPoint } from "@/lib/api";

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
        history.points[2].values.pe = null;
        render(<StockValuationChart data={prices} history={history} interval="1d" onIntervalChange={vi.fn()} />);
        expect(screen.getByLabelText("Latest P/E")).toHaveTextContent("N/M");
        expect(screen.getByRole("status")).toHaveTextContent("EPS is zero or negative.");
        expect(screen.getByText("21×")).toBeVisible();
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

    it("keeps the price chart available when valuation history is missing", () => {
        render(<StockValuationChart data={prices} interval="1d" onIntervalChange={vi.fn()} isLoading />);
        expect(screen.getByRole("status")).toHaveTextContent("Historical multiples are unavailable");
        expect(screen.getByRole("button", { name: "W" })).toBeDisabled();
        expect(screen.getByTestId("chart")).toBeVisible();
    });
});
