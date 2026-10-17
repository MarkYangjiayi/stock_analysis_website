"use client";

import { useMemo } from "react";
import ReactECharts from "echarts-for-react";
import { useTheme } from "next-themes";

import type { ThemeHistory, ThemeStage } from "@/lib/api";
import { chartMonoFont, chartTheme } from "@/lib/chartTheme";
import { STAGE_LABELS, displayTicker, formatHeat, formatShare, formatSigned } from "./themeFormat";

interface AxisTooltipParam {
    dataIndex?: number;
}

// Heat weights from the scoring spec: H = 0.40 P + 0.30 B + 0.15 V + 0.15 A.
const COMPONENTS = [
    { key: "score_price", label: "Price", weight: 0.4 },
    { key: "score_breadth", label: "Breadth", weight: 0.3 },
    { key: "score_volume", label: "Volume", weight: 0.15 },
    { key: "score_attention", label: "Attention", weight: 0.15 },
] as const;

const asReturn = (value: number | null | undefined) =>
    value == null || !Number.isFinite(value) ? null : (value - 1) * 100;

const asPercentPoints = (value: number | null | undefined) =>
    value == null || !Number.isFinite(value) ? null : value * 100;

const signedPercent = (value: number | null) => (value == null ? "—" : `${formatSigned(value, 1)}%`);

