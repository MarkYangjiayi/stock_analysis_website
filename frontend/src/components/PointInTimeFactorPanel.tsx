"use client";

import { ShieldCheck } from "lucide-react";
import { PublishedFactorSnapshot } from "@/lib/api";

interface PointInTimeFactorPanelProps {
    snapshot: PublishedFactorSnapshot | null;
    loading?: boolean;
    error?: string;
}

const FACTORS = [
    ["value", "Value", "Earnings yield and book-to-price"],
    ["quality", "Quality", "ROE, gross margin, and leverage"],
    ["growth", "Growth", "Five-year sales growth"],
    ["momentum", "Momentum", "Lagged medium-term price strength"],
    ["low_volatility", "Low volatility", "Realized volatility, inverted"],
] as const;

const clamp = (value: number, min: number, max: number) => Math.min(Math.max(value, min), max);

export default function PointInTimeFactorPanel({ snapshot, loading, error }: PointInTimeFactorPanelProps) {
    if (loading) {
        return (
            <section className="surface-panel flex min-h-[360px] animate-pulse flex-col p-5" aria-label="Loading factor snapshot">
                <div className="h-5 w-52 rounded bg-surface-muted" />
                <div className="mt-8 grid gap-7">{FACTORS.map(([key]) => <div key={key} className="h-8 rounded bg-surface-muted" />)}</div>
            </section>
        );
    }

    if (!snapshot) {
        return (
            <section className="surface-panel flex min-h-[300px] flex-col items-center justify-center p-8 text-center">
                <ShieldCheck className="text-fg-muted" size={34} />
                <h2 className="mt-4 font-semibold">Published factors unavailable</h2>
                <p className="mt-2 max-w-sm text-sm leading-6 text-fg-muted">{error || "This security is not part of the latest quality-gated factor universe."}</p>
            </section>
        );
    }

    const composite = snapshot.factors.composite?.normalized_value;

    return (
        <section className="surface-panel p-5 sm:p-6" aria-labelledby="factor-panel-title">
            <header className="flex flex-col justify-between gap-3 border-b pb-4 sm:flex-row sm:items-start">
                <div>
                    <p className="eyebrow">Point-in-time cross-section</p>
                    <h2 id="factor-panel-title" className="mt-1 text-lg font-semibold">Published factor profile</h2>
                    <p className="mt-1 text-xs text-fg-muted">Sector-neutral z-scores; zero is the universe average.</p>
                </div>
                <div className="flex flex-wrap gap-2 text-xs">
                    <span className="status-pill"><ShieldCheck size={13} /> {snapshot.version}</span>
                    <span className="rounded-full border px-2.5 py-1 font-mono font-medium text-fg-muted">As of {snapshot.as_of_date}</span>
                </div>
            </header>

            <div className="mt-5 rounded-lg border border-accent/30 bg-accent-soft p-4">
                <div className="flex items-end justify-between gap-4">
                    <div>
                        <p className="text-xs font-semibold uppercase tracking-wide text-accent-strong">Composite signal</p>
                        <p className="mt-1 text-xs text-fg-muted">Equal-weight blend of the five published factors</p>
                    </div>
                    <p className="font-mono text-2xl font-semibold text-accent-strong">{composite == null ? "—" : `${composite >= 0 ? "+" : ""}${composite.toFixed(2)}`}</p>
                </div>
            </div>

            <div className="mt-6 space-y-5">
                {FACTORS.map(([key, label, description]) => {
                    const value = snapshot.factors[key]?.normalized_value;
                    const normalized = value == null ? 50 : clamp(((value + 3) / 6) * 100, 0, 100);
                    return (
                        <div key={key}>
                            <div className="mb-2 flex items-end justify-between gap-4">
                                <div className="min-w-0">
                                    <p className="text-sm font-semibold">{label}</p>
                                    <p className="truncate text-xs text-fg-muted" title={description}>{description}</p>
                                </div>
                                <span className={`shrink-0 font-mono text-sm font-semibold ${value != null && value >= 0 ? "text-up" : "text-down"}`}>
                                    {value == null ? "—" : `${value >= 0 ? "+" : ""}${value.toFixed(2)}`}
                                </span>
                            </div>
                            <div className="relative h-2 rounded-full bg-surface-muted" role="img" aria-label={`${label} z-score ${value?.toFixed(2) ?? "unavailable"}`}>
                                <span className="absolute bottom-[-3px] left-1/2 top-[-3px] w-px bg-fg-muted/70" />
                                {value != null && <span className="absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-surface bg-accent" style={{ left: `${normalized}%` }} />}
                            </div>
                        </div>
                    );
                })}
            </div>
        </section>
    );
}
