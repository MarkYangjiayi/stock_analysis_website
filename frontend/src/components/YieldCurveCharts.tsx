"use client";

import { useMemo } from "react";
import ReactECharts from "echarts-for-react";
import { useTheme } from "next-themes";

import type { TreasuryYieldCurveResponse } from "@/lib/api";
import { chartMonoFont, chartTheme } from "@/lib/chartTheme";

const dateLabel = (value: string) => value.slice(5);
const yieldLabel = (value: unknown) => {
    if (value == null) return "—";
    const number = Number(value);
    return Number.isFinite(number) ? `${number.toFixed(2)}%` : "—";
};

function baseChartColors(dark: boolean) {
    const colors = chartTheme(dark);
    const monoFont = chartMonoFont();
    return {
        colors,
        monoFont,
        axisColor: colors.textMuted,
        axisLabel: { color: colors.textMuted, fontFamily: monoFont },
        splitLine: { lineStyle: { color: colors.grid, type: "dashed" as const } },
        tooltip: {
            backgroundColor: colors.tooltipBackground,
            borderColor: colors.border,
            textStyle: { color: colors.text, fontSize: 12, fontFamily: monoFont },
        },
        dataZoomSlider: {
            type: "slider",
            bottom: 12,
            height: 22,
            borderColor: colors.border,
            backgroundColor: colors.backgroundMuted,
            fillerColor: `${colors.brand}22`,
            dataBackground: {
                lineStyle: { color: colors.textMuted, opacity: 0.5 },
                areaStyle: { color: colors.textMuted, opacity: 0.12 },
            },
            handleStyle: { color: colors.brand, borderColor: colors.brand },
            moveHandleStyle: { color: colors.brand },
            textStyle: { color: colors.textMuted, fontFamily: monoFont },
        },
    };
}

