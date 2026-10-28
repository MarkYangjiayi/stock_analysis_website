"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Check, Copy, ExternalLink, FileCode, Lightbulb, RotateCcw, X } from "lucide-react";

import { SegmentedControl } from "@/components/ui/SegmentedControl";
import {
    ApiError,
    decideThemeProposal,
    fetchThemeProposals,
    type ThemeProposalDecision,
    type ThemeProposalItem,
    type ThemeProposalStatus,
} from "@/lib/api";
import { displayTicker, tickerHref } from "./themeFormat";

type StatusFilter = "pending" | "all";

const STATUS_CLASSES: Record<ThemeProposalStatus, string> = {
    pending: "border-accent/30 bg-accent-soft text-accent-strong",
    accepted: "border-up/30 bg-up/10 text-up",
    rejected: "border-line bg-surface-muted text-fg-muted",
    expired: "border-dashed border-line-strong text-fg-muted",
};

const safeLink = (value: unknown) => {
    if (typeof value !== "string") return null;
    try {
        const url = new URL(value);
        return url.protocol === "https:" || url.protocol === "http:" ? url.toString() : null;
    } catch {
        return null;
    }
};

const evidenceText = (item: Record<string, unknown>) => {
    for (const key of ["label", "title", "headline", "text"]) {
        const value = item[key];
        if (typeof value === "string" && value) return value;
    }
    return null;
};

type Evidence = ThemeProposalItem["evidence"][number];

const CITATION = /\[(E\d+)\]/g;
const CITATION_PART = /^\[(E\d+)\]$/;
const MAX_LISTED_EVIDENCE = 8;

const evidenceId = (item: Evidence) => (typeof item.id === "string" ? item.id : null);
const evidenceAnchor = (proposalId: number, id: string) => `proposal-${proposalId}-evidence-${id}`;
const isCompany = (item: Evidence) => item.kind === "company";

/**
 * The worker lists every cluster company (no source link) before the headlines, and the
 * rationale must cite a headline, so lead with what the rationale cites, then other
 * headlines, and fold the company profiles away instead of letting them crowd the card.
 */
function splitEvidence(proposal: ThemeProposalItem) {
    const cited = [...new Set([...(proposal.rationale ?? "").matchAll(CITATION)].map((match) => match[1]))];
    const rank = (item: Evidence) => {
        const at = cited.indexOf(evidenceId(item) ?? "");
        if (at >= 0) return at;
        return isCompany(item) ? cited.length + 1 : cited.length;
    };
    const ordered = [...proposal.evidence].sort((a, b) => rank(a) - rank(b));
    const listed = ordered.filter((item) => !isCompany(item) || cited.includes(evidenceId(item) ?? "")).slice(0, MAX_LISTED_EVIDENCE);
    const rest = ordered.filter((item) => !listed.includes(item));
    return { listed, rest };
}

/** Rationale is LLM text: render it as plain text, turning only known [E#] citations into in-card links. */
function Rationale({ proposal }: { proposal: ThemeProposalItem }) {
    const known = new Set(proposal.evidence.map(evidenceId));
    return (
        <p className="mt-3 text-sm leading-6 text-fg">
            {(proposal.rationale ?? "").split(/(\[E\d+\])/).map((part, index) => {
                const id = CITATION_PART.exec(part)?.[1];
                if (!id || !known.has(id)) return part;
                return (
                    <a key={index} href={`#${evidenceAnchor(proposal.id, id)}`} className="rounded bg-accent-soft px-1 font-mono text-xs font-semibold text-accent-strong hover:underline">
                        {id}
                    </a>
                );
            })}
        </p>
    );
}

function EvidenceList({ proposalId, items, className }: { proposalId: number; items: Evidence[]; className: string }) {
    return (
        <ul className={className}>
            {items.map((item, index) => {
                const id = evidenceId(item);
                const link = safeLink(item.link);
                const text = evidenceText(item) ?? id ?? `Evidence ${index + 1}`;
                return (
                    <li key={id ?? index} id={id ? evidenceAnchor(proposalId, id) : undefined} className="flex min-w-0 scroll-mt-24 items-center gap-1.5 rounded target:bg-accent-soft">
                        {id && <span className="font-mono text-fg">{id}</span>}
                        <span className="truncate">{text}</span>
                        {link && <a href={link} target="_blank" rel="noopener noreferrer" className="shrink-0 text-accent-strong" aria-label={`Open source for ${text}`}><ExternalLink size={12} /></a>}
                    </li>
                );
            })}
        </ul>
    );
}

