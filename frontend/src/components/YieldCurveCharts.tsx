"use client";

import { useMemo } from "react";
import ReactECharts from "echarts-for-react";
import { useTheme } from "next-themes";

import type { TreasuryYieldCurveResponse } from "@/lib/api";
import { chartTheme } from "@/lib/chartTheme";

const SERIES_COLORS = [
    "#10b981",
    "#6366f1",
    "#f59e0b",
    "#64748b",
    "#0ea5e9",
    "#ec4899",
    "#8b5cf6",
    "#f97316",
];

const dateLabel = (value: string) => value.slice(5);
const yieldLabel = (value: unknown) => {
    if (value == null) return "—";
    const number = Number(value);
    return Number.isFinite(number) ? `${number.toFixed(2)}%` : "—";
};

function baseChartColors(dark: boolean) {
    const colors = chartTheme(dark);
    return {
        colors,
        axisColor: colors.textMuted,
        splitColor: dark ? "rgba(145,161,155,0.13)" : "rgba(100,116,139,0.13)",
    };
}

export function CurrentYieldCurveChart({ data }: { data: TreasuryYieldCurveResponse }) {
    const { resolvedTheme } = useTheme();
    const dark = resolvedTheme === "dark";
    const option = useMemo(() => {
        const { colors, axisColor, splitColor } = baseChartColors(dark);
        return {
            animationDuration: 350,
            aria: {
                enabled: true,
                description: "Current U.S. Treasury par yield curve compared with prior monthly and annual snapshots.",
            },
            color: SERIES_COLORS,
            legend: {
                top: 4,
                textStyle: { color: axisColor, fontSize: 11 },
            },
            grid: { left: 58, right: 28, top: 48, bottom: 45 },
            tooltip: {
                trigger: "axis",
                confine: true,
                backgroundColor: colors.backgroundMuted,
                borderColor: colors.border,
                textStyle: { color: colors.text, fontSize: 12 },
                valueFormatter: yieldLabel,
                axisPointer: { type: "line" },
            },
            xAxis: {
                type: "category",
                data: data.maturities.map((maturity) => maturity.label),
                boundaryGap: false,
                axisLine: { lineStyle: { color: splitColor } },
                axisTick: { show: false },
                axisLabel: { color: axisColor, interval: 0, fontSize: 10 },
            },
            yAxis: {
                type: "value",
                scale: true,
                name: "Yield %",
                nameTextStyle: { color: axisColor },
                axisLabel: { color: axisColor, formatter: "{value}%" },
                splitLine: { lineStyle: { color: splitColor } },
            },
            series: data.snapshots.map((snapshot, index) => ({
                name: `${snapshot.label} · ${snapshot.date}`,
                type: "line",
                data: data.maturities.map((maturity) => snapshot.yields[maturity.key] ?? null),
                smooth: 0.24,
                connectNulls: false,
                symbol: snapshot.key === "latest" ? "circle" : "none",
                symbolSize: 7,
                lineStyle: {
                    width: snapshot.key === "latest" ? 3.5 : 1.8,
                    type: snapshot.key === "latest" ? "solid" : "dashed",
                    color: SERIES_COLORS[index],
                },
                itemStyle: { color: SERIES_COLORS[index] },
                emphasis: { focus: "series" },
                z: snapshot.key === "latest" ? 6 : 2,
            })),
        };
    }, [dark, data]);

    return (
        <div onWheelCapture={(event) => event.stopPropagation()}>
            <ReactECharts
                option={option}
                notMerge
                lazyUpdate
                style={{ width: "100%", height: "410px" }}
                opts={{ renderer: "canvas" }}
            />
        </div>
    );
}