export function CurrentYieldCurveChart({ data }: { data: TreasuryYieldCurveResponse }) {
    const { resolvedTheme } = useTheme();
    const dark = resolvedTheme === "dark";
    const option = useMemo(() => {
        const { colors, monoFont, axisColor, axisLabel, splitLine, tooltip } = baseChartColors(dark);
        // The latest curve owns the brand hue; prior snapshots cycle through the remaining series hues.
        const priorColors = colors.series.slice(1);
        let priorIndex = 0;
        const snapshotColors = data.snapshots.map((snapshot) =>
            snapshot.key === "latest" ? colors.brand : priorColors[priorIndex++ % priorColors.length]);
        return {
            animationDuration: 350,
            aria: {
                enabled: true,
                description: "Current U.S. Treasury par yield curve compared with prior monthly and annual snapshots.",
            },
            color: snapshotColors,
            legend: {
                top: 4,
                textStyle: { color: axisColor, fontSize: 11, fontFamily: monoFont },
            },
            grid: { left: 58, right: 28, top: 48, bottom: 45 },
            tooltip: {
                trigger: "axis",
                confine: true,
                ...tooltip,
                valueFormatter: yieldLabel,
                axisPointer: { type: "line", lineStyle: { color: colors.border } },
            },
            xAxis: {
                type: "category",
                data: data.maturities.map((maturity) => maturity.label),
                boundaryGap: false,
                axisLine: { lineStyle: { color: colors.border } },
                axisTick: { show: false },
                axisLabel: { ...axisLabel, interval: 0, fontSize: 10 },
            },
            yAxis: {
                type: "value",
                scale: true,
                name: "Yield %",
                nameTextStyle: { color: axisColor },
                axisLabel: { ...axisLabel, formatter: "{value}%" },
                splitLine,
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
                    color: snapshotColors[index],
                },
                itemStyle: { color: snapshotColors[index] },
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
        const { colors, monoFont, axisColor, axisLabel, splitLine, tooltip, dataZoomSlider } = baseChartColors(dark);
        const maturityByKey = new Map(data.maturities.map((maturity) => [maturity.key, maturity]));
        const seriesColors = colors.series;
        return {
            animationDuration: 250,
            aria: {
                enabled: true,
                description: "Historical U.S. Treasury par yields for the selected maturities.",
            },
            color: seriesColors,
            legend: {
                top: 4,
                data: selectedMaturities.map((key) => maturityByKey.get(key)?.label ?? key),
                textStyle: { color: axisColor, fontSize: 11, fontFamily: monoFont },
            },
            grid: { left: 58, right: 30, top: 48, bottom: 66 },
            tooltip: {
                trigger: "axis",
                confine: true,
                ...tooltip,
                valueFormatter: yieldLabel,
                axisPointer: { type: "cross", snap: true, lineStyle: { color: colors.border } },
            },
            xAxis: {
                type: "category",
                data: data.observations.map((observation) => observation.date),
                boundaryGap: false,
                axisLine: { lineStyle: { color: colors.border } },
                axisTick: { show: false },
                axisLabel: { ...axisLabel, formatter: dateLabel },
            },
            yAxis: {
                type: "value",
                scale: true,
                name: "Yield %",
                nameTextStyle: { color: axisColor },
                axisLabel: { ...axisLabel, formatter: "{value}%" },
                splitLine,
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
                dataZoomSlider,
            ],
            series: selectedMaturities.map((key, index) => ({
                name: maturityByKey.get(key)?.label ?? key,
                type: "line",
                data: data.observations.map((observation) => observation.yields[key] ?? null),
                showSymbol: false,
                connectNulls: false,
                smooth: 0.08,
                lineStyle: { width: 2.2, color: seriesColors[index % seriesColors.length] },
                itemStyle: { color: seriesColors[index % seriesColors.length] },
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
        const { colors, monoFont, axisColor, axisLabel, splitLine, tooltip, dataZoomSlider } = baseChartColors(dark);
        const spread = (longKey: string, shortKey: string) => data.observations.map((observation) => {
            const longYield = observation.yields[longKey];
            const shortYield = observation.yields[shortKey];
            return longYield == null || shortYield == null
                ? null
                : Math.round((longYield - shortYield) * 100) / 100;
        });
        // Spreads are two peer measures, not good/bad signals, so they use categorical hues.
        const tenTwoColor = colors.series[0];
        const tenThreeMonthColor = colors.series[1];
        return {
            animationDuration: 250,
            aria: {
                enabled: true,
                description: "Historical 10-year minus 2-year and 10-year minus 3-month Treasury yield spreads.",
            },
            color: [tenTwoColor, tenThreeMonthColor],
            legend: { top: 4, textStyle: { color: axisColor, fontSize: 11, fontFamily: monoFont } },
            grid: { left: 58, right: 30, top: 48, bottom: 66 },
            tooltip: {
                trigger: "axis",
                confine: true,
                ...tooltip,
                valueFormatter: (value: unknown) => {
                    if (value == null) return "—";
                    const number = Number(value);
                    return Number.isFinite(number) ? `${number.toFixed(2)} pp` : "—";
                },
                axisPointer: { type: "cross", snap: true, lineStyle: { color: colors.border } },
            },
            xAxis: {
                type: "category",
                data: data.observations.map((observation) => observation.date),
                boundaryGap: false,
                axisLine: { lineStyle: { color: colors.border } },
                axisTick: { show: false },
                axisLabel: { ...axisLabel, formatter: dateLabel },
            },
            yAxis: {
                type: "value",
                scale: true,
                name: "Percentage points",
                nameTextStyle: { color: axisColor },
                axisLabel: { ...axisLabel, formatter: "{value}" },
                splitLine,
            },
            dataZoom: [
                { type: "inside", filterMode: "none", start: 0, end: 100, zoomOnMouseWheel: false, moveOnMouseWheel: false },
                dataZoomSlider,
            ],
            series: [
                { name: "10Y − 2Y", data: spread("10y", "2y"), color: tenTwoColor },
                { name: "10Y − 3M", data: spread("10y", "3m"), color: tenThreeMonthColor },
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
