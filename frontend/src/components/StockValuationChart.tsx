"use client";

import { useMemo, useState } from "react";
import ReactECharts from "echarts-for-react";
import { useTheme } from "next-themes";
import { Info } from "lucide-react";

import type { HistoricalDataPoint, MultipleKey, ValuationHistoryResponse } from "@/lib/api";
import { chartTheme } from "@/lib/chartTheme";
import { SegmentedControl } from "@/components/ui/SegmentedControl";

const MULTIPLES: Array<{ key: MultipleKey; label: string }> = [
    { key: "pe", label: "P/E" }, { key: "ps", label: "P/S" },
    { key: "pb", label: "P/B" }, { key: "pfcf", label: "P/FCF" },
    { key: "ev_revenue", label: "EV/Revenue" }, { key: "ev_ebitda", label: "EV/EBITDA" },
];
const multiple = (value: number | null | undefined) => value == null || !Number.isFinite(value) ? "N/M" : `${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}×`;
const escapeHtml = (value: string) => value.replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]!);

interface Props {
    data: HistoricalDataPoint[];
    history?: ValuationHistoryResponse | null;
    interval: string;
    onIntervalChange: (interval: string) => void;
    isLoading?: boolean;
}

// Both price and multiples use the same category dates, axis pointer and zoom.
// Explicit nulls remain gaps; no smoothing or forward-fill hides missing inputs.
export function valuationChartOption(
    data: HistoricalDataPoint[], history: ValuationHistoryResponse | null | undefined,
    key: MultipleKey, dark: boolean, zoom: { start: number; end: number },
) {
    const colors = chartTheme(dark);
    const metadata = history?.metrics.find((item) => item.key === key);
    const label = MULTIPLES.find((item) => item.key === key)!.label;
    const points = new Map(history?.points.map((point) => [point.date, point]));
    const bases = new Map(history?.bases.map((basis) => [basis.id, basis]));
    const values = data.map((point) => points.get(point.date)?.values[key] ?? null);
    const dates = data.map((point) => point.date);
    const text = { color: colors.textMuted, fontSize: 11 };
    return {
        animation: false,
        aria: { enabled: true, description: `Logarithmic stock price and ${label} history on a shared timeline. Missing multiples are gaps.` },
        backgroundColor: colors.background,
        title: [
            { text: "PRICE · LOG", left: 12, top: 8, textStyle: { ...text, fontWeight: 600 } },
            { text: `${label.toUpperCase()} · ${key === "pb" ? "LATEST QUARTER" : "TTM"}`, left: 12, top: "62%", textStyle: { ...text, fontWeight: 600 } },
        ],
        legend: { data: ["MA20", "MA50"], right: 12, top: 4, textStyle: text, itemWidth: 14, itemHeight: 8 },
        grid: [
            { left: 12, right: 78, top: 38, height: "42%" },
            { left: 12, right: 78, top: "49%", height: "9%" },
            { left: 12, right: 78, top: "67%", height: "22%" },
        ],
        axisPointer: { link: [{ xAxisIndex: "all" }], label: { backgroundColor: colors.textMuted } },
        xAxis: [0, 1, 2].map((gridIndex) => ({
            type: "category", gridIndex, data: dates, boundaryGap: true,
            axisLine: { lineStyle: { color: colors.grid } },
            axisTick: { show: false }, axisLabel: { ...text, show: gridIndex === 2, hideOverlap: true },
            axisPointer: { show: true },
        })),
        yAxis: [
            { type: "log", gridIndex: 0, position: "right", scale: true, min: (extent: { min: number }) => extent.min * 0.9, max: (extent: { max: number }) => extent.max * 1.1, axisLabel: { ...text, showMinLabel: false, formatter: (v: number) => v.toLocaleString(undefined, { maximumFractionDigits: 2 }) }, splitLine: { lineStyle: { color: colors.grid } } },
            { type: "value", gridIndex: 1, position: "right", axisLabel: { ...text, formatter: (v: number) => v >= 1e9 ? `${(v / 1e9).toFixed(0)}B` : `${(v / 1e6).toFixed(0)}M` }, splitLine: { show: false }, splitNumber: 1 },
            { type: "value", gridIndex: 2, position: "right", scale: true, axisLabel: { ...text, formatter: (v: number) => `${v.toLocaleString(undefined, { maximumFractionDigits: 1 })}×` }, splitLine: { lineStyle: { color: colors.grid, type: "dashed" } }, splitNumber: 3 },
        ],
        dataZoom: [
            { id: "history-inside", type: "inside", xAxisIndex: [0, 1, 2], filterMode: "filter", ...zoom },
            { id: "history-slider", type: "slider", xAxisIndex: [0, 1, 2], filterMode: "filter", ...zoom, bottom: 4, height: 22, left: 12, right: 78, showDataShadow: false, borderColor: colors.grid, textStyle: text, fillerColor: dark ? "rgba(57,201,155,.16)" : "rgba(15,159,120,.12)" },
        ],
        tooltip: {
            trigger: "axis", confine: true, axisPointer: { type: "cross" },
            backgroundColor: colors.backgroundMuted, borderColor: colors.grid,
            textStyle: { color: colors.text, fontSize: 12 },
            formatter: (params: Array<{ dataIndex?: number }>) => {
                const index = params.find((param) => param.dataIndex != null)?.dataIndex;
                if (index == null || !data[index]) return "";
                const candle = data[index];
                const point = points.get(candle.date);
                const basis = point?.basis_id != null ? bases.get(point.basis_id) : null;
                const value = point?.values[key];
                const reason = point?.reason || (key.startsWith("ev_") ? point?.ev_reason : null) || basis?.reasons[key] || "No matching historical inputs.";
                return `<strong>${escapeHtml(point?.price_date || candle.date)}</strong>`
                    + `<div>Adjusted close: ${candle.close?.toLocaleString(undefined, { maximumFractionDigits: 2 }) ?? "—"}</div>`
                    + `<div style="margin-top:6px"><strong>${label}: ${multiple(value)}</strong></div>`
                    + (value == null ? `<div style="max-width:260px;white-space:normal">${escapeHtml(reason)}</div>` : "")
                    + (basis ? `<div style="margin-top:6px">Statement: ${escapeHtml(basis.period_end)}<br/>In use from: ${escapeHtml(basis.available_from)}<br/>${escapeHtml(basis.source)} · reconstructed estimate</div>` : "");
            },
        },
        series: [
            { id: "price", name: "Price", type: "candlestick", xAxisIndex: 0, yAxisIndex: 0,
                data: data.map((point) => [point.open, point.close, point.low, point.high].map((v) => v != null && v > 0 ? v : "-")),
                itemStyle: { color: colors.positive, color0: colors.negative, borderColor: colors.positive, borderColor0: colors.negative } },
            ...(["MA20", "MA50"] as const).map((name, index) => ({
                id: name, name, type: "line", xAxisIndex: 0, yAxisIndex: 0,
                data: data.map((point) => point[name] ?? null), showSymbol: false, connectNulls: false,
                lineStyle: { width: 1, color: colors.series[index] }, itemStyle: { color: colors.series[index] },
            })),
            { id: "volume", name: "Volume", type: "bar", xAxisIndex: 1, yAxisIndex: 1,
                data: data.map((point) => ({ value: point.volume, itemStyle: { color: (point.close ?? 0) >= (point.open ?? 0) ? colors.positive : colors.negative, opacity: 0.45 } })) },
            { id: "multiple", name: label, type: "line", xAxisIndex: 2, yAxisIndex: 2,
                data: values, showSymbol: false, connectNulls: false, smooth: false,
                lineStyle: { width: 1.8, color: colors.brand }, itemStyle: { color: colors.brand },
                markLine: { silent: true, symbol: "none", label: { show: false }, lineStyle: { color: colors.textMuted, type: "dashed", opacity: 0.7 }, data: metadata?.median != null ? [{ yAxis: metadata.median }] : [] } },
        ],
    };
}

