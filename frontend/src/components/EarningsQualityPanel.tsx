"use client";

import { useMemo, useState } from "react";
import {
    AlertCircle,
    AlertTriangle,
    ChevronDown,
    ChevronUp,
    ExternalLink,
    FileSearch,
    KeyRound,
    LoaderCircle,
    ShieldCheck,
} from "lucide-react";
import type {
    EarningsQualityPeriod,
    EarningsQualityPeriodType,
    EarningsQualityResponse,
} from "@/lib/api";

interface EarningsQualityPanelProps {
    data: EarningsQualityResponse | null;
    loading?: boolean;
    error?: string;
    adminKey?: string | null;
    busyPeriod?: string | null;
    onUnlock?: () => void;
    onAnalyze?: (period: EarningsQualityPeriod) => Promise<void> | void;
}

const compact = (value?: number | null) => value == null || !Number.isFinite(value)
    ? "—"
    : new Intl.NumberFormat("en-US", {
        notation: "compact",
        maximumFractionDigits: 2,
    }).format(value);

const verdictLabel: Record<EarningsQualityResponse["summary"]["verdict"], string> = {
    unavailable: "Unavailable",
    flags_present: "Potential distortions flagged",
    data_quality_warning: "Data quality limits",
    no_material_candidates_on_available_data: "No material candidates on available data",
};

const periodKey = (period: EarningsQualityPeriod) => `${period.period_type}:${period.period_end}`;

const filingCandidateCount = (period: EarningsQualityPeriod) => {
    const analysis = period.analysis;
    if (analysis?.status !== "completed" || !analysis.result) return 0;
    return analysis.result.adjustments.length
        + (analysis.validation_report?.rejected_adjustments?.length ?? 0)
        + (analysis.result.unquantified_candidates?.length ?? 0);
};

