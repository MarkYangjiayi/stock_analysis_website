"use client";

import { useMemo } from "react";
import ReactECharts from "echarts-for-react";
import { useTheme } from "next-themes";
import { EquityCurvePoint } from "@/lib/api";

export default function BacktestEquityChart({ data }: { data: EquityCurvePoint[] }) {
    const { resolvedTheme } = useTheme();
    const option = useMemo(() => {
        const dark = resolvedTheme === "dark";
        const text = dark ? "#91a19b" : "#64748b";
        const grid = dark ? "#26313a" : "#e2e8f0";
        return {
            animation: false,
            aria: { enabled: true, description: "Backtest equity curve over time" },
            grid: { left: 52, right: 24, top: 24, bottom: 38 },
            tooltip: {
                trigger: "axis",
                backgroundColor: dark ? "#172029" : "#ffffff",
                borderColor: grid,
                textStyle: { color: dark ? "#e8f0ed" : "#12211d" },
                formatter: (params: Array<{ axisValue: string; value: number }>) => {
                    const item = params[0];
                    return item ? `<strong>${item.axisValue}</strong><br/>Equity: ${Number(item.value).toFixed(4)}` : "";
                },
            },
            xAxis: {
                type: "category",
                data: data.map((point) => point.date),
                boundaryGap: false,
                axisLabel: { color: text, hideOverlap: true },
                axisLine: { lineStyle: { color: grid } },
            },
            yAxis: {
                type: "value",
                scale: true,
                axisLabel: { color: text, formatter: (value: number) => value.toFixed(2) },
                splitLine: { lineStyle: { color: grid, type: "dashed" } },
            },
            series: [{
                type: "line",
                data: data.map((point) => point.equity),
                showSymbol: false,
                smooth: false,
                lineStyle: { width: 2, color: "#0f9f78" },
                areaStyle: { color: "rgba(15,159,120,0.12)" },
            }],
        };
    }, [data, resolvedTheme]);

    return <ReactECharts option={option} style={{ height: 360, width: "100%" }} />;
}