/** Linked daily panels: heat (with stage bands), weighted components, basket vs SPY vs proxy, breadth. */
export default function ThemeDetailChart({ history, proxyEtf }: { history: ThemeHistory; proxyEtf: string | null }) {
    const { resolvedTheme } = useTheme();
    const dark = resolvedTheme === "dark";

    const option = useMemo(() => {
        const colors = chartTheme(dark);
        const monoFont = chartMonoFont();
        const axisText = { color: colors.textMuted, fontFamily: monoFont };
        const stageColor: Partial<Record<ThemeStage, string>> = {
            emerging: colors.info,
            accelerating: colors.positive,
            leading: colors.brand,
            crowded: colors.caution,
            fading: colors.negative,
        };
        const componentColors = [colors.series[0], colors.series[1], colors.series[4], colors.series[3]];
        const proxyLabel = proxyEtf ? displayTicker(proxyEtf) : "Proxy ETF";
        const basket = history.basket_index.map(asReturn);
        const benchmark = history.benchmark_index.map(asReturn);
        const proxy = history.proxy_index.map(asReturn);
        const b50 = history.pct_above_ma50.map(asPercentPoints);
        const nearHigh = history.near_high_share.map(asPercentPoints);

        // Contiguous stage runs become shaded bands behind the heat lines (neutral stays unshaded).
        // Each band also covers the step into its first session, so adjacent runs tile without gaps
        // and a one-session run (typically today's stage change) is still one step wide.
        const stageBands: Array<[Record<string, unknown>, Record<string, unknown>]> = [];
        let start = 0;
        history.stage.forEach((stage, index) => {
            const next = history.stage[index + 1];
            if (next === stage) return;
            const color = stageColor[stage];
            if (color) {
                stageBands.push([
                    { xAxis: history.dates[Math.max(start - 1, 0)], itemStyle: { color, opacity: dark ? 0.14 : 0.1 }, name: STAGE_LABELS[stage] },
                    { xAxis: history.dates[index] },
                ]);
            }
            start = index + 1;
        });

        const series: Array<Record<string, unknown>> = [
            {
                name: "Heat now",
                type: "line",
                xAxisIndex: 0,
                yAxisIndex: 0,
                data: history.heat_now,
                showSymbol: false,
                connectNulls: false,
                lineStyle: { width: 2.2, color: colors.brand },
                itemStyle: { color: colors.brand },
                markArea: stageBands.length ? { silent: true, label: { show: false }, data: stageBands } : undefined,
                markLine: {
                    silent: true,
                    symbol: "none",
                    label: { show: false },
                    lineStyle: { color: colors.textMuted, type: "dotted", opacity: 0.6 },
                    data: [{ yAxis: 50 }],
                },
            },
            {
                name: "Heat trend",
                type: "line",
                xAxisIndex: 0,
                yAxisIndex: 0,
                data: history.heat_trend,
                showSymbol: false,
                connectNulls: false,
                lineStyle: { width: 1.8, color: colors.text, type: "dashed" },
                itemStyle: { color: colors.text },
            },
            ...COMPONENTS.map((component, index) => ({
                name: component.label,
                type: "bar",
                stack: "components",
                xAxisIndex: 1,
                yAxisIndex: 1,
                data: history[component.key].map((value) => (value == null || !Number.isFinite(value) ? null : value * component.weight)),
                barMaxWidth: 8,
                itemStyle: { color: componentColors[index] },
            })),
            {
                name: "Theme basket",
                type: "line",
                xAxisIndex: 2,
                yAxisIndex: 2,
                data: basket,
                showSymbol: false,
                connectNulls: false,
                lineStyle: { width: 2.2, color: colors.brand },
                itemStyle: { color: colors.brand },
            },
            {
                name: "SPY",
                type: "line",
                xAxisIndex: 2,
                yAxisIndex: 2,
                data: benchmark,
                showSymbol: false,
                connectNulls: false,
                lineStyle: { width: 1.8, color: colors.text },
                itemStyle: { color: colors.text },
            },
            {
                name: proxyLabel,
                type: "line",
                xAxisIndex: 2,
                yAxisIndex: 2,
                data: proxy,
                showSymbol: false,
                connectNulls: false,
                lineStyle: { width: 1.8, color: colors.info, type: "dashed" },
                itemStyle: { color: colors.info },
            },
            {
                name: "% > MA50",
                type: "line",
                xAxisIndex: 3,
                yAxisIndex: 3,
                data: b50,
                showSymbol: false,
                connectNulls: false,
                lineStyle: { width: 2, color: colors.series[1] },
                itemStyle: { color: colors.series[1] },
            },
            {
                name: "Near 52W high",
                type: "line",
                xAxisIndex: 3,
                yAxisIndex: 3,
                data: nearHigh,
                showSymbol: false,
                connectNulls: false,
                lineStyle: { width: 2, color: colors.series[4] },
                itemStyle: { color: colors.series[4] },
            },
            {
                name: "Ignitions",
                type: "bar",
                xAxisIndex: 3,
                yAxisIndex: 4,
                data: history.ignitions,
                barMaxWidth: 6,
                itemStyle: { color: colors.brand, opacity: 0.55 },
            },
        ];

        const tooltipFormatter = (rawParams: AxisTooltipParam | AxisTooltipParam[]) => {
            const params = Array.isArray(rawParams) ? rawParams : [rawParams];
            const index = params.find((item) => item.dataIndex != null)?.dataIndex;
            if (index == null) return "";
            const row = (label: string, value: string) =>
                `<div style="display:flex;justify-content:space-between;gap:18px"><span>${label}</span><b>${value}</b></div>`;
            const divider = `<div style="border-top:1px solid ${colors.border};margin:6px 0"></div>`;
            const stage = history.stage[index];
            return `<div style="min-width:230px;color:${colors.text}">
                <div style="font-weight:600;margin-bottom:4px">${history.dates[index]}${history.point_in_time[index] === false ? ` <span style="color:${colors.caution}">· backfilled</span>` : ""}</div>
                ${row("Stage", stage ? STAGE_LABELS[stage] : "—")}
                ${row("Rank now", String(history.rank_now[index] ?? "—"))}
                ${row("Heat now / trend", `${formatHeat(history.heat_now[index])} / ${formatHeat(history.heat_trend[index])}`)}
                ${divider}
                ${COMPONENTS.map((component) => row(component.label, formatSigned(history[component.key][index]))).join("")}
                ${divider}
                ${row("Basket", signedPercent(basket[index]))}
                ${row("SPY", signedPercent(benchmark[index]))}
                ${row(proxyLabel, signedPercent(proxy[index]))}
                ${divider}
                ${row("% > MA50", formatShare(history.pct_above_ma50[index]))}
                ${row("Near 52W high", formatShare(history.near_high_share[index]))}
                ${row("Ignitions", String(history.ignitions[index] ?? "—"))}
            </div>`;
        };

        const categoryAxis = (gridIndex: number, showLabels: boolean) => ({
            type: "category",
            gridIndex,
            data: history.dates,
            boundaryGap: gridIndex === 1 || gridIndex === 3,
            axisLine: { lineStyle: { color: colors.border } },
            axisTick: { show: false },
            axisLabel: { ...axisText, show: showLabels, formatter: (value: string) => value.slice(5) },
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
        const titleStyle = { color: colors.text, fontSize: 13, fontWeight: 600 };
        const legendText = { color: colors.textMuted, fontSize: 11 };
        // Fixed pixel bands (title, legend, plot) so titles and legends never collide on phones.
        const PANELS = [
            { title: "Heat", top: 4, height: 150, legend: ["Heat now", "Heat trend"] },
            { title: "Weighted components", top: 222, height: 90, legend: COMPONENTS.map((component) => component.label) },
            { title: "Basket vs SPY vs proxy", top: 378, height: 150, legend: ["Theme basket", "SPY", proxyLabel] },
            { title: "Breadth and ignitions", top: 594, height: 130, legend: ["% > MA50", "Near 52W high", "Ignitions"] },
        ];

        return {
            animationDuration: 350,
            aria: {
                enabled: true,
                description: "Theme heat with stage bands, weighted heat components, the theme basket against SPY and its proxy ETF, and member breadth with daily ignitions.",
            },
            title: PANELS.map((panel) => ({ text: panel.title, left: 40, top: panel.top, textStyle: titleStyle })),
            legend: PANELS.map((panel) => ({
                type: "scroll",
                top: panel.top + 20,
                left: 40,
                right: 16,
                data: panel.legend,
                textStyle: legendText,
                pageTextStyle: { color: colors.textMuted },
                pageIconColor: colors.brand,
                pageIconInactiveColor: colors.border,
            })),
            tooltip: {
                trigger: "axis",
                confine: true,
                backgroundColor: colors.tooltipBackground,
                borderColor: colors.border,
                textStyle: { color: colors.text, fontSize: 12, fontFamily: monoFont },
                extraCssText: "max-height:72vh;overflow-y:auto;",
                axisPointer: { type: "line", snap: true },
                formatter: tooltipFormatter,
            },
            axisPointer: { link: [{ xAxisIndex: "all" }] },
            grid: PANELS.map((panel) => ({ left: 44, right: 40, top: panel.top + 48, height: panel.height })),
            xAxis: [categoryAxis(0, false), categoryAxis(1, false), categoryAxis(2, false), categoryAxis(3, true)],
            yAxis: [
                valueAxis(0, { min: 0, max: 100, interval: 25, scale: false }),
                valueAxis(1, { splitNumber: 3 }),
                valueAxis(2, { splitNumber: 4, axisLabel: { ...axisText, formatter: "{value}%" } }),
                valueAxis(3, { min: 0, max: 100, interval: 50, scale: false, axisLabel: { ...axisText, formatter: "{value}%" } }),
                valueAxis(3, {
                    position: "right",
                    min: 0,
                    minInterval: 1,
                    scale: false,
                    splitLine: { show: false },
                    axisLabel: { ...axisText, color: colors.brand },
                }),
            ],
            dataZoom: [
                {
                    type: "inside",
                    xAxisIndex: [0, 1, 2, 3],
                    filterMode: "none",
                    zoomOnMouseWheel: false,
                    moveOnMouseWheel: false,
                },
                {
                    type: "slider",
                    xAxisIndex: [0, 1, 2, 3],
                    filterMode: "none",
                    bottom: 12,
                    height: 20,
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
    }, [dark, history, proxyEtf]);

    if (history.dates.length === 0) {
        return <div className="flex h-[320px] items-center justify-center text-sm text-fg-muted">No published history for this theme yet.</div>;
    }

    return (
        <div onWheelCapture={(event) => event.stopPropagation()}>
            <ReactECharts option={option} notMerge lazyUpdate style={{ width: "100%", height: "860px" }} opts={{ renderer: "canvas" }} />
        </div>
    );
}
