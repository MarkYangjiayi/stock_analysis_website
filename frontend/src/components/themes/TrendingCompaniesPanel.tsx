"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Flame, Megaphone, RotateCcw } from "lucide-react";

import { ApiError, fetchTrendingCompanies, type TrendingCompaniesResponse } from "@/lib/api";
import { formatCompactNumber, formatMoney, formatSignedPercent } from "@/lib/format";
import { displayTicker, formatMultiple, formatShare, formatSigned, tickerHref, toneClass } from "./themeFormat";

const HEAD = "border-b px-3 py-2 font-medium";
const NUM = "px-3 py-2 text-right font-mono tabular-nums";

const FLAGS: Record<string, { label: string; className: string; icon: typeof Flame; title: string }> = {
    attention_only: {
        label: "Attention only",
        className: "border-caution/30 text-caution",
        icon: Megaphone,
        title: "News attention is unusually high while the 5-day excess return is negative",
    },
    ignited: {
        label: "Ignited",
        className: "border-accent/30 text-accent-strong",
        icon: Flame,
        title: "Gained at least 5% today on at least twice its normal volume",
    },
};

/** Cross-sectional company trend ranking for the same theme publication. */
export default function TrendingCompaniesPanel({
    adminKey,
    onUnauthorized,
    themeNames,
}: {
    adminKey: string;
    onUnauthorized: () => void;
    themeNames: Record<string, string>;
}) {
    const [data, setData] = useState<TrendingCompaniesResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [retryKey, setRetryKey] = useState(0);

    const load = useCallback(async (signal?: AbortSignal) => {
        setLoading(true);
        setError("");
        try {
            setData(await fetchTrendingCompanies({ limit: 50 }, adminKey, signal));
        } catch (caught) {
            if (caught instanceof DOMException && caught.name === "AbortError") return;
            if (caught instanceof ApiError && caught.status === 401) {
                onUnauthorized();
                return;
            }
            setData(null);
            setError(caught instanceof Error ? caught.message : "Unable to load trending companies.");
        } finally {
            if (!signal?.aborted) setLoading(false);
        }
    }, [adminKey, onUnauthorized]);

    useEffect(() => {
        const controller = new AbortController();
        void load(controller.signal);
        return () => controller.abort();
    }, [load, retryKey]);

    const companies = data?.companies ?? [];

    return (
        <div>
            {error && (
                <div className="error-panel m-4 flex items-center justify-between gap-4" role="alert">
                    <span>{error}</span>
                    <button type="button" className="secondary-button shrink-0" onClick={() => setRetryKey((value) => value + 1)}>
                        <RotateCcw size={15} /> Retry
                    </button>
                </div>
            )}
            {data && data.meta.stale && (
                <p className="border-b px-4 py-2 text-xs text-caution" role="status">Ranking from {data.meta.as_of_date}; {data.meta.expected_as_of_date} was expected.</p>
            )}
            <div className="overflow-x-auto">
                <table className="min-w-full whitespace-nowrap text-left text-sm" aria-label="Trending companies">
                    <thead className="bg-surface-muted font-mono text-xs font-medium uppercase tracking-[0.08em] text-fg-muted">
                        <tr>
                            <th className={`${HEAD} text-right`}>Rank</th>
                            <th className={HEAD}>Ticker</th>
                            <th className={HEAD}>Company</th>
                            <th className={HEAD}>Industry</th>
                            <th className={`${HEAD} text-right`} title="Company trend score (sector-relative return, volume, attention and proximity to highs)">Score</th>
                            <th className={`${HEAD} text-right`}>Close</th>
                            <th className={`${HEAD} text-right`}>Mkt cap</th>
                            <th className={`${HEAD} text-right`}>1D</th>
                            <th className={`${HEAD} text-right`} title="Excess return vs SPY">5D vs SPY</th>
                            <th className={`${HEAD} text-right`} title="5D excess return minus the sector median">vs sector</th>
                            <th className={`${HEAD} text-right`} title="5-day volume vs the 60-day median">RVOL 5D</th>
                            <th className={`${HEAD} text-right`} title="Abnormal news attention (robust z)">News z</th>
                            <th className={`${HEAD} text-right`} title="Close as a share of the 52-week high">Near high</th>
                            <th className={HEAD}>Themes</th>
                            <th className={HEAD}>Flags</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-line">
                        {loading && !companies.length ? (
                            Array.from({ length: 6 }).map((_, index) => (
                                <tr key={index}>{Array.from({ length: 15 }).map((__, cell) => <td key={cell} className="px-3 py-2.5"><div className="h-4 w-14 animate-pulse rounded bg-surface-muted" /></td>)}</tr>
                            ))
                        ) : companies.length === 0 ? (
                            <tr><td colSpan={15} className="px-6 py-16 text-center text-sm text-fg-muted">{error ? "Trending companies are unavailable." : "No company passed the liquidity screen for this session."}</td></tr>
                        ) : companies.map((company) => (
                            <tr key={company.ticker} className="transition-colors hover:bg-surface-muted">
                                <td className={`${NUM} text-fg`}>{company.rank}</td>
                                <td className="px-3 py-2 font-mono font-semibold text-accent-strong">
                                    <Link href={tickerHref(company.ticker)} className="hover:underline">{displayTicker(company.ticker)}</Link>
                                </td>
                                <td className="max-w-56 truncate px-3 py-2 font-medium text-fg" title={company.name ?? undefined}>{company.name ?? "—"}</td>
                                <td className="max-w-48 truncate px-3 py-2 text-fg-muted" title={company.sector ?? undefined}>{company.industry ?? company.sector ?? "—"}</td>
                                <td className={`${NUM} text-fg`}>{formatSigned(company.score)}</td>
                                <td className={`${NUM} text-fg`}>{formatMoney(company.close)}</td>
                                <td className={`${NUM} text-fg`}>{formatCompactNumber(company.market_cap, 1)}</td>
                                <td className={`${NUM} ${toneClass(company.return_1d)}`}>{formatSignedPercent(company.return_1d, 1)}</td>
                                <td className={`${NUM} ${toneClass(company.excess_5d)}`}>{formatSignedPercent(company.excess_5d, 1)}</td>
                                <td className={`${NUM} ${toneClass(company.excess_5d_vs_sector)}`}>{formatSignedPercent(company.excess_5d_vs_sector, 1)}</td>
                                <td className={`${NUM} text-fg`}>{formatMultiple(company.relative_volume_5d)}</td>
                                <td className={`${NUM} text-fg`}>{formatSigned(company.attention_z, 1)}</td>
                                <td className={`${NUM} text-fg`}>{formatShare(company.near_high_ratio)}</td>
                                <td className="px-3 py-2">
                                    {company.themes.length ? (
                                        <span className="flex gap-1">
                                            {company.themes.map((slug) => (
                                                <Link key={slug} href={`/themes/${encodeURIComponent(slug)}`} className="rounded border bg-surface px-1.5 py-0.5 text-xs text-fg-muted hover:border-accent hover:text-fg">
                                                    {themeNames[slug] ?? slug}
                                                </Link>
                                            ))}
                                        </span>
                                    ) : <span className="text-fg-muted">—</span>}
                                </td>
                                <td className="px-3 py-2">
                                    <span className="flex gap-1">
                                        {company.flags.map((flag) => {
                                            const meta = FLAGS[flag];
                                            const Icon = meta?.icon;
                                            return (
                                                <span key={flag} title={meta?.title} className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-xs ${meta?.className ?? "text-fg-muted"}`}>
                                                    {Icon && <Icon size={11} aria-hidden="true" />}{meta?.label ?? flag}
                                                </span>
                                            );
                                        })}
                                    </span>
                                </td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
}