export default function StockValuationChart({ data, history, interval, onIntervalChange, isLoading }: Props) {
    const { resolvedTheme } = useTheme();
    const [metric, setMetric] = useState<MultipleKey>("pe");
    const [zoom, setZoom] = useState({ start: 0, end: 100 });
    const metadata = history?.metrics.find((item) => item.key === metric);
    const selectedLabel = MULTIPLES.find((item) => item.key === metric)!.label;
    const option = useMemo(() => valuationChartOption(data, history, metric, resolvedTheme === "dark", zoom), [data, history, metric, resolvedTheme, zoom]);
    const chartEvents = useMemo(() => ({ datazoom: (event: { start?: number; end?: number; batch?: Array<{ start?: number; end?: number }> }) => {
        const next = event.batch?.[0] ?? event;
        if (next.start != null && next.end != null) {
            const start = next.start, end = next.end;
            setZoom((previous) => previous.start === start && previous.end === end ? previous : { start, end });
        }
    } }), []);

    return <section className="surface-panel overflow-hidden" aria-labelledby="valuation-history-title" aria-busy={isLoading}>
        <header className="section-header flex-wrap gap-4">
            <div><p className="eyebrow">Market history</p><h2 id="valuation-history-title" className="section-title">Price & valuation history</h2><p className="section-description">Explore price and valuation on one timeline.</p></div>
            <div className="flex flex-wrap items-center gap-3">
                <label className="flex items-center gap-2 text-xs font-semibold">Multiple
                    <select aria-label="Historical valuation multiple" value={metric} onChange={(event) => setMetric(event.target.value as MultipleKey)} className="control-field min-h-9 w-32 text-xs">
                        {MULTIPLES.map((item) => <option key={item.key} value={item.key}>{item.label}</option>)}
                    </select>
                </label>
                <SegmentedControl label="History interval" value={interval} options={[{ value: "1d", label: "D", disabled: isLoading }, { value: "1wk", label: "W", disabled: isLoading }, { value: "1mo", label: "M", disabled: isLoading }]} onChange={onIntervalChange} />
            </div>
        </header>
        <div className="grid grid-cols-3 gap-3 border-b px-4 py-3 text-xs sm:px-5" aria-label="Historical valuation summary">
            <div><p className="text-[var(--text-muted)]">Latest {selectedLabel}</p><p className="mt-1 font-mono text-lg font-bold" aria-label={`Latest ${selectedLabel}`}>{multiple(metadata?.latest_value)}</p><p className="text-[10px] text-[var(--text-muted)]">{metadata?.latest_date || "No observations"}</p></div>
            <div><p className="text-[var(--text-muted)]">Full-history median</p><p className="mt-1 font-mono text-lg font-bold">{multiple(metadata?.median)}</p><p className="text-[10px] text-[var(--text-muted)]">Dashed line · selected frequency</p></div>
            <div><p className="text-[var(--text-muted)]">Coverage</p><p className="mt-1 font-mono text-lg font-bold">{metadata?.total_points ? `${Math.round(metadata.valid_points / metadata.total_points * 100)}%` : "—"}</p><p className="text-[10px] text-[var(--text-muted)]">{metadata ? `${metadata.valid_points.toLocaleString()} / ${metadata.total_points.toLocaleString()} observations` : "No observations"}</p></div>
        </div>
        {(!metadata || metadata.latest_reason) && <p role="status" className="flex gap-2 border-b bg-[var(--surface-subtle)] px-4 py-3 text-xs text-[var(--text-muted)]"><Info size={15} className="shrink-0" />{metadata?.latest_reason || "Historical multiples are unavailable. Refresh stock data to load eligible quarterly statements."}</p>}
        <div className={`relative h-[600px] w-full sm:h-[660px] ${isLoading ? "opacity-50" : ""}`} role="img" aria-label={`Linked stock price, volume and ${selectedLabel} chart`}>
            <ReactECharts option={option} onEvents={chartEvents} style={{ width: "100%", height: "100%" }} />
        </div>
        <div className="border-t px-4 py-3 text-xs text-[var(--text-muted)] sm:px-5">
            <p>{metadata?.formula || "Historical valuation multiples"}</p>
            <p className="mt-1">{metadata?.description || "Reconstructed estimates"} · {history?.currency || "Currency unavailable"} · gaps mean unavailable or not meaningful.</p>
            <details className="mt-3">
                <summary className="cursor-pointer font-semibold text-[var(--text)]">Calculation & data coverage</summary>
                <p className="mt-3">The price chart includes dividend adjustments. Multiples use prices adjusted only for splits; the ratio is unchanged by a split when price and shares use the same basis.</p>
                {history?.methodology.map((note) => <p key={note} className="mt-2 leading-5">{note}</p>)}
                {history?.warnings.map((note) => <p key={note} className="mt-2 font-medium text-amber-700 dark:text-amber-300">{note}</p>)}
                <p className="mt-2">Forward P/E requires archived analyst expectations and is not inferred from today’s forecasts.</p>
            </details>
        </div>
    </section>;
}
