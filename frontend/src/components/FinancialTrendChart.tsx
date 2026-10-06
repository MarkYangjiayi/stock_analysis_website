"use client";

import { useMemo, useState } from "react";
import ReactECharts from "echarts-for-react";
import { useTheme } from "next-themes";
import { chartMonoFont, chartTheme } from "@/lib/chartTheme";
import { AlertTriangle, ExternalLink, ShieldCheck, X } from "lucide-react";
import { EarningsQualityResponse, HistoricalFinancialPoint, ValuationMetrics } from "@/lib/api";

export type FinancialEvidenceMetric =
    | "overview"
    | "revenue"
    | "net_income"
    | "free_cash_flow"
    | "gross_margin"
    | "operating_margin"
    | "cash_and_short_term_investments"
    | "total_debt"
    | "debt_to_equity"
    | "shares_outstanding";

interface FinancialTrendChartProps {
    data?: HistoricalFinancialPoint[];
    ttmData?: ValuationMetrics["ttm"];
    currentPrice?: number;
    timePeriod: "annual" | "ttm" | "quarterly";
    onTimePeriodChange: (period: "annual" | "ttm" | "quarterly") => void;
    selectedMetric?: FinancialEvidenceMetric;
    onMetricChange?: (metric: FinancialEvidenceMetric) => void;
    earningsQuality?: EarningsQualityResponse | null;
    dataQualityWarnings?: Array<{ code: string; message: string }>;
}

type ChartColors = ReturnType<typeof chartTheme>;

// Canvas text cannot resolve CSS variables; the HTML tooltip can.

// Overview series share these hues with their single-metric views. Caution (violet)
// is reserved for earnings-quality flags, and positive for verified normalized income.
const METRICS: Array<{
    key: FinancialEvidenceMetric;
    label: string;
    unit: "currency" | "percent" | "count" | "ratio";
    color: (colors: ChartColors) => string;
}> = [
    { key: "overview", label: "Financial overview", unit: "currency", color: (colors) => colors.brand },
    { key: "revenue", label: "Revenue", unit: "currency", color: (colors) => colors.brand },
    { key: "net_income", label: "Net income", unit: "currency", color: (colors) => colors.series[1] },
    { key: "free_cash_flow", label: "Free cash flow", unit: "currency", color: (colors) => colors.series[4] },
    { key: "gross_margin", label: "Gross margin", unit: "percent", color: (colors) => colors.series[3] },
    { key: "operating_margin", label: "Operating margin", unit: "percent", color: (colors) => colors.series[6] },
    { key: "cash_and_short_term_investments", label: "Cash + short-term investments", unit: "currency", color: (colors) => colors.series[5] },
    { key: "total_debt", label: "Total debt", unit: "currency", color: (colors) => colors.negative },
    { key: "debt_to_equity", label: "Debt / equity", unit: "ratio", color: (colors) => colors.negative },
    { key: "shares_outstanding", label: "Shares outstanding", unit: "count", color: (colors) => colors.series[7] },
];

const metricColor = (colors: ChartColors, key: FinancialEvidenceMetric) => METRICS.find((item) => item.key === key)!.color(colors);

const compact = (value: number) => {
    const absolute = Math.abs(value);
    if (absolute >= 1e12) return `${(value / 1e12).toFixed(1)}T`;
    if (absolute >= 1e9) return `${(value / 1e9).toFixed(1)}B`;
    if (absolute >= 1e6) return `${(value / 1e6).toFixed(1)}M`;
    return value.toLocaleString(undefined, { maximumFractionDigits: 1 });
};

const valueForMetric = (point: HistoricalFinancialPoint, metric: FinancialEvidenceMetric) => {
    if (metric === "overview") return null;
    const value = point[metric];
    if (value == null) return null;
    return metric === "gross_margin" || metric === "operating_margin" ? value * 100 : value;
};

