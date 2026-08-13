"use client";

import { useEffect, useMemo, useState } from "react";
import ReactECharts from "echarts-for-react";
import { useTheme } from "next-themes";
import { AlertTriangle, BarChart3, LoaderCircle } from "lucide-react";

import {
    fetchPeerMultiples,
    PeerMultipleKey,
    PeerMultipleScope,
    PeerMultiplesResponse,
} from "@/lib/api";

const MULTIPLES: Array<{ key: PeerMultipleKey; label: string }> = [
    { key: "pe_ratio", label: "P/E" },
    { key: "forward_pe", label: "Forward P/E" },
    { key: "ps_ratio", label: "P/S" },
    { key: "pb_ratio", label: "P/B" },
    { key: "price_fcf", label: "P/FCF" },
    { key: "ev_sales", label: "EV/Sales" },
    { key: "ev_ebitda", label: "EV/EBITDA" },
];

const SCOPE_OPTIONS: Array<{ key: PeerMultipleScope; label: string }> = [
    { key: "auto", label: "Auto" },
    { key: "industry", label: "Industry" },
    { key: "sector", label: "Sector" },
];

const multiple = (value: number | null | undefined) => value == null || !Number.isFinite(value)
    ? "—"
    : `${value.toFixed(value >= 100 ? 0 : 1)}×`;

const percent = (value: number | null | undefined) => value == null || !Number.isFinite(value)
    ? "—"
    : `${value >= 0 ? "+" : "−"}${Math.abs(value * 100).toFixed(1)}%`;

const compact = (value: number | null | undefined) => value == null || !Number.isFinite(value)
    ? "—"
    : new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(value);

const escapeHtml = (value: string) => value.replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "\"": "&quot;",
    "'": "&#39;",
}[character] ?? character));

const unavailableCopy: Record<NonNullable<PeerMultiplesResponse["reason"]>, string> = {
    target_not_in_snapshot: "This stock is not in the latest published peer snapshot. It can be compared after a subsequent daily snapshot includes it.",
    target_metric_unavailable: "This multiple is unavailable because its underlying denominator is not positive or the provider did not publish it.",
    insufficient_industry_coverage: "This industry does not have the 10 valid observations required for a reliable comparison.",
    insufficient_sector_coverage: "This sector does not have the 20 valid observations required for a reliable comparison.",
};

type ChartMember = PeerMultiplesResponse["peers"][number] & { isTarget: boolean };

interface TooltipParam {
    dataIndex: number;
}

interface BarColorParam {
    dataIndex: number;
}

function DistributionBand({ data }: { data: PeerMultiplesResponse }) {
    const distribution = data.distribution;
    const percentile = data.target.raw_percentile;
    if (!distribution || percentile == null) return null;
    const markerPosition = Math.min(100, Math.max(0, percentile));
    const markers = [
        ["P10", distribution.p10],
        ["P25", distribution.p25],
        ["Median", distribution.median],
        ["P75", distribution.p75],
        ["P90", distribution.p90],
    ] as const;

    return (
        <div className="rounded-xl border p-4 sm:p-5" aria-label="Peer multiple percentile distribution">
            <div className="flex items-center justify-between gap-3">
                <div>
                    <p className="text-xs font-black uppercase tracking-wide text-slate-500">Full peer distribution</p>
                    <p className="mt-1 text-xs text-slate-500">Position is based on raw multiple values, not investment attractiveness.</p>
                </div>
                <span className="rounded-full border border-amber-300 bg-amber-50 px-2.5 py-1 font-mono text-xs font-black text-amber-800 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-300">
                    P{Math.round(percentile)}
                </span>
            </div>
            <div className="relative mt-7 h-3 rounded-full bg-gradient-to-r from-emerald-400 via-amber-300 to-rose-400">
                <div
                    className="absolute top-1/2 h-8 w-1 -translate-x-1/2 -translate-y-1/2 rounded-full bg-slate-950 shadow-[0_0_0_3px_rgba(255,255,255,0.9)] dark:bg-white dark:shadow-[0_0_0_3px_rgba(15,23,42,0.9)]"
                    style={{ left: `${markerPosition}%` }}
                    title={`${data.target.ticker} is at the ${percentile.toFixed(1)}th percentile`}
                />
            </div>
            <div className="mt-5 grid grid-cols-5 gap-1 text-center">
                {markers.map(([label, value]) => (
                    <div key={label} className="min-w-0">
                        <p className="text-[9px] font-bold uppercase text-slate-400 sm:text-[10px]">{label}</p>
                        <p className="mt-1 truncate font-mono text-[10px] font-black sm:text-xs">{multiple(value)}</p>
                    </div>
                ))}
            </div>
        </div>
    );
}

