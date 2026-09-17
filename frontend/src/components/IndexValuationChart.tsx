"use client";

import { useMemo } from "react";
import ReactECharts from "echarts-for-react";
import { useTheme } from "next-themes";

import type { IndexValuationResponse } from "@/lib/api";
import { chartTheme } from "@/lib/chartTheme";

const multiple = (value: number | null | undefined) =>
    value == null || !Number.isFinite(value) ? "N/M" : `${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}×`;
const escapeHtml = (value: string) =>
    value.replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]!);
const GAP_REASONS: Record<string, string> = {
    "insufficient-members": "Fewer index members on record than the publication gate requires.",
    "insufficient-coverage": "Company coverage fell below the publication gate this month.",
};

// Month-end points stay gaps when coverage failed the gate; no smoothing,
// interpolation or forward-fill ever bridges a missing month.
export function indexValuationChartOption(
    data: IndexValuationResponse,
    dark: boolean,
) {
    const colors = chartTheme(dark);
    const dates = data.points.map((point) => point.date);
    const text = { color: colors.textMuted, fontSize: 11 };
    return {
        animation: false,
        aria: {
            enabled: true,
            description:
                "S&P 500 month-end aggregate P/E history with an earners-only variant. Missing months are gaps.",
        },
        backgroundColor: colors.background,
        legend: {
            data: ["All members", "Earning companies only"],
            right: 12,
            top: 4,
            textStyle: text,
            itemWidth: 14,
            itemHeight: 8,
        },
        grid: { left: 12, right: 64, top: 38, bottom: 64 },
        xAxis: {
            type: "category",
            data: dates,
            boundaryGap: true,
            axisLine: { lineStyle: { color: colors.grid } },
            axisTick: { show: false },
            axisLabel: { ...text, hideOverlap: true, formatter: (value: string) => value.slice(0, 4) },
            axisPointer: { show: true },
        },
        yAxis: {
            type: "value",
            scale: true,
            axisLabel: { ...text, formatter: (v: number) => `${v.toLocaleString(undefined, { maximumFractionDigits: 1 })}×` },
            splitLine: { lineStyle: { color: colors.grid, type: "dashed" } },
        },
        dataZoom: [
            {
                type: "inside",
                filterMode: "none",
                zoomOnMouseWheel: false,
                moveOnMouseWheel: false,
            },
            {
                type: "slider",
                filterMode: "none",
                bottom: 12,
                height: 22,
                left: 12,
                right: 64,
                showDataShadow: false,
                borderColor: colors.grid,
                textStyle: text,
                fillerColor: dark ? "rgba(57,201,155,.16)" : "rgba(15,159,120,.12)",
            },
        ],
        tooltip: {
            trigger: "axis",
            confine: true,
            axisPointer: { type: "cross", snap: true },
            backgroundColor: colors.backgroundMuted,
            borderColor: colors.grid,
            textStyle: { color: colors.text, fontSize: 12 },
            formatter: (params: Array<{ dataIndex?: number }>) => {
                const index = params.find((param) => param.dataIndex != null)?.dataIndex;
                if (index == null || !data.points[index]) return "";
                const point = data.points[index];
                const coverage =
                    point.member_count > 0
                        ? `${Math.round((point.covered_count / point.member_count) * 100)}% of ${point.member_count} companies`
                        : "No members on record";
                return `<strong>${escapeHtml(point.date)}</strong>`
                    + `<div style="margin-top:6px"><strong>Index P/E: ${multiple(point.index_pe)}</strong></div>`
                    + `<div>Earning companies only: ${multiple(point.index_pe_earners)}</div>`
                    + `<div>Median company P/E: ${multiple(point.median_pe)}</div>`
                    + (point.index_pe == null
                        ? `<div style="max-width:260px;white-space:normal">${escapeHtml(GAP_REASONS[point.reason ?? ""] ?? "No valid aggregate this month.")}</div>`
                        : "")
                    + `<div style="margin-top:6px">Loss makers: ${point.loss_maker_count.toLocaleString()}</div>`
                    + `<div>Coverage: ${escapeHtml(coverage)}</div>`;
            },
        },
        series: [
            {
                id: "index-pe",
                name: "All members",
                type: "line",
                data: data.points.map((point) => point.index_pe),
                showSymbol: true,
                symbol: "circle",
                symbolSize: 3,
                connectNulls: false,
                smooth: false,
                lineStyle: { width: 1.8, color: colors.brand },
                itemStyle: { color: colors.brand },
                markLine: {
                    silent: true,
                    symbol: "none",
                    label: { show: false },
                    lineStyle: { color: colors.textMuted, type: "dashed", opacity: 0.7 },
                    data: data.stats.median_index_pe != null ? [{ yAxis: data.stats.median_index_pe }] : [],
                },
            },
            {
                id: "index-pe-earners",
                name: "Earning companies only",
                type: "line",
                data: data.points.map((point) => point.index_pe_earners),
                showSymbol: false,
                connectNulls: false,
                smooth: false,
                lineStyle: { width: 1.2, color: colors.series[0], opacity: 0.9 },
                itemStyle: { color: colors.series[0] },
            },
        ],
    };
}

export default function IndexValuationChart({ data }: { data: IndexValuationResponse }) {
    const { resolvedTheme } = useTheme();
    const option = useMemo(
        () => indexValuationChartOption(data, resolvedTheme === "dark"),
        [data, resolvedTheme],
    );
    return (
        <div
            className="h-[440px] w-full"
            role="img"
            aria-label="S&P 500 month-end aggregate P/E history"
            onWheelCapture={(event) => event.stopPropagation()}
        >
            <ReactECharts option={option} notMerge lazyUpdate style={{ width: "100%", height: "100%" }} opts={{ renderer: "canvas" }} />
        </div>
    );
}