export function YieldHistoryChart({
    data,
    selectedMaturities,
}: {
    data: TreasuryYieldCurveResponse;
    selectedMaturities: string[];
}) {
    const { resolvedTheme } = useTheme();
    const dark = resolvedTheme === "dark";
    const option = useMemo(() => {
        const { colors, axisColor, splitColor } = baseChartColors(dark);
        const maturityByKey = new Map(data.maturities.map((maturity) => [maturity.key, maturity]));
        return {
            animationDuration: 250,
            aria: {
                enabled: true,
                description: "Historical U.S. Treasury par yields for the selected maturities.",
            },
            color: SERIES_COLORS,
            legend: {
                top: 4,
                data: selectedMaturities.map((key) => maturityByKey.get(key)?.label ?? key),
                textStyle: { color: axisColor, fontSize: 11 },
            },
            grid: { left: 58, right: 30, top: 48, bottom: 66 },
            tooltip: {
                trigger: "axis",
                confine: true,
                backgroundColor: colors.backgroundMuted,
                borderColor: colors.border,
                textStyle: { color: colors.text, fontSize: 12 },
                valueFormatter: yieldLabel,
                axisPointer: { type: "cross", snap: true },
            },
            xAxis: {
                type: "category",
                data: data.observations.map((observation) => observation.date),
                boundaryGap: false,
                axisLine: { lineStyle: { color: splitColor } },
                axisTick: { show: false },
                axisLabel: { color: axisColor, formatter: dateLabel },
            },
            yAxis: {
                type: "value",
                scale: true,
                name: "Yield %",
                nameTextStyle: { color: axisColor },
                axisLabel: { color: axisColor, formatter: "{value}%" },
                splitLine: { lineStyle: { color: splitColor } },
            },
            dataZoom: [
                {
                    type: "inside",
                    filterMode: "none",
                    start: 0,
                    end: 100,
                    zoomOnMouseWheel: false,
                    moveOnMouseWheel: false,
                },
                {
                    type: "slider",
                    bottom: 12,
                    height: 22,
                    borderColor: splitColor,
                    backgroundColor: colors.backgroundMuted,
                    fillerColor: dark ? "rgba(57,201,155,.16)" : "rgba(15,159,120,.13)",
                    handleStyle: { color: "#10b981", borderColor: "#10b981" },
                    textStyle: { color: axisColor },
                },
            ],
            series: selectedMaturities.map((key, index) => ({
                name: maturityByKey.get(key)?.label ?? key,
                type: "line",
                data: data.observations.map((observation) => observation.yields[key] ?? null),
                showSymbol: false,
                connectNulls: false,
                smooth: 0.08,
                lineStyle: { width: 2.2, color: SERIES_COLORS[index % SERIES_COLORS.length] },
                itemStyle: { color: SERIES_COLORS[index % SERIES_COLORS.length] },
                emphasis: { focus: "series", lineStyle: { width: 3.2 } },
            })),
        };
    }, [dark, data, selectedMaturities]);

    return (
        <div onWheelCapture={(event) => event.stopPropagation()}>
            <ReactECharts
                option={option}
                notMerge
                lazyUpdate
                style={{ width: "100%", height: "440px" }}
                opts={{ renderer: "canvas" }}
            />
        </div>
    );
}

export function YieldSpreadChart({ data }: { data: TreasuryYieldCurveResponse }) {
    const { resolvedTheme } = useTheme();
    const dark = resolvedTheme === "dark";
    const option = useMemo(() => {
        const { colors, axisColor, splitColor } = baseChartColors(dark);
        const spread = (longKey: string, shortKey: string) => data.observations.map((observation) => {
            const longYield = observation.yields[longKey];
            const shortYield = observation.yields[shortKey];
            return longYield == null || shortYield == null
                ? null
                : Math.round((longYield - shortYield) * 100) / 100;
        });
        return {
            animationDuration: 250,
            aria: {
                enabled: true,
                description: "Historical 10-year minus 2-year and 10-year minus 3-month Treasury yield spreads.",
            },
            color: ["#10b981", "#f59e0b"],
            legend: { top: 4, textStyle: { color: axisColor, fontSize: 11 } },
            grid: { left: 58, right: 30, top: 48, bottom: 66 },
            tooltip: {
                trigger: "axis",
                confine: true,
                backgroundColor: colors.backgroundMuted,
                borderColor: colors.border,
                textStyle: { color: colors.text, fontSize: 12 },
                valueFormatter: (value: unknown) => {
                    if (value == null) return "—";
                    const number = Number(value);
                    return Number.isFinite(number) ? `${number.toFixed(2)} pp` : "—";
                },
                axisPointer: { type: "cross", snap: true },
            },
            xAxis: {
                type: "category",
                data: data.observations.map((observation) => observation.date),
                boundaryGap: false,
                axisLine: { lineStyle: { color: splitColor } },
                axisTick: { show: false },
                axisLabel: { color: axisColor, formatter: dateLabel },
            },
            yAxis: {
                type: "value",
                scale: true,
                name: "Percentage points",
                nameTextStyle: { color: axisColor },
                axisLabel: { color: axisColor, formatter: "{value}" },
                splitLine: { lineStyle: { color: splitColor } },
            },
            dataZoom: [
                { type: "inside", filterMode: "none", start: 0, end: 100, zoomOnMouseWheel: false, moveOnMouseWheel: false },
                {
                    type: "slider",
                    bottom: 12,
                    height: 22,
                    borderColor: splitColor,
                    backgroundColor: colors.backgroundMuted,
                    fillerColor: dark ? "rgba(57,201,155,.16)" : "rgba(15,159,120,.13)",
                    handleStyle: { color: "#10b981", borderColor: "#10b981" },
                    textStyle: { color: axisColor },
                },
            ],
            series: [
                { name: "10Y − 2Y", data: spread("10y", "2y"), color: "#10b981" },
                { name: "10Y − 3M", data: spread("10y", "3m"), color: "#f59e0b" },
            ].map((series, index) => ({
                ...series,
                type: "line",
                showSymbol: false,
                connectNulls: false,
                smooth: 0.08,
                lineStyle: { width: 2.3, color: series.color },
                itemStyle: { color: series.color },
                emphasis: { focus: "series" },
                markLine: index === 0 ? {
                    silent: true,
                    symbol: "none",
                    label: { show: false },
                    lineStyle: { color: axisColor, type: "dashed", opacity: 0.75 },
                    data: [{ yAxis: 0 }],
                } : undefined,
            })),
        };
    }, [dark, data]);

    return (
        <div onWheelCapture={(event) => event.stopPropagation()}>
            <ReactECharts
                option={option}
                notMerge
                lazyUpdate
                style={{ width: "100%", height: "390px" }}
                opts={{ renderer: "canvas" }}
            />
        </div>
    );
}
