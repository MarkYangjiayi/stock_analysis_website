"use client";

import { useEffect, useState } from "react";
import { Check, Minus, X } from "lucide-react";
import { fetchQualityScorecard, type QualityMetric, type QualityScorecardResponse } from "@/lib/api";
import { formatSignedPercent } from "@/lib/format";

const scoreClass = (score: number | null | undefined) =>
    score == null ? "text-fg-muted" : score >= 75 ? "text-up" : score >= 60 ? "text-accent" : score >= 45 ? "text-fg" : "text-down";

const barClass = (score: number) => (score >= 75 ? "bg-up" : score >= 60 ? "bg-accent" : score >= 45 ? "bg-fg-muted" : "bg-down");

const zoneClass = { safe: "text-up", grey: "text-caution", distress: "text-down" } as const;

// Changes read better with a sign; levels and shares of years do not need one.
const SIGNED_METRICS = new Set(["revenue_cagr", "accruals_ratio", "share_change"]);

const formatMetric = (key: string, metric: QualityMetric) => {
    if (metric.value == null) return "—";
    if (metric.unit === "ratio") return `${metric.value.toFixed(1)}×`;
    return SIGNED_METRICS.has(key) ? formatSignedPercent(metric.value, 1) : `${(metric.value * 100).toFixed(1)}%`;
};

const TEMPLATE_LABELS = { financial: "Financials template", reit: "REIT template" } as const;

/** Annual-statement quality: five pillars, Piotroski F-score and Altman Z (rules in docs/quality_scorecard.md). */
export default function QualityScorecardPanel({ ticker }: { ticker: string }) {
    const [result, setResult] = useState<{ ticker: string; data: QualityScorecardResponse | null; error: string }>({ ticker: "", data: null, error: "" });

    useEffect(() => {
        const controller = new AbortController();
        fetchQualityScorecard(ticker, controller.signal)
            .then((data) => setResult({ ticker, data, error: "" }))
            .catch((caught: unknown) => {
                if (caught instanceof DOMException && caught.name === "AbortError") return;
                setResult({ ticker, data: null, error: caught instanceof Error ? caught.message : "Quality scorecard unavailable." });
            });
        return () => controller.abort();
    }, [ticker]);

    const loading = result.ticker !== ticker;
    const card = loading ? null : result.data;

    return (
        <section className="research-panel" aria-label="Quality scorecard">
            <header className="flex flex-wrap items-start justify-between gap-3 px-4 pb-2 pt-4 sm:px-5">
                <div>
                    <p className="eyebrow">Quality</p>
                    <h2 className="text-base font-semibold tracking-tight">质量评分卡</h2>
                    <p className="mt-1 text-xs text-fg-muted">Five pillars from up to six fiscal years of annual statements, scored against fixed thresholds.</p>
                </div>
                {card?.score != null && (
                    <div className="text-right">
                        <p className={`font-mono text-3xl font-semibold ${scoreClass(card.score)}`}>{card.score}<span className="text-sm text-fg-muted"> / 100</span></p>
                        <p className={`text-sm font-semibold ${scoreClass(card.score)}`}>{card.grade}</p>
                    </div>
                )}
            </header>
            {loading ? (
                <div className="mx-4 mb-4 h-40 animate-pulse rounded-lg bg-surface-muted sm:mx-5" aria-label="Loading quality scorecard" />
            ) : result.error || !card ? (
                <p className="px-4 pb-4 text-sm text-fg-muted sm:px-5">{result.error || "Quality scorecard unavailable."}</p>
            ) : (
                <div className="grid gap-4 px-4 pb-4 sm:px-5 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
                    <div className="min-w-0 space-y-3">
                        {card.template && card.template !== "standard" && (
                            <p className="text-xs text-caution">{TEMPLATE_LABELS[card.template]}{card.industry ? ` · ${card.industry}` : ""}</p>
                        )}
                        {card.pillars.map((pillar) => (
                            <div key={pillar.key}>
                                <div className="flex items-baseline justify-between gap-2 text-sm">
                                    <span className="font-medium">{pillar.label}</span>
                                    <span className={`font-mono ${scoreClass(pillar.score)}`}>
                                        {!pillar.applicable ? "n/a" : pillar.score == null ? "—" : Math.round(pillar.score)}
                                    </span>
                                </div>
                                <div className="mt-1 h-1.5 rounded-full bg-surface-muted" aria-hidden="true">
                                    {pillar.score != null && <div className={`h-1.5 rounded-full ${barClass(pillar.score)}`} style={{ width: `${Math.max(pillar.score, 2)}%` }} />}
                                </div>
                                {pillar.applicable && (
                                    <ul className="mt-1 flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-fg-muted">
                                        {pillar.metrics.map((key) => card.metrics[key] && (
                                            <li key={key} title={`Weak ${card.metrics[key].weak} · strong ${card.metrics[key].strong}`}>
                                                {card.metrics[key].label} <span className="font-mono text-fg">{formatMetric(key, card.metrics[key])}</span>
                                            </li>
                                        ))}
                                    </ul>
                                )}
                            </div>
                        ))}
                    </div>
                    <div className="min-w-0 space-y-4 border-t pt-3 lg:border-l lg:border-t-0 lg:pl-4 lg:pt-0">
                        <div>
                            <p className="flex items-baseline justify-between text-sm">
                                <span className="font-medium">Piotroski F-score</span>
                                <span className="font-mono">
                                    {card.piotroski == null ? "n/a" : card.piotroski.score == null ? `${card.piotroski.available}/9 checks` : `${card.piotroski.score} / 9`}
                                </span>
                            </p>
                            {card.piotroski?.checks.length ? (
                                <ul className="mt-1 grid grid-cols-1 gap-0.5 text-xs sm:grid-cols-2 lg:grid-cols-1">
                                    {card.piotroski.checks.map((check) => (
                                        <li key={check.key} className="flex items-center gap-1.5">
                                            {check.passed == null ? <Minus size={12} className="text-fg-muted" /> : check.passed ? <Check size={12} className="text-up" /> : <X size={12} className="text-down" />}
                                            <span className={check.passed == null ? "text-fg-muted" : ""}>{check.label}</span>
                                        </li>
                                    ))}
                                </ul>
                            ) : null}
                        </div>
                        <p className="flex items-baseline justify-between text-sm">
                            <span className="font-medium">Altman Z</span>
                            {card.altman ? (
                                <span className={`font-mono ${zoneClass[card.altman.zone]}`}>{card.altman.z.toFixed(2)} · {card.altman.zone_label}</span>
                            ) : <span className="font-mono text-fg-muted">n/a</span>}
                        </p>
                    </div>
                    <footer className="text-xs text-fg-muted lg:col-span-2">
                        {card.fiscal_years.length > 0 && <>Fiscal years {card.fiscal_years[0]} to {card.fiscal_years.at(-1)} · </>}
                        {card.source}{card.fetched_at ? ` fetched ${card.fetched_at.slice(0, 10)}` : ""}
                        {card.notes.map((note) => <span key={note} className="block text-caution">{note}</span>)}
                    </footer>
                </div>
            )}
        </section>
    );
}