/** Owner review queue for worker-suggested themes; accepting returns a taxonomy YAML snippet. */
export default function ThemeProposalsPanel({ onUnauthorized }: { onUnauthorized: () => void }) {
    const [filter, setFilter] = useState<StatusFilter>("pending");
    const [proposals, setProposals] = useState<ThemeProposalItem[] | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [retryKey, setRetryKey] = useState(0);
    const [deciding, setDeciding] = useState<number | null>(null);
    const [snippets, setSnippets] = useState<Record<number, string>>({});
    const [copied, setCopied] = useState<number | null>(null);

    const load = useCallback(async (signal?: AbortSignal) => {
        setLoading(true);
        setError("");
        try {
            const response = await fetchThemeProposals(filter === "pending" ? "pending" : null, signal);
            setProposals(response.proposals);
        } catch (caught) {
            if (caught instanceof DOMException && caught.name === "AbortError") return;
            if (caught instanceof ApiError && caught.status === 401) {
                onUnauthorized();
                return;
            }
            setProposals(null);
            setError(caught instanceof Error ? caught.message : "Unable to load theme proposals.");
        } finally {
            if (!signal?.aborted) setLoading(false);
        }
    }, [filter, onUnauthorized]);

    useEffect(() => {
        const controller = new AbortController();
        void load(controller.signal);
        return () => controller.abort();
    }, [load, retryKey]);

    const decide = async (proposal: ThemeProposalItem, decision: ThemeProposalDecision) => {
        setDeciding(proposal.id);
        setError("");
        try {
            const result = await decideThemeProposal(proposal.id, decision);
            setProposals((current) => current?.map((item) => (item.id === result.proposal.id ? result.proposal : item)) ?? null);
            const snippet = result.yaml_snippet;
            if (snippet) setSnippets((current) => ({ ...current, [proposal.id]: snippet }));
        } catch (caught) {
            if (caught instanceof ApiError && caught.status === 401) {
                onUnauthorized();
                return;
            }
            setError(caught instanceof Error ? caught.message : "Unable to record the decision.");
        } finally {
            setDeciding(null);
        }
    };

    const copySnippet = async (id: number) => {
        try {
            await navigator.clipboard.writeText(snippets[id]);
            setCopied(id);
        } catch {
            setCopied(null);
        }
    };

    return (
        <div className="space-y-4 p-4 sm:p-5">
            <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-center">
                <p className="max-w-2xl text-sm text-fg-muted">
                    Clusters of co-moving, unusually active companies outside every current theme. Accepting a proposal returns a snippet to add to the taxonomy file; nothing changes until it is committed.
                </p>
                <SegmentedControl
                    label="Proposal status"
                    value={filter}
                    options={[{ value: "pending", label: "Pending" }, { value: "all", label: "All" }]}
                    onChange={setFilter}
                />
            </div>

            {error && (
                <div className="error-panel flex items-center justify-between gap-4" role="alert">
                    <span>{error}</span>
                    <button type="button" className="secondary-button shrink-0" onClick={() => setRetryKey((value) => value + 1)}>
                        <RotateCcw size={15} /> Retry
                    </button>
                </div>
            )}

            {loading && !proposals ? (
                <div className="grid gap-3 lg:grid-cols-2">
                    {[0, 1].map((index) => <div key={index} className="h-40 animate-pulse rounded-lg bg-surface-muted" />)}
                </div>
            ) : proposals && proposals.length === 0 ? (
                <div className="flex min-h-[220px] flex-col items-center justify-center rounded-lg border border-dashed p-6 text-center">
                    <Lightbulb className="text-fg-muted" size={26} aria-hidden="true" />
                    <p className="mt-3 font-semibold text-fg">{filter === "pending" ? "No pending proposals" : "No proposals yet"}</p>
                    <p className="mt-1 text-sm text-fg-muted">The weekly job suggests new themes when an untracked cluster keeps moving together.</p>
                </div>
            ) : (
                <ul className={`grid gap-3 lg:grid-cols-2 ${loading ? "opacity-60" : ""}`} aria-busy={loading}>
                    {proposals?.map((proposal) => {
                        const evidence = splitEvidence(proposal);
                        return (
                            <li key={proposal.id} className="min-w-0 rounded-lg border bg-surface p-4">
                                <div className="flex items-start justify-between gap-3">
                                    <div className="min-w-0">
                                        <h3 className="font-semibold text-fg">{proposal.label_en ?? proposal.slug_suggestion ?? `Proposal ${proposal.id}`}</h3>
                                        <p className="mt-0.5 text-sm text-fg-muted">
                                            {proposal.label_zh ?? "—"}
                                            {proposal.slug_suggestion && <span className="ml-2 font-mono text-xs">{proposal.slug_suggestion}</span>}
                                        </p>
                                    </div>
                                    <span className={`shrink-0 rounded-md border px-2 py-0.5 text-xs font-medium capitalize ${STATUS_CLASSES[proposal.status]}`}>{proposal.status}</span>
                                </div>
                                {proposal.rationale && <Rationale proposal={proposal} />}
                                <div className="mt-3 flex flex-wrap gap-1">
                                    {proposal.tickers.map((ticker) => (
                                        <Link key={ticker} href={tickerHref(ticker)} className="rounded border bg-surface px-1.5 py-0.5 font-mono text-xs font-semibold text-accent-strong hover:border-accent">
                                            {displayTicker(ticker)}
                                        </Link>
                                    ))}
                                </div>
                                {evidence.listed.length > 0 && (
                                    <EvidenceList proposalId={proposal.id} items={evidence.listed} className="mt-3 space-y-1 text-xs text-fg-muted" />
                                )}
                                {evidence.rest.length > 0 && (
                                    <details className="mt-2 text-xs text-fg-muted">
                                        <summary className="cursor-pointer select-none font-medium hover:text-fg">
                                            {evidence.rest.every(isCompany) ? "Company profiles" : "More evidence"} ({evidence.rest.length})
                                        </summary>
                                        <EvidenceList proposalId={proposal.id} items={evidence.rest} className="mt-1 space-y-1" />
                                    </details>
                                )}
                                <p className="mt-3 font-mono text-xs text-fg-muted">
                                    First seen {proposal.first_seen} · last seen {proposal.last_seen} · {proposal.hit_count} {proposal.hit_count === 1 ? "hit" : "hits"}
                                </p>
                                {proposal.status === "pending" && (
                                    <div className="mt-3 flex gap-2">
                                        <button type="button" className="primary-button" disabled={deciding != null} onClick={() => void decide(proposal, "accepted")}>
                                            <Check size={15} /> {deciding === proposal.id ? "Saving…" : "Accept"}
                                        </button>
                                        <button type="button" className="secondary-button" disabled={deciding != null} onClick={() => void decide(proposal, "rejected")}>
                                            <X size={15} /> Reject
                                        </button>
                                    </div>
                                )}
                                {/* The snippet lives only in this panel's state and the list never returns it, so decided
                                    proposals can ask again; the backend re-decides and rebuilds it. */}
                                {proposal.status === "rejected" && (
                                    <div className="mt-3 flex gap-2">
                                        <button type="button" className="secondary-button" disabled={deciding != null} title="Undo the rejection and get the taxonomy snippet" onClick={() => void decide(proposal, "accepted")}>
                                            <Check size={15} /> {deciding === proposal.id ? "Saving…" : "Accept instead"}
                                        </button>
                                    </div>
                                )}
                                {proposal.status === "accepted" && !snippets[proposal.id] && (
                                    <div className="mt-3 flex gap-2">
                                        <button type="button" className="secondary-button" disabled={deciding != null} title="Request the snippet again; its slug skips any already in the taxonomy file" onClick={() => void decide(proposal, "accepted")}>
                                            <FileCode size={15} /> {deciding === proposal.id ? "Loading…" : "Show snippet"}
                                        </button>
                                    </div>
                                )}
                                {snippets[proposal.id] && (
                                    <div className="mt-3 rounded-md border bg-surface-muted">
                                        <div className="flex items-center justify-between border-b px-3 py-1.5 text-xs text-fg-muted">
                                            <span>Add to services/themes/taxonomy/themes_v1.yaml</span>
                                            <button type="button" className="inline-flex items-center gap-1 font-medium text-fg hover:text-accent-strong" onClick={() => void copySnippet(proposal.id)}>
                                                <Copy size={12} /> {copied === proposal.id ? "Copied" : "Copy"}
                                            </button>
                                        </div>
                                        <pre className="custom-scrollbar max-h-64 overflow-auto p-3 font-mono text-xs text-fg">{snippets[proposal.id]}</pre>
                                    </div>
                                )}
                            </li>
                        );
                    })}
                </ul>
            )}
        </div>
    );
}
