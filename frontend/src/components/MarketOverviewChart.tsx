"use client";

import { useMemo } from "react";
import ReactECharts from "echarts-for-react";
import { useTheme } from "next-themes";

import type { MarketOverviewResponse } from "@/lib/api";

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

const SECTOR_COLORS = [
    "#2563eb",
    "#7c3aed",
    "#db2777",
    "#ea580c",
    "#ca8a04",
    "#16a34a",
    "#0891b2",
    "#4f46e5",
    "#9333ea",
    "#dc2626",
    "#0d9488",
];

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
        const axisColor = dark ? "#91a19b" : "#64748b";
        const splitColor = dark ? "rgba(145,161,155,0.13)" : "rgba(100,116,139,0.13)";
        const textColor = dark ? "#e8f0ed" : "#12211d";
        const tooltipBackground = dark ? "rgba(15,21,27,0.97)" : "rgba(255,255,255,0.98)";
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
            lineStyle: { width: 1.7, color: SECTOR_COLORS[index % SECTOR_COLORS.length] },
            itemStyle: { color: SECTOR_COLORS[index % SECTOR_COLORS.length] },
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
                lineStyle: { color: dark ? "#f8fafc" : "#0f172a", width: 2.8 },
                itemStyle: { color: dark ? "#f8fafc" : "#0f172a" },
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
            lineStyle: { color: "#10b981", width: 3.2, type: "dashed" },
            itemStyle: { color: "#10b981" },
            emphasis: { focus: "series" },
            z: 8,
        });

        [
            ["Above MA20", data.breadth.pct_above_ma20, "#0ea5e9"],
            ["Above MA50", data.breadth.pct_above_ma50, "#8b5cf6"],
            ["Above MA200", data.breadth.pct_above_ma200, "#f59e0b"],
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
                color: (params: BarColorParam) => Number(params.value ?? 0) >= 0 ? "#10b981" : "#ef5b6b",
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
            lineStyle: { color: "#f97316", width: 2.4 },
            itemStyle: { color: "#f97316" },
            emphasis: { focus: "series" },
            z: 7,
        });

        const tooltipFormatter = (rawParams: AxisTooltipParam | AxisTooltipParam[]) => {
            const params = Array.isArray(rawParams) ? rawParams : [rawParams];
            const index = params.find((item) => item.dataIndex != null)?.dataIndex;
            if (index == null) return "";
            const sectorRows = data.sector_trends.map((sector, sectorIndex) => {
                const value = sector[trendKey][index];
                const color = SECTOR_COLORS[sectorIndex % SECTOR_COLORS.length];
                return `<div style="display:flex;justify-content:space-between;gap:18px"><span><i style="display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;background:${color}"></i>${sector.ticker}</span><b>${asDisplayNumber(value, 2)}</b></div>`;
            }).join("");
            const breadth = data.breadth;
            return `<div style="min-width:240px;color:${textColor}">
                <div style="font-weight:800;margin-bottom:6px">${data.dates[index]}</div>
                ${sectorRows}
                <div style="display:flex;justify-content:space-between;gap:18px;margin-top:4px"><span><i style="display:inline-block;width:12px;border-top:2px dashed #10b981;margin-right:6px;vertical-align:middle"></i>RSP/SPY</span><b>${asDisplayNumber(data.rsp_spy_index[index], 2)}</b></div>
                <div style="border-top:1px solid ${splitColor};margin:7px 0"></div>
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
            axisLine: { lineStyle: { color: splitColor } },
            axisTick: { show: false },
            axisLabel: {
                show: showLabels,
                color: axisColor,
                formatter: (value: string) => value.slice(5),
            },
            axisPointer: { show: true, snap: true },
        });
        const valueAxis = (gridIndex: number, extras: Record<string, unknown> = {}) => ({
            type: "value",
            gridIndex,
            scale: true,
            axisLabel: { color: axisColor },
            axisLine: { show: false },
            axisTick: { show: false },
            splitLine: { lineStyle: { color: splitColor } },
            ...extras,
        });

        return {
            animationDuration: 350,
            aria: {
                enabled: true,
                description: "US sector trends, market breadth, participation, and cross-sectional dispersion on linked daily timelines.",
            },
            color: SECTOR_COLORS,
            title: [
                { text: trendMode === "relative" ? "Sector trends relative to SPY" : "Sector and SPY absolute trends", left: 62, top: 43, textStyle: { color: textColor, fontSize: 13, fontWeight: 700 } },
                { text: "Market breadth", left: 62, top: "46%", textStyle: { color: textColor, fontSize: 13, fontWeight: 700 } },
                { text: "Participation & dispersion", left: 62, top: "69%", textStyle: { color: textColor, fontSize: 13, fontWeight: 700 } },
            ],
            legend: [
                {
                    type: "scroll",
                    top: 5,
                    left: 50,
                    right: 24,
                    data: topLegend,
                    textStyle: { color: axisColor, fontSize: 11 },
                    pageTextStyle: { color: axisColor },
                    pageIconColor: "#10b981",
                    pageIconInactiveColor: dark ? "#35434d" : "#cbd5e1",
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
                backgroundColor: tooltipBackground,
                borderColor: dark ? "#35434d" : "#cbd5e1",
                textStyle: { color: textColor, fontSize: 12 },
                extraCssText: "max-height:72vh;overflow-y:auto;box-shadow:0 18px 45px rgba(15,23,42,.18);",
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
                valueAxis(0, { name: "Index", nameTextStyle: { color: axisColor }, splitNumber: 5 }),
                valueAxis(1, { min: 0, max: 100, interval: 25, name: "%", nameTextStyle: { color: axisColor } }),
                valueAxis(2, { name: "%", nameTextStyle: { color: axisColor }, splitNumber: 4 }),
                valueAxis(2, {
                    position: "right",
                    name: "Dispersion %",
                    nameTextStyle: { color: "#f97316" },
                    axisLabel: { color: "#f97316", formatter: "{value}%" },
                    splitLine: { show: false },
                }),
            ],
            dataZoom: [
                { type: "inside", xAxisIndex: [0, 1, 2], filterMode: "none", start: 0, end: 100 },
                {
                    type: "slider",
                    xAxisIndex: [0, 1, 2],
                    filterMode: "none",
                    bottom: 12,
                    height: 22,
                    borderColor: splitColor,
                    backgroundColor: dark ? "#0f151b" : "#f8fafc",
                    fillerColor: dark ? "rgba(57,201,155,.16)" : "rgba(15,159,120,.13)",
                    handleStyle: { color: "#10b981", borderColor: "#10b981" },
                    textStyle: { color: axisColor },
                },
            ],
            series,
        };
    }, [dark, data, lowerMetric, trendMode]);

    return (
        <ReactECharts
            option={option}
            notMerge
            lazyUpdate
            style={{ width: "100%", height: "940px" }}
            opts={{ renderer: "canvas" }}
        />
    );
}
