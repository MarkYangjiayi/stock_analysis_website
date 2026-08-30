"use client";

import { useMemo } from "react";
import ReactECharts from "echarts-for-react";
import { useTheme } from "next-themes";
import { AlertTriangle, ExternalLink, LoaderCircle, ShieldCheck } from "lucide-react";

import type { FinancialFlowResponse } from "@/lib/api";

interface FinancialFlowPanelProps {
    data: FinancialFlowResponse | null;
    loading: boolean;
    error: string;
    timePeriod: "annual" | "ttm" | "quarterly";
    onTimePeriodChange: (period: "annual" | "ttm" | "quarterly") => void;
    onPeriodEndChange: (periodEnd: string) => void;
    onRetry: () => void;
}

const compact = (value: number | null | undefined, currency = "USD") => {
    if (value == null || !Number.isFinite(value)) return "—";
    return new Intl.NumberFormat("en-US", {
        style: "currency",
        currency,
        notation: "compact",
        maximumFractionDigits: 1,
    }).format(value);
};

const percent = (value: number | null | undefined) => value == null || !Number.isFinite(value)
    ? "—"
    : `${value >= 0 ? "+" : "−"}${Math.abs(value * 100).toFixed(1)}%`;

const escapeHtml = (value: string) => value.replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "\"": "&quot;",
    "'": "&#39;",
}[character] ?? character));

const buildSankeyLayout = (
    nodes: FinancialFlowResponse["nodes"],
    links: FinancialFlowResponse["links"],
) => {
    const revenueSegmentIds = new Set(
        links
            .filter((link) => link.target === "revenue" && link.source !== "revenue")
            .map((link) => link.source),
    );
    const revenueDepth = revenueSegmentIds.size ? 1 : 0;
    const depthById = new Map<string, number>();
    const setDepth = (nodeId: string, depth: number) => {
        if (nodes.some((node) => node.id === nodeId)) depthById.set(nodeId, depth);
    };

    revenueSegmentIds.forEach((nodeId) => depthById.set(nodeId, 0));
    setDepth("revenue", revenueDepth);
    setDepth("gross_profit", revenueDepth + 1);
    setDepth("operating_income", revenueDepth + 2);
    setDepth("pretax_income", revenueDepth + 3);
    setDepth("net_income", revenueDepth + 4);
    setDepth("income_tax", revenueDepth + 4);

    for (let pass = 0; pass < nodes.length; pass += 1) {
        let changed = false;
        links.forEach((link) => {
            const sourceDepth = depthById.get(link.source);
            const targetDepth = depthById.get(link.target);
            if (sourceDepth != null && targetDepth == null) {
                depthById.set(link.target, sourceDepth + 1);
                changed = true;
            } else if (sourceDepth == null && targetDepth != null) {
                depthById.set(link.source, Math.max(0, targetDepth - 1));
                changed = true;
            }
        });
        if (!changed) break;
    }

    nodes.forEach((node) => {
        if (!depthById.has(node.id)) depthById.set(node.id, 0);
    });

    const lanePriority = (node: FinancialFlowResponse["nodes"][number]) => {
        if (["gross_profit", "operating_income", "pretax_income", "net_income", "revenue"].includes(node.id)) return 0;
        if (node.id === "non_operating_income" || revenueSegmentIds.has(node.id)) return 1;
        return node.kind === "expense" ? 3 : 2;
    };
    const orderedNodes = [...nodes].sort((left, right) => {
        const depthDifference = (depthById.get(left.id) ?? 0) - (depthById.get(right.id) ?? 0);
        return depthDifference || lanePriority(left) - lanePriority(right);
    });
    const nodesPerDepth = new Map<number, number>();
    depthById.forEach((depth) => nodesPerDepth.set(depth, (nodesPerDepth.get(depth) ?? 0) + 1));
    const maxNodesInColumn = Math.max(1, ...nodesPerDepth.values());

    return {
        depthById,
        orderedNodes,
        revenueSegmentIds,
        chartHeight: Math.min(680, Math.max(440, 150 + maxNodesInColumn * 62)),
    };
};