function PeerBars({ data }: { data: PeerMultiplesResponse }) {
    const { resolvedTheme } = useTheme();
    const dark = resolvedTheme === "dark";
    const members = useMemo<ChartMember[]>(() => [
        {
            ticker: data.target.ticker,
            name: data.target.name,
            value: data.target.value ?? 0,
            market_cap: data.target.market_cap,
            sales_growth_ttm: data.target.sales_growth_ttm,
            isTarget: true,
        },
        ...data.peers.map((peer) => ({ ...peer, isTarget: false })),
    ].sort((left, right) => right.value - left.value || left.ticker.localeCompare(right.ticker)), [data]);

    const option = useMemo(() => {
        const text = dark ? "#cbd5e1" : "#475569";
        const grid = dark ? "rgba(148,163,184,0.16)" : "rgba(100,116,139,0.16)";
        return {
            animationDuration: 350,
            aria: { enabled: true, description: `${data.metric.label} comparison for ${data.target.ticker} and representative peers` },
            grid: { left: 82, right: 112, top: 20, bottom: 42 },
            tooltip: {
                trigger: "item",
                backgroundColor: dark ? "rgba(15,23,42,0.98)" : "rgba(255,255,255,0.98)",
                borderColor: dark ? "#334155" : "#e2e8f0",
                textStyle: { color: dark ? "#f8fafc" : "#0f172a" },
                formatter: (params: TooltipParam) => {
                    const member = members[params.dataIndex];
                    if (!member) return "";
                    const company = escapeHtml(member.name || member.ticker);
                    const metricLabel = escapeHtml(data.metric.label);
                    const snapshotDate = escapeHtml(data.as_of_date || "—");
                    return [
                        `<strong>${company}${member.isTarget ? " · selected" : ""}</strong>`,
                        `${metricLabel}: ${multiple(member.value)}`,
                        `Market cap: ${compact(member.market_cap)}`,
                        `Sales growth TTM: ${percent(member.sales_growth_ttm)}`,
                        `Snapshot: ${snapshotDate}`,
                    ].join("<br/>");
                },
            },
            xAxis: {
                type: "value",
                min: 0,
                axisLabel: { color: text, formatter: (value: number) => `${value.toFixed(0)}×` },
                splitLine: { lineStyle: { color: grid, type: "dashed" } },
            },
            yAxis: {
                type: "category",
                inverse: true,
                data: members.map((member) => member.ticker.replace(".US", "")),
                axisTick: { show: false },
                axisLine: { show: false },
                axisLabel: { color: text, fontWeight: 700 },
            },
            series: [{
                type: "bar",
                data: members.map((member) => member.value),
                barMaxWidth: 24,
                itemStyle: {
                    borderRadius: [0, 5, 5, 0],
                    color: (params: BarColorParam) => members[params.dataIndex]?.isTarget ? "#f59e0b" : "#10b981",
                    opacity: 0.9,
                },
                label: {
                    show: true,
                    position: "right",
                    color: text,
                    fontSize: 10,
                    formatter: (params: TooltipParam) => {
                        const member = members[params.dataIndex];
                        return member ? `${multiple(member.value)}  Growth ${percent(member.sales_growth_ttm)}` : "";
                    },
                },
            }],
        };
    }, [dark, data, members]);

    return (
        <div className="overflow-x-auto rounded-xl border" aria-label="Representative peer multiples">
            <div className="min-w-[640px] p-2">
                <ReactECharts option={option} style={{ height: Math.max(360, members.length * 42 + 76), width: "100%" }} />
            </div>
        </div>
    );
}

