"use client";

import { useMemo } from "react";
import ReactECharts from "echarts-for-react";
import { useTheme } from "next-themes";
import { EquityCurvePoint } from "@/lib/api";
import { chartMonoFont, chartTheme } from "@/lib/chartTheme";

export default function BacktestEquityChart({ data }: { data: EquityCurvePoint[] }) {
    const { resolvedTheme } = useTheme();
    const option = useMemo(() => {
        const colors = chartTheme(resolvedTheme === "dark");
        const monoFont = chartMonoFont();
        return {
            animation: false,
            aria: { enabled: true, description: "Backtest equity curve over time" },
            grid: { left: 52, right: 24, top: 24, bottom: 38 },
            tooltip: {
                trigger: "axis",
                backgroundColor: colors.tooltipBackground,
                borderColor: colors.border,
                textStyle: { color: colors.text, fontFamily: monoFont },
                axisPointer: { type: "line", lineStyle: { color: colors.textMuted, type: "dashed" } },
                formatter: (params: Array<{ axisValue: string; value: number }>) => {
                    const item = params[0];
                    return item ? `<strong>${item.axisValue}</strong><br/>Equity: ${Number(item.value).toFixed(4)}` : "";
                },
            },
            xAxis: {
                type: "category",
                data: data.map((point) => point.date),
                boundaryGap: false,
                axisLabel: { color: colors.textMuted, fontFamily: monoFont, hideOverlap: true },
                axisLine: { lineStyle: { color: colors.border } },
                axisTick: { lineStyle: { color: colors.border } },
            },
            yAxis: {
                type: "value",
                scale: true,
                axisLabel: { color: colors.textMuted, fontFamily: monoFont, formatter: (value: number) => value.toFixed(2) },
                splitLine: { lineStyle: { color: colors.grid, type: "dashed" } },
            },
            series: [{
                type: "line",
                data: data.map((point) => point.equity),
                showSymbol: false,
                smooth: false,
                lineStyle: { width: 2, color: colors.brand },
                itemStyle: { color: colors.brand },
                areaStyle: { color: `${colors.brand}1f` },
            }],
        };
    }, [data, resolvedTheme]);

    return <div onWheelCapture={(event) => event.stopPropagation()}>
        <ReactECharts option={option} style={{ height: 360, width: "100%" }} />
    </div>;
}
