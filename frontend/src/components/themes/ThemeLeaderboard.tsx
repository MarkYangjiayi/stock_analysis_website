import Link from "next/link";
import { Megaphone } from "lucide-react";

import { Sparkline } from "@/components/ui/Sparkline";
import type { ThemeListItem, ThemeView } from "@/lib/api";
import { formatSignedPercent } from "@/lib/format";
import ThemeStageChip from "./ThemeStageChip";
import {
    displayTicker,
    formatHeat,
    formatRankChange,
    formatShare,
    formatSigned,
    seriesChange,
    sparkValues,
    tickerHref,
    toneClass,
} from "./themeFormat";

const COMPONENTS = [
    ["score_price", "Price"],
    ["score_breadth", "Breadth"],
    ["score_volume", "Volume"],
    ["score_attention", "Attention"],
] as const;

// Components are blends of cross-sectional z-scores clipped at ±3.
const COMPONENT_SCALE = 3;

/** Four zero-centred bars for the P/B/V/A heat components; missing components draw no bar. */
export function ComponentBars({ theme }: { theme: Pick<ThemeListItem, (typeof COMPONENTS)[number][0]> }) {
    const summary = COMPONENTS.map(([key, label]) => `${label} ${formatSigned(theme[key])}`).join(", ");
    return (
        <span className="relative inline-flex h-6 items-stretch gap-1" title={summary}>
            <span className="sr-only">{summary}</span>
            {COMPONENTS.map(([key, label]) => {
                const value = theme[key];
                const size = value == null || !Number.isFinite(value) ? 0 : Math.min(Math.abs(value) / COMPONENT_SCALE, 1) * 50;
                const positive = value != null && value > 0;
                return (
                    <span key={key} className="relative w-1.5 rounded-sm bg-surface-muted" aria-hidden="true" data-component={label}>
                        <span className="absolute inset-x-0 top-1/2 h-px bg-line-strong" />
                        {size > 0 && (
                            <span
                                className={`absolute inset-x-0 rounded-sm ${positive ? "bg-up" : "bg-down"}`}
                                style={positive ? { bottom: "50%", height: `${size}%` } : { top: "50%", height: `${size}%` }}
                            />
                        )}
                    </span>
                );
            })}
        </span>
    );
}

export function HeatBar({ value, unranked = false }: { value: number | null; unranked?: boolean }) {
    const width = value == null || !Number.isFinite(value) ? 0 : Math.min(Math.max(value, 0), 100);
    return (
        <span className="inline-flex items-center gap-2" title={unranked ? "Not ranked: too few priced members" : undefined}>
            <span className="h-1.5 w-16 overflow-hidden rounded-full bg-surface-muted" aria-hidden="true">
                <span className={`block h-full rounded-full ${unranked ? "bg-flat/40" : "bg-accent"}`} style={{ width: `${width}%` }} />
            </span>
            <span className={`w-9 text-right font-mono tabular-nums ${unranked ? "text-fg-muted" : "text-fg"}`}>{formatHeat(value)}</span>
        </span>
    );
}

const HEAD = "border-b px-3 py-2 font-medium";
const NUM = "px-3 py-2 text-right font-mono tabular-nums";