export default function PeerMultipleDistribution({ ticker }: { ticker: string }) {
    const [metric, setMetric] = useState<PeerMultipleKey>("ps_ratio");
    const [scope, setScope] = useState<PeerMultipleScope>("auto");
    const [data, setData] = useState<PeerMultiplesResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");

    useEffect(() => {
        const controller = new AbortController();
        void fetchPeerMultiples(ticker, metric, scope, controller.signal)
            .then((response) => setData(response))
            .catch((requestError: unknown) => {
                if (controller.signal.aborted) return;
                setData(null);
                setError(requestError instanceof Error ? requestError.message : "Peer multiples could not be loaded.");
            })
            .finally(() => {
                if (!controller.signal.aborted) setLoading(false);
            });
        return () => controller.abort();
    }, [ticker, metric, scope]);

    const percentileCopy = data?.target.raw_percentile == null
        ? "—"
        : `Raw percentile rank: ${data.target.raw_percentile.toFixed(1)} / 100 among valid peers`;

    return (
        <section className="space-y-4" aria-labelledby="peer-multiple-title">
            <div className="flex flex-col justify-between gap-3 lg:flex-row lg:items-end">
                <div>
                    <p className="eyebrow">Relative valuation</p>
                    <h3 id="peer-multiple-title" className="mt-1 text-base font-black">Valuation multiples vs peers</h3>
                    <p className="mt-1 text-xs text-slate-500">Same-date published values · peer median is the primary benchmark.</p>
                </div>
                <div className="flex flex-wrap gap-2">
                    <label className="text-[10px] font-black uppercase tracking-wide text-slate-500">
                        Multiple
                        <select
                            aria-label="Peer multiple"
                            value={metric}
                            onChange={(event) => {
                                setLoading(true);
                                setError("");
                                setData(null);
                                setMetric(event.target.value as PeerMultipleKey);
                            }}
                            className="control-field mt-1 min-w-36 py-2 text-xs normal-case tracking-normal"
                        >
                            {MULTIPLES.map((item) => <option key={item.key} value={item.key}>{item.label}</option>)}
                        </select>
                    </label>
                    <div>
                        <p className="text-[10px] font-black uppercase tracking-wide text-slate-500">Peer scope</p>
                        <div className="mt-1 flex rounded-lg border bg-slate-100 p-1 dark:bg-slate-900">
                            {SCOPE_OPTIONS.map((item) => (
                                <button
                                    key={item.key}
                                    type="button"
                                    onClick={() => {
                                        if (scope === item.key) return;
                                        setLoading(true);
                                        setError("");
                                        setData(null);
                                        setScope(item.key);
                                    }}
                                    className={`rounded-md px-2.5 py-1.5 text-xs font-bold ${scope === item.key ? "bg-white text-emerald-700 shadow-sm dark:bg-slate-700 dark:text-emerald-300" : "text-slate-500"}`}
                                    aria-pressed={scope === item.key}
                                >
                                    {item.label}
                                </button>
                            ))}
                        </div>
                    </div>
                </div>
            </div>

            {loading && (
                <div className="grid min-h-56 place-items-center rounded-xl border bg-slate-50/60 dark:bg-slate-900/30" role="status">
                    <div className="text-center text-sm text-slate-500"><LoaderCircle className="mx-auto mb-2 animate-spin" size={22} />Loading same-date peer distribution…</div>
                </div>
            )}
            {!loading && error && <div className="error-panel" role="alert">{error}</div>}
            {!loading && !error && data && !data.available && (
                <div className="rounded-xl border border-amber-200 bg-amber-50 p-5 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/20 dark:text-amber-300" role="status">
                    <AlertTriangle className="mr-2 inline" size={17} />
                    {data.reason ? unavailableCopy[data.reason] : "Peer comparison is unavailable."}
                    <p className="mt-2 text-xs opacity-80">Snapshot {data.as_of_date || "not published"}{data.cohort ? ` · ${data.cohort.valid_count}/${data.cohort.minimum_observations} required valid peers` : ""}</p>
                </div>
            )}
            {!loading && !error && data?.available && data.distribution && data.cohort && (
                <>
                    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                        {[
                            [data.target.ticker.replace(".US", ""), multiple(data.target.value), percentileCopy],
                            ["Peer median", multiple(data.distribution.median), `${data.cohort.name || data.cohort.scope} median`],
                            ["Raw percentile", `P${Math.round(data.target.raw_percentile ?? 0)}`, "Higher multiple means more expensive"],
                            ["Premium / discount", percent(data.target.premium_to_median), `vs median · mean ${multiple(data.distribution.mean)}`],
                        ].map(([label, value, detail]) => (
                            <article key={label} className="surface-subtle rounded-xl border p-4">
                                <p className="text-[10px] font-black uppercase tracking-wide text-slate-500">{label}</p>
                                <p className="mt-2 font-mono text-xl font-black">{value}</p>
                                <p className="mt-1 text-[10px] text-slate-500">{detail}</p>
                            </article>
                        ))}
                    </div>
                    <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
                        <span className="inline-flex items-center gap-1 font-bold text-slate-700 dark:text-slate-200"><BarChart3 size={14} />{data.cohort.scope === "industry" ? "Industry" : "Sector"}: {data.cohort.name || "Unknown"}</span>
                        <span>· {data.cohort.valid_count} valid peers</span>
                        <span>· {data.cohort.excluded_count} excluded</span>
                        <span>· Snapshot {data.as_of_date}</span>
                    </div>
                    <DistributionBand data={data} />
                    <PeerBars data={data} />
                </>
            )}
        </section>
    );
}