export default function FinancialFlowPanel({
    data,
    loading,
    error,
    timePeriod,
    onTimePeriodChange,
    onPeriodEndChange,
    onRetry,
}: FinancialFlowPanelProps) {
    const { resolvedTheme } = useTheme();
    const isDark = resolvedTheme === "dark";
    const nodeById = useMemo(
        () => new Map((data?.nodes ?? []).map((node) => [node.id, node])),
        [data?.nodes],
    );
    const sankeyLayout = useMemo(
        () => buildSankeyLayout(data?.nodes ?? [], data?.links ?? []),
        [data?.links, data?.nodes],
    );
    const option = useMemo(() => {
        if (!data?.chart_available || !data.nodes.length || !data.links.length) return {};
        const textColor = isDark ? "#d1d5db" : "#334155";
        const borderColor = isDark ? "#475569" : "#e2e8f0";
        const colors: Record<string, string> = {
            income: isDark ? "#60a5fa" : "#3b82f6",
            profit: isDark ? "#3b82f6" : "#1d4ed8",
            expense: isDark ? "#fb923c" : "#f97316",
        };
        return {
            aria: {
                enabled: true,
                description: `Reported profit flow for ${data.ticker}, ${data.period_end ?? "latest period"}`,
            },
            animationDuration: 350,
            tooltip: {
                trigger: "item",
                backgroundColor: isDark ? "rgba(15,23,42,.96)" : "rgba(255,255,255,.98)",
                borderColor,
                textStyle: { color: textColor },
                formatter: (params: { dataType?: string; data?: { name?: string; source?: string; target?: string; value?: number } }) => {
                    if (params.dataType === "edge") {
                        const source = nodeById.get(params.data?.source ?? "")?.label ?? params.data?.source ?? "";
                        const target = nodeById.get(params.data?.target ?? "")?.label ?? params.data?.target ?? "";
                        return `<strong>${escapeHtml(source)} → ${escapeHtml(target)}</strong><br/>${compact(params.data?.value, data.currency ?? "USD")}`;
                    }
                    const node = nodeById.get(params.data?.name ?? "");
                    if (!node) return "";
                    return `<strong>${escapeHtml(node.label)}</strong><br/>${compact(node.value, data.currency ?? "USD")}<br/><span style="opacity:.7">${escapeHtml(node.evidence_type.replaceAll("_", " "))}</span>`;
                },
            },
            series: [{
                type: "sankey",
                left: sankeyLayout.revenueSegmentIds.size ? 210 : 36,
                right: 180,
                top: 36,
                bottom: 36,
                nodeAlign: "justify",
                nodeWidth: 13,
                nodeGap: sankeyLayout.revenueSegmentIds.size ? 16 : 24,
                layoutIterations: 0,
                draggable: false,
                emphasis: { focus: "adjacency" },
                data: sankeyLayout.orderedNodes.map((node) => ({
                    name: node.id,
                    value: node.value,
                    depth: sankeyLayout.depthById.get(node.id),
                    itemStyle: {
                        color: colors[node.kind] ?? "#64748b",
                        borderColor,
                        borderWidth: 1,
                        borderRadius: 2,
                    },
                    label: {
                        formatter: node.label,
                        position: sankeyLayout.revenueSegmentIds.has(node.id) ? "left" : "right",
                        align: sankeyLayout.revenueSegmentIds.has(node.id) ? "right" : "left",
                        distance: 8,
                        color: textColor,
                        fontSize: 12,
                        fontWeight: 600,
                    },
                })),
                links: data.links.map((link) => ({
                    ...link,
                    lineStyle: {
                        color: colors[link.kind] ?? "#64748b",
                        curveness: 0.42,
                        opacity: link.kind === "expense" ? 0.3 : 0.24,
                    },
                })),
                lineStyle: { curveness: 0.42, opacity: 0.24 },
            }],
        };
    }, [data, isDark, nodeById, sankeyLayout]);

    if (timePeriod === "ttm") {
        return (
            <section className="surface-panel p-5 sm:p-7" aria-labelledby="financial-flow-title">
                <p className="eyebrow">Reported economics</p>
                <h2 id="financial-flow-title" className="mt-1 text-xl font-black">Profit flow</h2>
                <div className="surface-subtle mt-5 rounded-xl border p-5 text-sm text-slate-600 dark:text-slate-300">
                    Profit flow is available for reported annual and quarterly periods. TTM segment mixes are not combined unless every constituent quarter is independently verified.
                    <div className="mt-4 flex gap-2">
                        <button type="button" className="secondary-button" onClick={() => onTimePeriodChange("annual")}>Annual</button>
                        <button type="button" className="secondary-button" onClick={() => onTimePeriodChange("quarterly")}>Quarterly</button>
                    </div>
                </div>
            </section>
        );
    }

    return (
        <section className="surface-panel overflow-hidden" aria-labelledby="financial-flow-title">
            <header className="surface-subtle flex flex-col justify-between gap-4 border-b px-5 py-4 sm:flex-row sm:items-center">
                <div>
                    <p className="eyebrow">Reported economics</p>
                    <h2 id="financial-flow-title" className="mt-1 text-xl font-black">Profit flow</h2>
                    <p className="mt-1 text-xs text-slate-500">Reported values first · derived residuals are labeled · missing data stays missing</p>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                    {(["annual", "quarterly"] as const).map((period) => (
                        <button
                            key={period}
                            type="button"
                            className={timePeriod === period ? "primary-button min-h-9 px-3 py-1.5" : "secondary-button min-h-9 px-3 py-1.5"}
                            onClick={() => onTimePeriodChange(period)}
                        >
                            {period === "annual" ? "Annual" : "Quarterly"}
                        </button>
                    ))}
                    {!!data?.available_periods.length && (
                        <select
                            aria-label="Profit flow reporting period"
                            className="min-h-9 rounded-lg border bg-transparent px-3 text-sm font-semibold"
                            value={data.period_end ?? ""}
                            onChange={(event) => onPeriodEndChange(event.target.value)}
                        >
                            {data.available_periods.map((period) => <option key={period} value={period}>{period}</option>)}
                        </select>
                    )}
                    {data && (
                        <span className="status-pill">
                            {data.coverage_level === "full" ? "Official detail" : data.coverage_level === "consolidated" ? "Consolidated" : "Unavailable"}
                        </span>
                    )}
                </div>
            </header>

            <div className="p-5 sm:p-7">
                {loading && !data && <div className="flex min-h-48 items-center justify-center gap-2 text-sm text-slate-500"><LoaderCircle className="animate-spin" size={18} /> Loading profit flow…</div>}
                {error && !data && (
                    <div className="error-panel" role="alert"><AlertTriangle size={18} /><span>{error}</span><button type="button" className="secondary-button ml-auto" onClick={onRetry}>Retry</button></div>
                )}
                {data && (
                    <>
                        <div className="grid gap-3 md:grid-cols-3">
                            {data.summary_cards.map((card) => (
                                <article key={card.key} className="surface-subtle rounded-xl border p-4">
                                    <p className="text-sm font-semibold text-slate-500">{card.label}</p>
                                    <p className="mt-1 font-mono text-2xl font-black">{compact(card.value, data.currency ?? "USD")}</p>
                                    <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs font-semibold">
                                        {card.yoy_change != null && <span className={card.yoy_change >= 0 ? "text-emerald-600 dark:text-emerald-400" : "text-rose-500"}>{percent(card.yoy_change)} YoY</span>}
                                        {card.key !== "revenue" && card.margin != null && <span className="text-slate-500">{(card.margin * 100).toFixed(1)}% margin</span>}
                                    </div>
                                    {card.note && <p className="mt-2 text-xs leading-5 text-slate-500">{card.note}</p>}
                                </article>
                            ))}
                        </div>

                        {(data.enrichment.status === "queued" || data.enrichment.status === "running") && (
                            <div className="mt-4 flex flex-wrap items-center gap-2 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-xs font-semibold text-blue-700 dark:border-blue-900 dark:bg-blue-950/30 dark:text-blue-300">
                                {loading && <LoaderCircle className="animate-spin" size={14} />} Official SEC detail is being checked. The consolidated view remains usable.
                                {!loading && <button type="button" className="secondary-button ml-auto" onClick={onRetry}>Refresh status</button>}
                            </div>
                        )}
                        {data.enrichment.status === "pending_refresh" && (
                            <div className="mt-4 flex flex-wrap items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs font-semibold text-amber-800 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
                                Official SEC detail is still processing. The consolidated view remains usable.
                                <button type="button" className="secondary-button ml-auto" onClick={onRetry}>Refresh status</button>
                            </div>
                        )}
                        {loading && data && <div className="mt-4 flex items-center gap-2 text-xs font-semibold text-slate-500"><LoaderCircle className="animate-spin" size={14} /> Refreshing the selected report…</div>}
                        {error && data && (
                            <div className="error-panel mt-4" role="alert"><AlertTriangle size={18} /><span>{error}</span><button type="button" className="secondary-button ml-auto" onClick={onRetry}>Retry</button></div>
                        )}
                        {data.unsupported_reason && <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200">{data.unsupported_reason}</div>}
                        {data.insights.map((insight, index) => insight.message && (
                            <div key={`${insight.code ?? "insight"}-${index}`} className="mt-4 flex gap-2 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200"><AlertTriangle size={17} className="mt-0.5 shrink-0" />{insight.message}</div>
                        ))}

                        {data.chart_available && data.links.length ? (
                            <div className="mt-5 overflow-x-auto">
                                <div className="mx-auto min-w-[960px] max-w-[1480px]"><ReactECharts option={option} style={{ height: sankeyLayout.chartHeight }} opts={{ renderer: "canvas" }} /></div>
                            </div>
                        ) : !data.unsupported_reason && (
                            <div className="surface-subtle mt-5 rounded-xl border p-5 text-sm text-slate-600 dark:text-slate-300">
                                The selected period cannot be represented as a non-negative Sankey flow. Reported line items remain available below.
                            </div>
                        )}

                        {!!data.nodes.length && (
                            <div className="mt-5 overflow-x-auto">
                                <table className="w-full min-w-[620px] text-left text-sm">
                                    <thead className="border-b text-xs uppercase tracking-wide text-slate-500"><tr><th className="py-2">Line item</th><th className="py-2 text-right">Amount</th><th className="py-2 pl-5">Evidence</th></tr></thead>
                                    <tbody>{data.nodes.map((node) => <tr key={node.id} className="border-b border-slate-100 dark:border-slate-800"><td className="py-2.5 font-semibold">{node.label}</td><td className="py-2.5 text-right font-mono">{compact(node.value, data.currency ?? "USD")}</td><td className="py-2.5 pl-5 text-xs text-slate-500">{node.evidence_type.replaceAll("_", " ")}</td></tr>)}</tbody>
                                </table>
                            </div>
                        )}

                        <details className="mt-5 rounded-xl border p-4">
                            <summary className="cursor-pointer text-sm font-bold">Sources & validation</summary>
                            <div className="mt-3 space-y-2 text-xs text-slate-500">
                                {data.sources.map((source) => (
                                    <div key={source.source_id} className="flex flex-wrap items-center gap-2">
                                        <ShieldCheck size={14} className="text-emerald-500" />
                                        <span>{source.document_type}</span>
                                        {source.filing_date && <span>· {source.filing_date}</span>}
                                        {source.url && <a href={source.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-blue-600 hover:underline">Open filing <ExternalLink size={12} /></a>}
                                    </div>
                                ))}
                                {(data.validation.warnings ?? []).map((warning, index) => <p key={`${warning.code ?? "warning"}-${index}`}>{warning.message}</p>)}
                                {data.enrichment.last_error && <p>Official detail unavailable: {data.enrichment.last_error}</p>}
                            </div>
                        </details>
                    </>
                )}
            </div>
        </section>
    );
}