export default function EarningsQualityPanel({
    data,
    loading = false,
    error = "",
    adminKey = null,
    busyPeriod = null,
    onUnlock,
    onAnalyze,
}: EarningsQualityPanelProps) {
    const [periodType, setPeriodType] = useState<EarningsQualityPeriodType>("quarterly");
    const [expanded, setExpanded] = useState<string | null>(null);

    const periods = useMemo(
        () => data?.[periodType] ?? [],
        [data, periodType],
    );
    const totalFilingCandidates = useMemo(
        () => data
            ? [...data.quarterly, ...data.annual]
                .reduce((total, period) => total + filingCandidateCount(period), 0)
            : 0,
        [data],
    );

    if (loading && !data) {
        return <div className="flex min-h-48 items-center justify-center rounded-lg border"><LoaderCircle className="mr-2 animate-spin text-accent" size={18} /> Loading earnings-quality evidence…</div>;
    }
    if (!data) {
        return <div className="rounded-lg border border-caution/30 bg-caution/6 p-4 text-sm text-caution"><AlertTriangle className="mr-2 inline" size={17} />{error || "Earnings-quality evidence is unavailable."}</div>;
    }

    return (
        <section className="space-y-4 rounded-lg border p-4" aria-labelledby="earnings-quality-title">
            <div className="flex flex-col justify-between gap-3 md:flex-row md:items-start">
                <div>
                    <div className="flex flex-wrap items-center gap-2">
                        <h3 id="earnings-quality-title" className="text-sm font-semibold">Earnings Quality</h3>
                        <span className="font-mono text-xs text-fg-muted">E37</span>
                        <span className={`rounded-full border px-2 py-0.5 text-xs font-semibold uppercase ${totalFilingCandidates > 0 || data.summary.verdict === "flags_present" || data.summary.verdict === "data_quality_warning" ? "border-caution/30 text-caution" : data.summary.verdict === "unavailable" ? "text-fg-muted" : "border-up/30 text-up"}`}>{totalFilingCandidates > 0 ? "Filing candidates flagged" : verdictLabel[data.summary.verdict]}</span>
                    </div>
                    <p className="mt-1 max-w-3xl text-xs leading-5 text-fg-muted">Reported figures remain primary. Structured fields create screening candidates only; adjusted values appear only after SEC-source and reconciliation validation.</p>
                </div>
                <div className="segmented-control">
                    {(["quarterly", "annual"] as const).map((value) => <button key={value} type="button" onClick={() => setPeriodType(value)} aria-pressed={periodType === value} className="segmented-control-item px-3">{value === "quarterly" ? "Quarterly" : "Annual"}</button>)}
                </div>
            </div>

            {error && <div className="error-panel text-sm" role="alert">{error} Cached evidence remains visible.</div>}
            {data.summary.financial_industry_exemption && <p className="rounded-lg bg-info/10 p-3 text-xs text-info">The generic non-operating-income swing rule is disabled for this financial company.</p>}

            <div className="space-y-2">
                {periods.map((period) => {
                    const key = `${data.ticker}:${periodKey(period)}`;
                    const isExpanded = expanded === key;
                    const analysis = period.analysis;
                    const rejectedAdjustments = analysis?.validation_report?.rejected_adjustments ?? [];
                    const unquantifiedCandidates = analysis?.result?.unquantified_candidates ?? [];
                    const periodFilingCandidateCount = filingCandidateCount(period);
                    const isBusy = busyPeriod === periodKey(period) || analysis?.status === "queued" || analysis?.status === "running";
                    const highCount = period.flags.filter((flag) => flag.severity === "high").length;
                    const includedEarningsEffect = analysis?.result?.adjustments
                        .filter((adjustment) => adjustment.include_in_normalized)
                        .reduce((total, adjustment) => total + adjustment.earnings_effect_after_tax, 0) ?? null;
                    return (
                        <article key={key} className="overflow-hidden rounded-lg border">
                            <button type="button" onClick={() => setExpanded(isExpanded ? null : key)} className="flex w-full items-center justify-between gap-3 p-3.5 text-left">
                                <div className="flex min-w-0 flex-wrap items-center gap-2">
                                    <span className="font-mono text-sm font-semibold">{period.period_end}</span>
                                    {period.flags.length > 0 ? <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${highCount ? "bg-danger/10 text-danger" : "bg-caution/6 text-caution"}`}>{period.flags.length} flag{period.flags.length === 1 ? "" : "s"}</span> : period.assessment === "unavailable" ? <span className="text-xs font-medium text-fg-muted">Unavailable</span> : period.assessment === "data_quality_warning" ? <span className="text-xs font-medium text-caution">Data quality warning</span> : <span className="text-xs font-medium text-up">No material candidates on available data</span>}
                                    {periodFilingCandidateCount > 0 && <span className="rounded-full bg-caution/6 px-2 py-0.5 text-xs font-semibold text-caution">{periodFilingCandidateCount} filing flag{periodFilingCandidateCount === 1 ? "" : "s"}</span>}
                                    {period.verified_normalized?.net_income != null && <span className="inline-flex items-center gap-1 rounded-full bg-up/10 px-2 py-0.5 text-xs font-semibold text-up"><ShieldCheck size={11} /> Verified adjusted</span>}
                                    {isBusy && <span className="inline-flex items-center gap-1 text-xs font-medium text-info"><LoaderCircle size={11} className="animate-spin" /> {analysis?.stage || "queued"}</span>}
                                </div>
                                {isExpanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                            </button>

                            {isExpanded && <div className="space-y-4 border-t p-4">
                                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                                    <div className="surface-subtle rounded-lg p-3"><p className="text-xs font-medium uppercase text-fg-muted">Reported net income</p><p className="mt-1 font-mono text-base font-semibold">{compact(period.reported.net_income)}</p></div>
                                    <div className="surface-subtle rounded-lg p-3"><p className="text-xs font-medium uppercase text-fg-muted">Materiality base</p><p className="mt-1 font-mono text-base font-semibold">{compact(period.materiality_base)}</p></div>
                                    <div className="surface-subtle rounded-lg p-3"><p className="text-xs font-medium uppercase text-fg-muted">Verified normalized NI</p><p className="mt-1 font-mono text-base font-semibold text-up">{compact(period.verified_normalized?.net_income)}</p></div>
                                    <div className="surface-subtle rounded-lg p-3"><p className="text-xs font-medium uppercase text-fg-muted">Verified adjusted EPS</p><p className="mt-1 font-mono text-base font-semibold text-up">{period.verified_normalized?.adjusted_eps == null ? "—" : period.verified_normalized.adjusted_eps.toFixed(2)}</p></div>
                                </div>
                                <dl className="grid gap-x-4 gap-y-2 rounded-lg border p-3 text-xs sm:grid-cols-2 xl:grid-cols-3">{[
                                    ["Income before tax", period.reported.income_before_tax],
                                    ["Continuing-operations NI", period.reported.net_income_from_continuing_operations],
                                    ["Provider non-recurring", period.reported.non_recurring],
                                    ["Extraordinary items", period.reported.extraordinary_items],
                                    ["Discontinued operations", period.reported.discontinued_operations],
                                    ["Non-operating income, net", period.reported.non_operating_income_net_other],
                                ].map(([label, value]) => <div key={String(label)} className="flex items-center justify-between gap-3"><dt className="text-fg-muted">{label}</dt><dd className="font-mono font-semibold">{compact(value as number | null)}</dd></div>)}</dl>

                                {period.flags.length > 0 && <div className="space-y-2">{period.flags.map((flag, index) => <div key={`${flag.category}-${index}`} className={`rounded-lg border p-3 ${flag.severity === "high" ? "border-danger/30 bg-danger/10" : "border-caution/30 bg-caution/6"}`}><div className="flex flex-wrap items-center justify-between gap-2"><p className="text-xs font-semibold">{flag.label}</p><span className="font-mono text-xs font-semibold">{compact(flag.amount)} · {(flag.materiality_ratio * 100).toFixed(1)}% of base</span></div><p className="mt-1 text-xs leading-5 text-fg-muted">{flag.detail}</p>{flag.recurring_adjustment && <p className="mt-2 text-xs font-semibold uppercase text-danger">Recurring adjustment · not one-time</p>}</div>)}</div>}

                                {period.data_quality_warnings.map((warning) => <div key={warning.code} className="rounded-lg border border-caution/30 bg-caution/6 p-3 text-xs text-caution"><AlertCircle className="mr-1.5 inline" size={14} /><span className="font-mono text-xs">{warning.code}</span><p className="mt-1">{warning.message}</p></div>)}

                                {analysis?.status === "completed" && analysis.result && <div className="space-y-3 rounded-lg border p-3"><div className="flex flex-wrap items-center justify-between gap-2"><p className="text-xs font-semibold">Filing extraction</p><span className={`rounded-full px-2 py-0.5 text-xs font-semibold uppercase ${analysis.result.verification_status === "verified" ? "bg-up/10 text-up" : "bg-surface-muted text-fg-muted"}`}>{analysis.result.verification_status === "verified" ? "Verified" : "Shadow / flag only"}</span></div>
                                    <div className="grid gap-2 rounded-lg bg-surface-muted p-3 text-xs sm:grid-cols-3"><div><p className="text-xs font-medium uppercase text-fg-muted">Reported NI</p><p className="mt-1 font-mono font-semibold">{compact(analysis.result.reported_net_income)}</p></div><div><p className="text-xs font-medium uppercase text-fg-muted">Minus included effects</p><p className="mt-1 font-mono font-semibold">{compact(includedEarningsEffect)}</p></div><div><p className="text-xs font-medium uppercase text-fg-muted">Verified normalized NI</p><p className="mt-1 font-mono font-semibold">{compact(analysis.result.normalized_net_income)}</p></div><p className="text-xs leading-4 text-fg-muted sm:col-span-3">normalized NI = reported NI − Σ earnings_effect_after_tax; positive effects raised reported earnings and negative effects reduced it.</p></div>
                                    {analysis.result.company_adjusted && <div className="rounded-lg bg-info/10 p-3 text-xs text-info"><p className="font-semibold">AI-extracted company disclosure · {analysis.result.company_adjusted.label}</p><p className="mt-1 font-mono">Net income {compact(analysis.result.company_adjusted.adjusted_net_income)} ({analysis.validation_report?.verified ? "source-reconciled" : "unverified"}) · Diluted EPS {analysis.result.company_adjusted.adjusted_diluted_eps == null ? "—" : analysis.result.company_adjusted.adjusted_diluted_eps.toFixed(2)} ({analysis.validation_report?.eps_verified ? "source-reconciled" : "unverified"})</p><p className="mt-1 text-xs leading-4 opacity-80">Raw disclosure extraction is retained for auditability; only the verified cards above and dashed chart line are adjusted outputs.</p></div>}
                                    {analysis.result.adjustments.length ? <div className="space-y-2">{analysis.result.adjustments.map((adjustment, index) => <div key={`${adjustment.label}-${index}`} className="rounded-lg bg-surface-muted p-3 text-xs"><div className="flex flex-wrap justify-between gap-2"><span className="font-semibold">{adjustment.label}</span><span className="font-mono">After tax {compact(adjustment.earnings_effect_after_tax)}</span></div><div className="mt-2 grid grid-cols-3 gap-2 font-mono text-xs"><span>Pre-tax {compact(adjustment.pretax_earnings_effect)}</span><span>Tax {compact(adjustment.tax_effect)}</span><span>After-tax {compact(adjustment.earnings_effect_after_tax)}</span></div><p className="mt-1 text-fg-muted">{adjustment.category.replaceAll("_", " ")} · {adjustment.cash_effect.replace("_", " ")} · {adjustment.include_in_normalized ? "included if verified" : "flag only"}</p><blockquote className="mt-2 border-l-2 pl-2 text-xs leading-4 text-fg-muted">{adjustment.citation.excerpt}</blockquote><p className="mt-2 font-mono text-xs text-fg-muted">{adjustment.citation.accession} · {adjustment.citation.document_name} · {adjustment.citation.section} · source amount {compact(adjustment.citation.source_amount)} × {compact(adjustment.citation.source_unit_scale)}</p></div>)}</div> : rejectedAdjustments.length || unquantifiedCandidates.length ? <p className="text-xs text-fg-muted">No source-validated adjustment amount is available.</p> : <p className="text-xs text-fg-muted">No adjustment item was extracted.</p>}
                                    {unquantifiedCandidates.length > 0 && <div className="rounded-lg border border-caution/30 bg-caution/6 p-3 text-xs text-caution"><p className="font-semibold">Unquantified filing candidates</p><p className="mt-1 leading-5">These filing items remain visible as red flags, but no finite, non-zero earnings effect was available for amount validation.</p><div className="mt-2 space-y-2">{unquantifiedCandidates.map((candidate, index) => <div key={`${index}-${candidate.label}`} className="rounded-md border border-caution/30 p-2"><p className="font-semibold">{candidate.label}</p><p className="mt-1 text-xs text-caution/80">{candidate.policy_category.replaceAll("_", " ")} · flag only · amount unavailable</p></div>)}</div></div>}
                                    {rejectedAdjustments.length > 0 && <div className="rounded-lg border border-caution/30 bg-caution/6 p-3 text-xs text-caution"><p className="font-semibold">Unverified filing candidates</p><p className="mt-1 leading-5">Candidate names and categories are shown as red flags; amounts are hidden because source, period, or amount validation did not pass.</p><div className="mt-2 space-y-2">{rejectedAdjustments.map((candidate) => <div key={`${candidate.adjustment_index}-${candidate.label}`} className="rounded-md border border-caution/30 p-2"><p className="font-semibold">{candidate.label}</p><p className="mt-1 text-xs text-caution/80">{candidate.policy_category.replaceAll("_", " ")} · flag only</p><p className="mt-1 font-mono text-xs text-caution/70">{candidate.failure_codes?.length ? candidate.failure_codes.join(" · ") : "source_or_period_validation_failed"}</p></div>)}</div></div>}
                                    {analysis.validation_report && !analysis.validation_report.verified && <div className="rounded-lg bg-caution/6 p-3 text-xs text-caution"><p className="font-semibold">Why adjusted values are withheld</p><ul className="mt-1 list-disc space-y-1 pl-4">{analysis.validation_report.failures.map((failure, index) => <li key={`${failure.code}-${index}`}>{failure.message}</li>)}</ul></div>}
                                    {analysis.validation_report?.eps_failures?.length ? <div className="rounded-lg bg-caution/6 p-3 text-xs text-caution"><p className="font-semibold">Why adjusted EPS is withheld</p><ul className="mt-1 list-disc space-y-1 pl-4">{analysis.validation_report.eps_failures.map((failure, index) => <li key={`${failure.code}-${index}`}>{failure.message}</li>)}</ul></div> : null}
                                    {analysis.source_snapshots.length > 0 && <div className="flex flex-wrap gap-2">{analysis.source_snapshots.map((source) => source.source_url && <a key={source.source_id} href={source.source_url} target="_blank" rel="noreferrer" className="secondary-button min-h-8 px-2.5 py-1 text-xs"><ExternalLink size={12} /> {source.form} source</a>)}</div>}
                                </div>}

                                {analysis?.status === "failed" && <div className="rounded-lg border border-danger/30 bg-danger/10 p-3 text-xs text-danger"><p className="font-semibold">Analysis failed</p><p className="mt-1">{analysis.error_message || "The filing analysis could not be completed."}</p></div>}

                                {analysis?.status === "waiting_for_filing" && <div className="rounded-lg border border-info/30 bg-info/10 p-3 text-xs text-info"><p className="font-semibold">SEC filing not available yet</p><p className="mt-1">{analysis.error_message || "The matching filing has not been submitted for this reporting period. Check again after it is filed."}</p></div>}

                                {(!analysis || analysis.status === "failed" || analysis.status === "waiting_for_filing") && <div className="flex flex-col gap-2 border-t pt-3 sm:flex-row sm:items-center sm:justify-between"><p className="text-xs leading-5 text-fg-muted">Runs only for this period after you click. It is never part of sync, scheduling, or batch work.</p>{!data.sec_analysis.supported ? <span className="text-xs font-medium text-fg-muted">{data.sec_analysis.reason}</span> : <button type="button" disabled={isBusy} onClick={() => adminKey ? void onAnalyze?.(period) : onUnlock?.()} className="primary-button shrink-0"><span>{adminKey ? <FileSearch size={15} /> : <KeyRound size={15} />}</span>{adminKey ? (analysis?.status === "failed" ? "Retry filing analysis" : analysis?.status === "waiting_for_filing" ? "Check for filing" : "Analyze filing") : "Unlock to analyze"}</button>}</div>}
                            </div>}
                        </article>
                    );
                })}
                {periods.length === 0 && <p className="rounded-lg bg-surface-muted p-4 text-sm text-fg-muted">No local {periodType} statements are available. A clean verdict is not inferred.</p>}
            </div>

            <p className="text-xs leading-5 text-fg-muted">{data.summary.message} Materiality uses {data.methodology.materiality_base}; warning at 10%, high at 25%.</p>
        </section>
    );
}