export default function ThemeLeaderboard({
    themes,
    view,
    loading = false,
}: {
    themes: ThemeListItem[];
    view: ThemeView;
    loading?: boolean;
}) {
    const heatLabel = view === "now" ? "Heat 5D" : "Heat 20D";
    return (
        <div className={`relative overflow-x-auto transition-opacity ${loading ? "opacity-60" : ""}`} aria-busy={loading}>
            <table className="min-w-full whitespace-nowrap text-left text-sm" aria-label="Theme leaderboard">
                <thead className="bg-surface-muted font-mono text-xs font-medium uppercase tracking-[0.08em] text-fg-muted">
                    <tr>
                        <th className={`${HEAD} text-right`} title="Rank in this view; the arrow is the change in the Now (5D) rank since the prior session">Rank (Δ)</th>
                        <th className={HEAD}>Theme</th>
                        <th className={HEAD}>Stage</th>
                        <th className={HEAD}>{heatLabel}</th>
                        <th className={HEAD}>60D heat</th>
                        <th className={HEAD} title="Price · Breadth · Volume · Attention components">P · B · V · A</th>
                        <th className={`${HEAD} text-right`} title="Basket excess return vs SPY">5D vs SPY</th>
                        <th className={`${HEAD} text-right`} title="Basket excess return vs SPY">21D vs SPY</th>
                        <th className={`${HEAD} text-right`}>% &gt; MA50</th>
                        <th className={`${HEAD} text-right`} title="Distinct members with a +5% day on 2× volume in the last 5 sessions">Ign. 5D</th>
                        <th className={`${HEAD} text-right`} title="Consecutive sessions ranked in the top 5">Top-5 streak</th>
                        <th className={HEAD}>Leaders</th>
                        <th className={`${HEAD} text-right`} title="Primary proxy ETF excess return vs SPY">Proxy ETF 21D</th>
                    </tr>
                </thead>
                <tbody className="divide-y divide-line">
                    {loading && themes.length === 0 ? (
                        Array.from({ length: 6 }).map((_, index) => (
                            <tr key={index}>{Array.from({ length: 13 }).map((__, cell) => <td key={cell} className="px-3 py-2.5"><div className="h-4 w-16 animate-pulse rounded bg-surface-muted" /></td>)}</tr>
                        ))
                    ) : themes.length === 0 ? (
                        <tr><td colSpan={13} className="px-6 py-16 text-center text-sm text-fg-muted">No themes match this filter.</td></tr>
                    ) : themes.map((theme) => {
                        const rank = view === "now" ? theme.rank_now : theme.rank_trend;
                        const heat = view === "now" ? theme.heat_now : theme.heat_trend;
                        return (
                            <tr key={theme.slug} className="transition-colors hover:bg-surface-muted">
                                <td className={NUM}>
                                    <span className="text-fg">{rank ?? "—"}</span>
                                    <span className={`ml-2 inline-block w-8 text-xs ${toneClass(theme.rank_change)}`}>{formatRankChange(theme.rank_change)}</span>
                                </td>
                                <td className="px-3 py-2">
                                    <Link href={`/themes/${encodeURIComponent(theme.slug)}`} className="font-medium text-fg hover:text-accent-strong hover:underline">
                                        {theme.name_en}
                                    </Link>
                                    <span className="ml-2 text-xs text-fg-muted">{theme.name_zh}</span>
                                    {theme.attention_only && (
                                        <span className="ml-2 inline-flex items-center gap-1 rounded border border-caution/30 px-1.5 text-xs text-caution" title="News attention is high while price has not confirmed">
                                            <Megaphone size={11} aria-hidden="true" /> Attention only
                                        </span>
                                    )}
                                </td>
                                <td className="px-3 py-2"><ThemeStageChip stage={theme.stage} changed={theme.stage_changed} /></td>
                                <td className="px-3 py-2"><HeatBar value={heat} unranked={theme.stage === "insufficient"} /></td>
                                <td className="px-3 py-2">
                                    <Sparkline values={sparkValues(theme.heat_sparkline)} className={`w-24 ${toneClass(seriesChange(theme.heat_sparkline))}`} height={20} />
                                </td>
                                <td className="px-3 py-2"><ComponentBars theme={theme} /></td>
                                <td className={`${NUM} ${toneClass(theme.excess_5d)}`}>{formatSignedPercent(theme.excess_5d, 1)}</td>
                                <td className={`${NUM} ${toneClass(theme.excess_21d)}`}>{formatSignedPercent(theme.excess_21d, 1)}</td>
                                <td className={`${NUM} text-fg`}>{formatShare(theme.pct_above_ma50)}</td>
                                <td className={`${NUM} text-fg`}>{theme.ignitions_5d}</td>
                                <td className={`${NUM} ${theme.streak_top5 > 0 ? "text-fg" : "text-fg-muted"}`}>{theme.streak_top5}</td>
                                <td className="px-3 py-2">
                                    {theme.leaders.length ? (
                                        <span className="flex gap-1">
                                            {theme.leaders.map((leader) => (
                                                <Link
                                                    key={leader.ticker}
                                                    href={tickerHref(leader.ticker)}
                                                    title={leader.name ?? leader.ticker}
                                                    className="rounded border bg-surface px-1.5 py-0.5 font-mono text-xs font-semibold text-accent-strong hover:border-accent"
                                                >
                                                    {displayTicker(leader.ticker)}
                                                </Link>
                                            ))}
                                        </span>
                                    ) : <span className="text-fg-muted">—</span>}
                                </td>
                                <td className={NUM}>
                                    {theme.proxy_etf ? (
                                        <>
                                            <span className="mr-2 text-xs text-fg-muted">{displayTicker(theme.proxy_etf)}</span>
                                            <span className={toneClass(theme.proxy_excess_21d)}>{formatSignedPercent(theme.proxy_excess_21d, 1)}</span>
                                        </>
                                    ) : <span className="text-fg-muted">—</span>}
                                </td>
                            </tr>
                        );
                    })}
                </tbody>
            </table>
        </div>
    );
}