interface TooltipParam {
    axisValue: string;
    marker: string;
    seriesName: string;
    value: number | [string, number] | null;
}

interface ChartClickParam {
    seriesName?: string;
    data?: { periodEnd?: string };
}

export default function FinancialTrendChart({
    data,
    ttmData,
    currentPrice,
    timePeriod,
    onTimePeriodChange,
    selectedMetric,
    onMetricChange,
    earningsQuality,
    dataQualityWarnings = [],
}: FinancialTrendChartProps) {
    const { resolvedTheme } = useTheme();
    const [internalMetric, setInternalMetric] = useState<FinancialEvidenceMetric>("overview");
    const [selectedEarningsPeriodKey, setSelectedEarningsPeriodKey] = useState<string | null>(null);
    const metric = selectedMetric ?? internalMetric;
    const showEarningsQualityOverlay = metric === "overview" || metric === "net_income";
    const visibleEarningsPeriods = showEarningsQualityOverlay
        ? timePeriod === "quarterly"
            ? earningsQuality?.quarterly ?? []
            : earningsQuality?.annual ?? []
        : [];
    const selectedEarningsPeriod = visibleEarningsPeriods.find((period) => (
        `${earningsQuality?.ticker}:${timePeriod}:${period.period_end}` === selectedEarningsPeriodKey
    )) ?? null;

    const changeMetric = (next: FinancialEvidenceMetric) => {
        setInternalMetric(next);
        onMetricChange?.(next);
    };

    const options = useMemo(() => {
        if (!data?.length) return {};
        const points: HistoricalFinancialPoint[] = data.map((point) => ({ ...point }));
        if (timePeriod === "ttm" && ttmData) {
            points.push({
                date: "TTM (Current)",
                revenue: ttmData.revenue,
                net_income: ttmData.net_income,
                gross_margin: ttmData.revenue > 0 ? ttmData.gross_profit / ttmData.revenue : 0,
                free_cash_flow: ttmData.free_cash_flow,
                price: currentPrice ?? null,
            });
        }

        const isDark = resolvedTheme === "dark";
        const colors = chartTheme(isDark);
        const textColor = colors.textMuted;
        const gridColor = colors.grid;
        const axisText = { color: textColor, fontFamily: chartMonoFont() };
        const dates = points.map((point) => point.date);
        const earningsPeriods = showEarningsQualityOverlay
            ? timePeriod === "quarterly"
                ? earningsQuality?.quarterly ?? []
                : earningsQuality?.annual ?? []
            : [];
        const earningsByDate = new Map(earningsPeriods.map((period) => [period.period_end, period]));
        const amountAxis = {
            type: "value",
            name: "Amount",
            axisLabel: { ...axisText, formatter: (value: number) => `$${compact(value)}` },
            nameTextStyle: { color: textColor },
            splitLine: { lineStyle: { color: gridColor, type: "dashed" } },
        };
        const percentAxis = {
            type: "value",
            name: "Margin (%)",
            position: "right",
            axisLabel: { ...axisText, formatter: "{value}%" },
            nameTextStyle: { color: textColor },
            splitLine: { show: false },
        };
        const priceAxis = {
            type: "value",
            name: "Matched price",
            position: "right",
            axisLabel: { ...axisText, formatter: (value: number) => `$${value.toFixed(0)}` },
            nameTextStyle: { color: textColor },
            splitLine: { show: false },
        };
        const ratioAxis = {
            type: "value",
            name: "Debt / equity (x)",
            axisLabel: { ...axisText, formatter: (value: number) => `${value.toFixed(2)}x` },
            nameTextStyle: { color: textColor },
            splitLine: { lineStyle: { color: gridColor, type: "dashed" } },
        };
        const priceSeries = (axisIndex: number) => ({
            name: "Matched stock price",
            type: "line",
            yAxisIndex: axisIndex,
            data: points.map((point) => point.price ?? null),
            itemStyle: { color: colors.series[7] },
            lineStyle: { width: 2, type: "dotted" },
            symbol: "diamond",
            connectNulls: true,
        });

        let yAxis: Array<Record<string, unknown>>;
        let series: Array<Record<string, unknown>>;
        if (metric === "overview") {
            yAxis = [amountAxis, percentAxis, { ...priceAxis, offset: 72 }];
            series = [
                { name: "Revenue", type: "bar", yAxisIndex: 0, data: points.map((point) => point.revenue), itemStyle: { color: metricColor(colors, "revenue"), borderRadius: [4, 4, 0, 0] }, barMaxWidth: 28 },
                { name: "Net income", type: "bar", yAxisIndex: 0, data: points.map((point) => point.net_income), itemStyle: { color: metricColor(colors, "net_income"), borderRadius: [4, 4, 0, 0] }, barMaxWidth: 28 },
                { name: "Free cash flow", type: "bar", yAxisIndex: 0, data: points.map((point) => point.free_cash_flow ?? null), itemStyle: { color: metricColor(colors, "free_cash_flow"), borderRadius: [4, 4, 0, 0] }, barMaxWidth: 28 },
                { name: "Gross margin", type: "line", yAxisIndex: 1, data: points.map((point) => point.gross_margin * 100), itemStyle: { color: metricColor(colors, "gross_margin") }, lineStyle: { width: 2.5 }, symbolSize: 7 },
                { name: "Operating margin", type: "line", yAxisIndex: 1, data: points.map((point) => point.operating_margin == null ? null : point.operating_margin * 100), itemStyle: { color: metricColor(colors, "operating_margin") }, lineStyle: { width: 2.5 }, symbolSize: 7 },
                priceSeries(2),
            ];
        } else {
            const config = METRICS.find((item) => item.key === metric)!;
            const primaryAxis = config.unit === "percent"
                ? { ...percentAxis, position: "left", name: config.label }
                : config.unit === "ratio"
                    ? ratioAxis
                : {
                    ...amountAxis,
                    name: config.unit === "count" ? "Shares" : config.label,
                    axisLabel: {
                        ...axisText,
                        formatter: (value: number) => config.unit === "count" ? compact(value) : `$${compact(value)}`,
                    },
                };
            yAxis = [primaryAxis, priceAxis];
            series = [
                {
                    name: config.label,
                    type: config.unit === "percent" || config.unit === "ratio" ? "line" : "bar",
                    yAxisIndex: 0,
                    data: points.map((point) => valueForMetric(point, metric)),
                    itemStyle: { color: config.color(colors), borderRadius: [4, 4, 0, 0] },
                    lineStyle: { width: 3 },
                    symbolSize: 8,
                    barMaxWidth: 38,
                },
                priceSeries(1),
            ];
        }

        if (metric === "overview" || metric === "net_income") {
            const normalizedValues = points.map((point) => earningsByDate.get(point.date)?.verified_normalized?.net_income ?? null);
            if (normalizedValues.some((value) => value != null)) {
                series.push({
                    name: "Verified normalized net income",
                    type: "line",
                    yAxisIndex: 0,
                    data: normalizedValues,
                    itemStyle: { color: colors.positive },
                    lineStyle: { width: 2.5, type: "dashed" },
                    symbol: "circle",
                    symbolSize: 7,
                    connectNulls: false,
                    z: 5,
                });
            }
        }

        const flaggedMarkers = earningsPeriods.flatMap((period) => {
            if ((!period.flags.length && !period.data_quality_warnings.length) || !dates.includes(period.period_end)) return [];
            const point = points.find((candidate) => candidate.date === period.period_end);
            if (!point) return [];
            const yValue = metric === "overview"
                ? point.net_income
                : valueForMetric(point, metric);
            if (yValue == null) return [];
            return [{
                name: "Earnings-quality flag",
                value: [period.period_end, yValue] as [string, number],
                periodEnd: period.period_end,
                itemStyle: {
                    color: period.flags.some((flag) => flag.severity === "high")
                        ? colors.negative
                        : period.flags.length
                            ? colors.caution
                            : colors.textMuted,
                },
            }];
        });
        if (flaggedMarkers.length) {
            series.push({
                name: "Earnings-quality flag",
                type: "scatter",
                yAxisIndex: 0,
                data: flaggedMarkers,
                symbol: "pin",
                symbolSize: 34,
                z: 10,
            });
        }

        return {
            aria: { enabled: true, description: `Historical financial evidence focused on ${metric.replaceAll("_", " ")}` },
            animationDuration: 300,
            tooltip: {
                trigger: "axis",
                backgroundColor: colors.tooltipBackground,
                borderColor: colors.border,
                textStyle: { color: colors.text, fontFamily: chartMonoFont() },
                axisPointer: { type: "line", lineStyle: { color: colors.textMuted, type: "dashed" } },
                formatter: (params: TooltipParam[]) => {
                    if (!params.length) return "";
                    const rows = params.filter((param) => param.value != null).map((param) => {
                        if (param.seriesName === "Earnings-quality flag") {
                            return `<div style="margin-top:6px"><strong>${param.marker}Earnings-quality details</strong> · click marker</div>`;
                        }
                        const isPercent = param.seriesName.toLowerCase().includes("margin");
                        const isPrice = param.seriesName === "Matched stock price";
                        const isShares = param.seriesName === "Shares outstanding";
                        const isRatio = param.seriesName === "Debt / equity";
                        const shown = isPercent
                            ? `${Number(param.value).toFixed(2)}%`
                            : isRatio
                                ? `${Number(param.value).toFixed(2)}x`
                            : isShares
                                ? compact(Number(param.value))
                                : isPrice
                                    ? `$${Number(param.value).toFixed(2)}`
                                    : `$${compact(Number(param.value))}`;
                        return `<div style="display:flex;justify-content:space-between;gap:24px;margin-top:6px"><span>${param.marker}${param.seriesName}</span><strong>${shown}</strong></div>`;
                    }).join("");
                    return `<strong>${params[0].axisValue}</strong>${rows}`;
                },
            },
            legend: { top: 0, textStyle: { color: textColor, fontWeight: 600 } },
            grid: { left: "3%", right: "5%", bottom: 52, top: 54, containLabel: true },
            dataZoom: [
                {
                    type: "inside",
                    startValue: Math.max(0, dates.length - 20),
                    endValue: dates.length - 1,
                    zoomOnMouseWheel: false,
                    moveOnMouseWheel: false,
                },
                { type: "slider", bottom: 4, height: 24, startValue: Math.max(0, dates.length - 20), endValue: dates.length - 1, borderColor: colors.border, textStyle: axisText, fillerColor: `${colors.brand}24` },
            ],
            xAxis: { type: "category", data: dates, axisLabel: axisText, axisTick: { show: false }, axisLine: { lineStyle: { color: colors.border } } },
            yAxis,
            series,
        };
    }, [currentPrice, data, earningsQuality, metric, resolvedTheme, showEarningsQualityOverlay, timePeriod, ttmData]);

    const chartEvents = {
        click: (params: ChartClickParam) => {
            if (!showEarningsQualityOverlay || params.seriesName !== "Earnings-quality flag" || !params.data?.periodEnd) return;
            setSelectedEarningsPeriodKey(`${earningsQuality?.ticker}:${timePeriod}:${params.data.periodEnd}`);
        },
    };

    if (!data?.length) return null;

    return (
        <section className="surface-panel overflow-hidden" aria-labelledby="financial-trends-title">
            <header className="surface-subtle flex flex-col justify-between gap-4 border-b p-4 sm:flex-row sm:items-center sm:px-5">
                <div>
                    <p className="eyebrow">Fundamental history</p>
                    <h2 id="financial-trends-title" className="mt-1 font-semibold">Financial evidence</h2>
                </div>
                <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                    <div className="segmented-control">
                        {(["annual", "ttm", "quarterly"] as const).map((period) => (
                            <button key={period} type="button" onClick={() => onTimePeriodChange(period)} aria-pressed={timePeriod === period} className="segmented-control-item flex-1">
                                {period === "annual" ? "Annual" : period === "ttm" ? "Annual + TTM" : "Quarterly"}
                            </button>
                        ))}
                    </div>
                    <select value={metric} onChange={(event) => changeMetric(event.target.value as FinancialEvidenceMetric)} aria-label="Financial evidence metric" className="control-field py-2 text-xs font-semibold sm:w-64">
                        {METRICS.map((item) => <option key={item.key} value={item.key}>{item.label}</option>)}
                    </select>
                </div>
            </header>
            <div className="h-[420px] p-2 sm:h-[470px] sm:p-4" onWheelCapture={(event) => event.stopPropagation()}>
                <ReactECharts option={options} onEvents={chartEvents} style={{ height: "100%", width: "100%" }} notMerge lazyUpdate />
            </div>
            {timePeriod === "ttm" && dataQualityWarnings.length > 0 && <div className="mx-4 mb-4 rounded-lg border border-caution/30 bg-caution/6 p-3 text-xs text-caution"><AlertTriangle className="mr-1.5 inline" size={14} /><strong>Reported data-quality warning:</strong> {dataQualityWarnings.map((warning) => warning.message).join(" ")}</div>}
            {showEarningsQualityOverlay && selectedEarningsPeriod && <aside className="m-4 mt-0 rounded-lg border p-4" aria-label={`Earnings-quality details for ${selectedEarningsPeriod.period_end}`}>
                <div className="flex items-start justify-between gap-3"><div><div className="flex flex-wrap items-center gap-2"><h3 className="text-sm font-semibold">{selectedEarningsPeriod.period_end} earnings quality</h3>{selectedEarningsPeriod.verified_normalized?.net_income != null && <span className="inline-flex items-center gap-1 rounded-full bg-up/10 px-2 py-0.5 text-xs font-semibold text-up"><ShieldCheck size={11} /> Verified normalized</span>}</div><p className="mt-1 text-xs text-fg-muted">Reported net income remains the primary series. Marker values are screening evidence only.</p></div><button type="button" className="rounded-md p-1 text-fg-muted hover:bg-surface-muted hover:text-fg" onClick={() => setSelectedEarningsPeriodKey(null)} aria-label="Close earnings-quality details"><X size={16} /></button></div>
                <div className="mt-3 grid gap-2 md:grid-cols-2">{selectedEarningsPeriod.flags.map((flag, index) => <div key={`${flag.category}-${index}`} className="rounded-lg bg-surface-muted p-3 text-xs"><div className="flex justify-between gap-2"><span className="font-semibold">{flag.label}</span><span className="font-mono">{(flag.materiality_ratio * 100).toFixed(1)}% of base</span></div><p className="mt-1 leading-5 text-fg-muted">{flag.detail}</p></div>)}</div>
                {selectedEarningsPeriod.data_quality_warnings.map((warning) => <div key={warning.code} className="mt-2 rounded-lg border border-caution/30 bg-caution/6 p-3 text-xs text-caution"><strong>{warning.code}</strong><p className="mt-1">{warning.message}</p></div>)}
                {selectedEarningsPeriod.analysis?.source_snapshots.length ? <div className="mt-3 flex flex-wrap gap-2">{selectedEarningsPeriod.analysis.source_snapshots.map((source) => source.source_url && <a key={source.source_id} href={source.source_url} target="_blank" rel="noreferrer" className="secondary-button min-h-8 px-2.5 py-1 text-xs"><ExternalLink size={12} /> {source.form} source</a>)}</div> : null}
            </aside>}
        </section>
    );
}
