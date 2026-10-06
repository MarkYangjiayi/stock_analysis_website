"use client";

import { useMemo } from "react";
import ReactECharts from "echarts-for-react";
import { useTheme } from "next-themes";

import type { MarketOverviewResponse } from "@/lib/api";
import { chartMonoFont, chartTheme } from "@/lib/chartTheme";

export type TrendMode = "relative" | "absolute";
export type LowerMetric = "net_advances" | "new_high_low" | "mcclellan";

interface MarketOverviewChartProps {
    data: MarketOverviewResponse;
    trendMode: TrendMode;
    lowerMetric: LowerMetric;
}

interface AxisTooltipParam {
    dataIndex?: number;
}

interface BarColorParam {
    value?: number | null;
}

const LOWER_LABELS: Record<LowerMetric, string> = {
    net_advances: "Net advances",
    new_high_low: "New highs − new lows",
    mcclellan: "McClellan oscillator",
};

const asDisplayNumber = (value: number | null | undefined, digits = 1) =>
    value == null || !Number.isFinite(value) ? "—" : value.toFixed(digits);

const asPercent = (value: number | null | undefined, digits = 2) =>
    value == null || !Number.isFinite(value) ? "—" : `${(value * 100).toFixed(digits)}%`;

export default function MarketOverviewChart({
    data,
    trendMode,
    lowerMetric,
}: MarketOverviewChartProps) {
    const { resolvedTheme } = useTheme();
    const dark = resolvedTheme === "dark";

    const option = useMemo(() => {
        const colors = chartTheme(dark);
        const sectorColors = colors.categorical;
        const sectorColor = (index: number) => sectorColors[index % sectorColors.length];
        const monoFont = chartMonoFont();
        const axisColor = colors.textMuted;
        const axisText = { color: axisColor, fontFamily: monoFont };
        const textColor = colors.text;
        // SPY is the neutral, emphasized benchmark; RSP/SPY is a neutral dashed proxy so
        // neither competes with the categorical sector hues.
        const benchmarkColor = colors.text;
        const proxyColor = colors.textMuted;
        const dispersionColor = colors.brand;
        const breadthColors = [colors.series[1], colors.series[4], colors.series[2]];
        const lowerValues = lowerMetric === "net_advances"
            ? data.breadth.net_advances_pct
            : lowerMetric === "new_high_low"
                ? data.breadth.new_high_low_pct
                : data.breadth.mcclellan;
        const trendKey = trendMode === "relative" ? "relative_to_spy_index" : "absolute_index";
        const topLegend = data.sector_trends.map((sector) => sector.ticker);
        if (trendMode === "absolute") topLegend.push("SPY");
        topLegend.push("RSP/SPY");

        const series: Array<Record<string, unknown>> = data.sector_trends.map((sector, index) => ({
            name: sector.ticker,
            type: "line",
            xAxisIndex: 0,
            yAxisIndex: 0,
            data: sector[trendKey],
            showSymbol: false,
            connectNulls: false,
            smooth: 0.12,
            lineStyle: { width: 1.7, color: sectorColor(index) },
            itemStyle: { color: sectorColor(index) },
            emphasis: { focus: "series", lineStyle: { width: 3 } },
            markLine: index === 0 ? {
                silent: true,
                symbol: "none",
                label: { show: false },
                lineStyle: { color: axisColor, type: "dotted", opacity: 0.55 },
                data: [{ yAxis: 100 }],
            } : undefined,
        }));

        if (trendMode === "absolute") {
            series.push({
                name: "SPY",
                type: "line",
                xAxisIndex: 0,
                yAxisIndex: 0,
                data: data.benchmark.absolute_index,
                showSymbol: false,
                lineStyle: { color: benchmarkColor, width: 2.8 },
                itemStyle: { color: benchmarkColor },
                emphasis: { focus: "series" },
            });
        }
        series.push({
            name: "RSP/SPY",
            type: "line",
            xAxisIndex: 0,
            yAxisIndex: 0,
            data: data.rsp_spy_index,
            showSymbol: false,
            lineStyle: { color: proxyColor, width: 3.2, type: "dashed" },
            itemStyle: { color: proxyColor },
            emphasis: { focus: "series" },
            z: 8,
        });

        [
            ["Above MA20", data.breadth.pct_above_ma20, breadthColors[0]],
            ["Above MA50", data.breadth.pct_above_ma50, breadthColors[1]],
            ["Above MA200", data.breadth.pct_above_ma200, breadthColors[2]],
        ].forEach(([name, values, color], index) => {
            series.push({
                name,
                type: "line",
                xAxisIndex: 1,
                yAxisIndex: 1,
                data: values,
                showSymbol: false,
                lineStyle: { color, width: 2 },
                itemStyle: { color },
                emphasis: { focus: "series" },
                markLine: index === 0 ? {
                    silent: true,
                    symbol: "none",
                    label: { show: false },
                    lineStyle: { color: axisColor, type: "dashed", opacity: 0.6 },
                    data: [{ yAxis: 50 }],
                } : undefined,
            });
        });

        series.push({
            name: LOWER_LABELS[lowerMetric],
            type: "bar",
            xAxisIndex: 2,
            yAxisIndex: 2,
            data: lowerValues,
            barMaxWidth: 8,
            itemStyle: {
                color: (params: BarColorParam) => Number(params.value ?? 0) >= 0 ? colors.positive : colors.negative,
                opacity: 0.82,
            },
            emphasis: { focus: "series" },
        });
        series.push({
            name: "Dispersion 20D EMA",
            type: "line",
            xAxisIndex: 2,
            yAxisIndex: 3,
            data: data.breadth.dispersion_20d.map((value) => value == null ? null : value * 100),
            showSymbol: false,
            connectNulls: false,
            lineStyle: { color: dispersionColor, width: 2.4 },
            itemStyle: { color: dispersionColor },
            emphasis: { focus: "series" },
            z: 7,
        });

        const tooltipFormatter = (rawParams: AxisTooltipParam | AxisTooltipParam[]) => {
            const params = Array.isArray(rawParams) ? rawParams : [rawParams];
            const index = params.find((item) => item.dataIndex != null)?.dataIndex;
            if (index == null) return "";
            const sectorRows = data.sector_trends.map((sector, sectorIndex) => {
                const value = sector[trendKey][index];
                const color = sectorColor(sectorIndex);
                return `<div style="display:flex;justify-content:space-between;gap:18px"><span><i style="display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;background:${color}"></i>${sector.ticker}</span><b>${asDisplayNumber(value, 2)}</b></div>`;
            }).join("");
            const benchmarkRow = trendMode === "absolute"
                ? `<div style="display:flex;justify-content:space-between;gap:18px"><span><i style="display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;background:${benchmarkColor}"></i>SPY</span><b>${asDisplayNumber(data.benchmark.absolute_index[index], 2)}</b></div>`
                : "";
            const breadth = data.breadth;
            return `<div style="min-width:240px;color:${textColor}">
                <div style="font-weight:600;margin-bottom:6px">${data.dates[index]}</div>
                ${sectorRows}
                ${benchmarkRow}
                <div style="display:flex;justify-content:space-between;gap:18px;margin-top:4px"><span><i style="display:inline-block;width:12px;border-top:2px dashed ${proxyColor};margin-right:6px;vertical-align:middle"></i>RSP/SPY</span><b>${asDisplayNumber(data.rsp_spy_index[index], 2)}</b></div>
                <div style="border-top:1px solid ${colors.border};margin:7px 0"></div>
                <div style="display:flex;justify-content:space-between"><span>Above MA20 / 50 / 200</span><b>${asDisplayNumber(breadth.pct_above_ma20[index])} / ${asDisplayNumber(breadth.pct_above_ma50[index])} / ${asDisplayNumber(breadth.pct_above_ma200[index])}%</b></div>
                <div style="display:flex;justify-content:space-between"><span>Net advances</span><b>${asDisplayNumber(breadth.net_advances_pct[index])}%</b></div>
                <div style="display:flex;justify-content:space-between"><span>New highs / lows</span><b>${asDisplayNumber(breadth.new_high_pct[index])}% / ${asDisplayNumber(breadth.new_low_pct[index])}%</b></div>
                <div style="display:flex;justify-content:space-between"><span>McClellan</span><b>${asDisplayNumber(breadth.mcclellan[index], 2)}</b></div>
                <div style="display:flex;justify-content:space-between"><span>Dispersion 1D / 20D EMA</span><b>${asPercent(breadth.dispersion_1d[index])} / ${asPercent(breadth.dispersion_20d[index])}</b></div>
                <div style="display:flex;justify-content:space-between"><span>Members / price coverage</span><b>${breadth.member_count[index] ?? "—"} / ${asDisplayNumber(breadth.price_coverage_pct[index])}%</b></div>
            </div>`;
        };

        const categoryAxis = (gridIndex: number, showLabels: boolean) => ({
            type: "category",
            gridIndex,
            data: data.dates,
            boundaryGap: gridIndex === 2,
            axisLine: { lineStyle: { color: colors.border } },
            axisTick: { show: false },
            axisLabel: {
                ...axisText,
                show: showLabels,
                formatter: (value: string) => value.slice(5),
            },
            axisPointer: { show: true, snap: true },
        });
        const valueAxis = (gridIndex: number, extras: Record<string, unknown> = {}) => ({
            type: "value",
            gridIndex,
            scale: true,
            axisLabel: axisText,
            axisLine: { show: false },
            axisTick: { show: false },
            splitLine: { lineStyle: { color: colors.grid, type: "dashed" } },
            ...extras,
        });

        return {
            animationDuration: 350,
            aria: {
                enabled: true,
                description: "US sector trends, market breadth, participation, and cross-sectional dispersion on linked daily timelines.",
            },
            color: sectorColors,
            title: [
                { text: trendMode === "relative" ? "Sector trends relative to SPY" : "Sector and SPY absolute trends", left: 62, top: 43, textStyle: { color: textColor, fontSize: 13, fontWeight: 600 } },
                { text: "Market breadth", left: 62, top: "46%", textStyle: { color: textColor, fontSize: 13, fontWeight: 600 } },
                { text: "Participation & dispersion", left: 62, top: "69%", textStyle: { color: textColor, fontSize: 13, fontWeight: 600 } },
            ],
            legend: [
                {
                    type: "scroll",
                    top: 5,
                    left: 50,
                    right: 24,
                    data: topLegend,
                    textStyle: { ...axisText, fontSize: 11 },
                    pageTextStyle: axisText,
                    pageIconColor: colors.brand,
                    pageIconInactiveColor: colors.border,
                },
                {
                    top: "45.5%",
                    right: 28,
                    data: ["Above MA20", "Above MA50", "Above MA200"],
                    textStyle: { color: axisColor, fontSize: 11 },
                },
                {
                    top: "68.5%",
                    right: 28,
                    data: [LOWER_LABELS[lowerMetric], "Dispersion 20D EMA"],
                    textStyle: { color: axisColor, fontSize: 11 },
                },
            ],
            tooltip: {
                trigger: "axis",
                confine: true,
                order: "seriesAsc",
                backgroundColor: colors.tooltipBackground,
                borderColor: colors.border,
                textStyle: { color: textColor, fontSize: 12, fontFamily: monoFont },
                extraCssText: "max-height:72vh;overflow-y:auto;",
                axisPointer: { type: "cross", snap: true },
                formatter: tooltipFormatter,
            },
            axisPointer: { link: [{ xAxisIndex: "all" }] },
            grid: [
                { left: 62, right: 56, top: 72, height: "34%", containLabel: false },
                { left: 62, right: 56, top: "50%", height: "15%", containLabel: false },
                { left: 62, right: 56, top: "73%", bottom: 72, containLabel: false },
            ],
            xAxis: [categoryAxis(0, false), categoryAxis(1, false), categoryAxis(2, true)],
            yAxis: [
                valueAxis(0, { name: "Index", nameTextStyle: axisText, splitNumber: 5 }),
                valueAxis(1, { min: 0, max: 100, interval: 25, name: "%", nameTextStyle: axisText }),
                valueAxis(2, { name: "%", nameTextStyle: axisText, splitNumber: 4 }),
                valueAxis(2, {
                    position: "right",
                    name: "Dispersion %",
                    nameTextStyle: { ...axisText, color: dispersionColor },
                    axisLabel: { ...axisText, color: dispersionColor, formatter: "{value}%" },
                    splitLine: { show: false },
                }),
            ],
            dataZoom: [
                {
                    type: "inside",
                    xAxisIndex: [0, 1, 2],
                    filterMode: "none",
                    start: 0,
                    end: 100,
                    zoomOnMouseWheel: false,
                    moveOnMouseWheel: false,
                },
                {
                    type: "slider",
                    xAxisIndex: [0, 1, 2],
                    filterMode: "none",
                    bottom: 12,
                    height: 22,
                    borderColor: colors.border,
                    backgroundColor: colors.backgroundMuted,
                    fillerColor: `${colors.brand}24`,
                    handleStyle: { color: colors.brand, borderColor: colors.brand },
                    moveHandleStyle: { color: colors.brand },
                    textStyle: axisText,
                },
            ],
            series,
        };
    }, [dark, data, lowerMetric, trendMode]);

    return <div onWheelCapture={(event) => event.stopPropagation()}>
        <ReactECharts
            option={option}
            notMerge
            lazyUpdate
            style={{ width: "100%", height: "940px" }}
            opts={{ renderer: "canvas" }}
        />
    </div>;
}
