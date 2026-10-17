import Link from "next/link";
import { Flame } from "lucide-react";

import { Sparkline } from "@/components/ui/Sparkline";
import type { ThemeMemberRow } from "@/lib/api";
import { formatSignedPercent } from "@/lib/format";
import { displayTicker, formatMultiple, formatShare, formatSigned, seriesChange, sparkValues, tickerHref, toneClass } from "./themeFormat";

const HEAD = "border-b px-3 py-2 font-medium";
const NUM = "px-3 py-2 text-right font-mono tabular-nums";

const REVIEW_LABELS: Record<string, string> = {
    pinned: "Pinned",
    etf_only: "ETF only · unverified",
    seed_unverified: "Seed · unverified",
};

const ROLE_LABELS: Record<string, string> = {
    pure_play: "Pure play",
    supplier: "Supplier",
    adopter: "Adopter",
    tangential: "Tangential",
};

const sourceLabel = (source: string) => {
    if (source.startsWith("etf:")) return `ETF ${displayTicker(source.slice(4))}`;
    return source.replace(/_/g, " ");
};

export default function ThemeMembersTable({ members }: { members: ThemeMemberRow[] }) {
    return (
        <div className="overflow-x-auto">
            <table className="min-w-full text-left text-sm" aria-label="Theme members">
                <thead className="bg-surface-muted font-mono text-xs font-medium uppercase tracking-[0.08em] text-fg-muted">
                    <tr className="whitespace-nowrap">
                        <th className={HEAD}>Tier</th>
                        <th className={HEAD}>Ticker</th>
                        <th className={HEAD}>Company</th>
                        <th className={`${HEAD} text-right`} title="Capped, normalized basket weight">Weight</th>
                        <th className={HEAD}>Why in theme</th>
                        <th className={`${HEAD} text-right`} title="Excess return vs SPY">5D</th>
                        <th className={`${HEAD} text-right`} title="Excess return vs SPY">21D</th>
                        <th className={`${HEAD} text-right`} title="Close as a share of the 52-week high">Near high</th>
                        <th className={`${HEAD} text-right`} title="5-day volume vs the 60-day median">RVOL</th>
                        <th className={`${HEAD} text-right`} title="Abnormal news attention (robust z)">News z</th>
                        <th className={HEAD}>Leader</th>
                        <th className={HEAD} title="Earliest +5% day on 2× volume in the last 63 sessions">First ignition</th>
                        <th className={HEAD}>60D</th>
                    </tr>
                </thead>
                <tbody className="divide-y divide-line">
                    {members.length === 0 ? (
                        <tr><td colSpan={13} className="px-6 py-16 text-center text-sm text-fg-muted">No active members for this session.</td></tr>
                    ) : members.map((member) => {
                        const review = REVIEW_LABELS[member.review_status];
                        return (
                            <tr key={member.ticker} className="align-top transition-colors hover:bg-surface-muted">
                                <td className="whitespace-nowrap px-3 py-2">
                                    <span className={`rounded border px-1.5 py-0.5 text-xs font-medium ${member.tier === "core" ? "border-accent/30 bg-accent-soft text-accent-strong" : "text-fg-muted"}`}>
                                        {member.tier === "core" ? "Core" : "Peripheral"}
                                    </span>
                                </td>
                                <td className="whitespace-nowrap px-3 py-2 font-mono font-semibold text-accent-strong">
                                    <Link href={tickerHref(member.ticker)} className="hover:underline">{displayTicker(member.ticker)}</Link>
                                    {member.ignited && <Flame size={13} className="ml-1 inline text-accent" aria-label="Ignited today" />}
                                </td>
                                <td className="max-w-48 truncate px-3 py-2 font-medium text-fg" title={member.name ?? undefined}>{member.name ?? "—"}</td>
                                <td className={`${NUM} text-fg`}>{formatShare(member.weight, 1)}</td>
                                <td className="min-w-72 max-w-md px-3 py-2">
                                    {member.evidence_quote ? (
                                        <q className="line-clamp-2 text-sm text-fg" title={member.evidence_quote}>{member.evidence_quote}</q>
                                    ) : <span className="text-fg-muted">—</span>}
                                    <span className="mt-1 flex flex-wrap gap-x-2 gap-y-0.5 font-mono text-xs text-fg-muted">
                                        <span>Exposure {member.exposure}/3</span>
                                        {member.role && <span>{ROLE_LABELS[member.role] ?? member.role}</span>}
                                        {member.evidence_source && <span>{member.evidence_source}</span>}
                                        {member.evidence_as_of && <span>as of {member.evidence_as_of}</span>}
                                        {member.sources.map((source) => <span key={source}>{sourceLabel(source)}</span>)}
                                        {review && <span className={member.review_status === "pinned" ? "text-accent-strong" : "text-caution"}>{review}</span>}
                                    </span>
                                </td>
                                <td className={`${NUM} ${toneClass(member.excess_5d)}`}>{formatSignedPercent(member.excess_5d, 1)}</td>
                                <td className={`${NUM} ${toneClass(member.excess_21d)}`}>{formatSignedPercent(member.excess_21d, 1)}</td>
                                <td className={`${NUM} text-fg`}>{formatShare(member.near_high_ratio)}</td>
                                <td className={`${NUM} text-fg`}>{formatMultiple(member.relative_volume_5d)}</td>
                                <td className={`${NUM} text-fg`}>{formatSigned(member.attention_z, 1)}</td>
                                <td className="whitespace-nowrap px-3 py-2">
                                    {member.is_leader ? (
                                        <span className="rounded border border-accent/30 bg-accent-soft px-1.5 py-0.5 font-mono text-xs font-semibold text-accent-strong">#{member.leader_rank ?? "—"}</span>
                                    ) : <span className="font-mono text-xs text-fg-muted">{member.leader_rank != null ? `#${member.leader_rank}` : "—"}</span>}
                                </td>
                                <td className="whitespace-nowrap px-3 py-2 font-mono text-xs text-fg">{member.first_ignition_date ?? "—"}</td>
                                <td className="px-3 py-2">
                                    <Sparkline values={sparkValues(member.sparkline)} className={`w-20 ${toneClass(seriesChange(member.sparkline))}`} height={18} />
                                </td>
                            </tr>
                        );
                    })}
                </tbody>
            </table>
        </div>
    );
}
