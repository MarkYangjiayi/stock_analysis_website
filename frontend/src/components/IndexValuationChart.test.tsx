import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { makeIndexValuationFixture } from "@/test/indexValuationFixture";

const chart = vi.hoisted(() => ({ option: {} as Record<string, unknown> }));
vi.mock("next-themes", () => ({ useTheme: () => ({ resolvedTheme: "light" }) }));
vi.mock("echarts-for-react", () => ({
    default: (props: { option: Record<string, unknown> }) => {
        chart.option = props.option;
        return <div data-testid="index-pe-chart" />;
    },
}));

import IndexValuationChart, { indexValuationChartOption } from "./IndexValuationChart";

describe("IndexValuationChart", () => {
    it("maps month points to series and preserves gap months", () => {
        const fixture = makeIndexValuationFixture();
        const option = indexValuationChartOption(fixture, false);
        const primary = option.series.find((item) => item.id === "index-pe")!;
        const earners = option.series.find((item) => item.id === "index-pe-earners")!;
        expect(primary).toMatchObject({ connectNulls: false, smooth: false });
        // The low-coverage month in the fixture must stay an explicit gap.
        expect(primary.data).toEqual([27.8, null, 28.4]);
        expect(earners.data).toEqual([26.1, null, 26.6]);
        expect(primary.markLine!.data).toEqual([{ yAxis: fixture.stats.median_index_pe }]);
        expect(option.xAxis.data).toEqual(fixture.points.map((point) => point.date));
        expect(option.dataZoom.every((zoom) => zoom.filterMode === "none")).toBe(true);
    });

    it("omits the median markLine when no valid month exists", () => {
        const fixture = makeIndexValuationFixture();
        fixture.stats.median_index_pe = null;
        const option = indexValuationChartOption(fixture, true);
        const primary = option.series.find((item) => item.id === "index-pe")!;
        expect(primary.markLine!.data).toEqual([]);
    });

    it("renders the linked chart inside the page", () => {
        render(<IndexValuationChart data={makeIndexValuationFixture()} />);
        expect(screen.getByTestId("index-pe-chart")).toBeInTheDocument();
        expect(screen.getByRole("img", { name: /aggregate P\/E history/ })).toBeInTheDocument();
    });
});
