"use client";

import OperatingForecastEditor from "./OperatingForecastEditor";

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
    AlertTriangle,
    BarChart3,
    Bot,
    Calculator,
    CheckCircle2,
    ChevronRight,
    Database,
    FileWarning,
    KeyRound,
    LoaderCircle,
    RefreshCw,
    RotateCcw,
    Save,
    ShieldAlert,
} from "lucide-react";
import AIReport from "@/components/AIReport";
import EarningsQualityPanel from "@/components/EarningsQualityPanel";
import EventsExpectationsPanel from "@/components/EventsExpectationsPanel";
import PeerMultipleDistribution from "@/components/PeerMultipleDistribution";
import DecisionOverview from "@/components/analysis/DecisionOverview";
import {
    ApiError,
    calculateDecisionValuation,
    DecisionSummaryMetric,
    DecisionSupportResponse,
    DecisionValuation,
    DecisionValuationScenarioInput,
    DecisionWarning,
    EarningsQualityPeriod,
    EarningsQualityResponse,
    EventsExpectationsResponse,
    PeerMetric,
    resetPersonalValuationScenarios,
    savePersonalValuationScenarios,
} from "@/lib/api";

export type CockpitTab = "overview" | "valuation" | "peers" | "risks" | "brief";

interface DecisionCockpitProps {
    ticker: string;
    decision: DecisionSupportResponse | null;
    loading: boolean;
    error: string;
    adminKey: string | null;
    onUnlock: () => void;
    onUnauthorized: () => void;
    onRetry: () => void;
    onRefresh: () => Promise<void>;
    onShowEvidence: (metric: DecisionWarning["evidence_metric"]) => void;
    earningsQuality?: EarningsQualityResponse | null;
    earningsQualityLoading?: boolean;
    earningsQualityError?: string;
    earningsQualityBusyPeriod?: string | null;
    onAnalyzeEarningsPeriod?: (period: EarningsQualityPeriod) => Promise<void> | void;
    eventsExpectations?: EventsExpectationsResponse | null;
    eventsExpectationsLoading?: boolean;
    eventsExpectationsError?: string;
    activeView?: CockpitTab;
    onViewChange?: (view: CockpitTab) => void;
    hideNavigation?: boolean;
    overviewContent?: ReactNode;
}

const DEFAULT_SCENARIOS: DecisionValuationScenarioInput[] = [
    { scenario: "bear", fcf_growth_rate: 0.05, wacc: 0.09, perpetual_growth: 0.025 },
    { scenario: "base", fcf_growth_rate: 0.10, wacc: 0.09, perpetual_growth: 0.025 },
    { scenario: "bull", fcf_growth_rate: 0.15, wacc: 0.09, perpetual_growth: 0.025 },
];

type ScenarioRateField = "fcf_growth_rate" | "wacc" | "perpetual_growth";
type ScenarioDraft = Pick<DecisionValuationScenarioInput, "operating_forecast" | "terminal_roic" | "forecast_as_of"> & {
    scenario: DecisionValuationScenarioInput["scenario"];
    fcf_growth_rate: string;
    wacc: string;
    perpetual_growth: string;
};

const toScenarioDrafts = (inputs: DecisionValuationScenarioInput[]): ScenarioDraft[] => inputs.map((item) => ({
    operating_forecast: item.operating_forecast, terminal_roic: item.terminal_roic, forecast_as_of: item.forecast_as_of,
    scenario: item.scenario,
    fcf_growth_rate: (item.fcf_growth_rate * 100).toString(),
    wacc: (item.wacc * 100).toString(),
    perpetual_growth: (item.perpetual_growth * 100).toString(),
}));

const valuationEvidenceFingerprint = (valuation: DecisionValuation | null | undefined) => JSON.stringify(
    valuation
        ? {
            available: valuation.available,
            unavailable_reasons: valuation.unavailable_reasons,
            inputs: valuation.inputs,
            current_price: valuation.current_price,
            scenarios: valuation.scenarios,
            implied_growth: valuation.implied_growth,
            position: valuation.position,
            sensitivity: valuation.sensitivity,
            formula: valuation.formula,
        }
        : null,
);

const tabs: Array<{ key: CockpitTab; label: string; icon: typeof Calculator }> = [
    { key: "overview", label: "Overview", icon: BarChart3 },
    { key: "valuation", label: "Valuation", icon: Calculator },
    { key: "peers", label: "Peer Benchmarks", icon: Database },
    { key: "risks", label: "Risks", icon: FileWarning },
    { key: "brief", label: "Evidence Brief", icon: Bot },
];

const money = (value?: number | null, currency?: string | null) => {
    if (value == null || !Number.isFinite(value)) return "—";
    const normalizedCurrency = currency?.trim().toUpperCase() || "USD";
    try {
        return new Intl.NumberFormat("en-US", {
            style: "currency",
            currency: normalizedCurrency,
            maximumFractionDigits: 2,
        }).format(value);
    } catch {
        return new Intl.NumberFormat("en-US", {
            style: "currency",
            currency: "USD",
            maximumFractionDigits: 2,
        }).format(value);
    }
};

const compact = (value?: number | null) => value == null || !Number.isFinite(value)
    ? "—"
    : new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 }).format(value);

const peerValue = (metric: Pick<PeerMetric, "value" | "format"> | DecisionSummaryMetric) => {
    if (metric.value == null) return "—";
    if (metric.format === "percent") return `${(metric.value * 100).toFixed(1)}%`;
    if (metric.format === "multiple") return `${metric.value.toFixed(1)}×`;
    return metric.value.toFixed(2);
};

const betterPositionedTone = (percentile: number | null, available: boolean) => {
    if (!available || percentile == null) {
        return {
            label: "Unavailable",
            className: "border-slate-200 bg-slate-50 text-slate-500 dark:border-slate-700 dark:bg-slate-900/60 dark:text-slate-400",
        };
    }
    if (percentile >= 75) {
        return {
            label: "Strong",
            className: "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-300",
        };
    }
    if (percentile >= 50) {
        return {
            label: "Above median",
            className: "border-teal-200 bg-teal-50 text-teal-800 dark:border-teal-900 dark:bg-teal-950/30 dark:text-teal-300",
        };
    }
    if (percentile >= 25) {
        return {
            label: "Below median",
            className: "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-300",
        };
    }
    return {
        label: "Weak",
        className: "border-rose-200 bg-rose-50 text-rose-800 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-300",
    };
};

const betterPositionedLegend = [
    { label: "75–100 strong", className: "border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-300" },
    { label: "50–75 above median", className: "border-teal-200 bg-teal-50 text-teal-800 dark:border-teal-900 dark:bg-teal-950/30 dark:text-teal-300" },
    { label: "25–50 below median", className: "border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-300" },
    { label: "0–25 weak", className: "border-rose-200 bg-rose-50 text-rose-800 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-300" },
];

function SummaryMetricCard({ metric, tone }: { metric: DecisionSummaryMetric; tone: "strong" | "weak" }) {
    return (
        <article className={`rounded-xl border p-3.5 ${tone === "strong" ? "border-emerald-200 bg-emerald-50/70 dark:border-emerald-900 dark:bg-emerald-950/20" : "border-rose-200 bg-rose-50/70 dark:border-rose-900 dark:bg-rose-950/20"}`}>
            <div className="flex items-start justify-between gap-3">
                <div><p className="text-xs font-bold">{metric.label}</p><p className="mt-1 font-mono text-lg font-black">{peerValue(metric)}</p></div>
                <span className="rounded-full border bg-white/70 px-2 py-1 font-mono text-[11px] font-bold dark:bg-slate-900/50">{metric.desirability_percentile.toFixed(0)}th</span>
            </div>
            <p className="mt-2 text-[11px] text-slate-500">Better-positioned percentile · {metric.scope} · {metric.direction.replace("_", " ")}</p>
        </article>
    );
}

function RiskCard({ warning, onShowEvidence }: { warning: DecisionWarning; onShowEvidence: () => void }) {
    const high = warning.severity === "high";
    return (
        <article className={`rounded-xl border p-4 ${high ? "border-rose-200 bg-rose-50/70 dark:border-rose-900 dark:bg-rose-950/20" : "border-amber-200 bg-amber-50/70 dark:border-amber-900 dark:bg-amber-950/20"}`}>
            <div className="flex items-start gap-3">
                <span className={`rounded-lg p-2 ${high ? "bg-rose-100 text-rose-600 dark:bg-rose-950" : "bg-amber-100 text-amber-700 dark:bg-amber-950"}`}><ShieldAlert size={17} /></span>
                <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2"><h3 className="text-sm font-black">{warning.title}</h3><span className="rounded-full border px-2 py-0.5 text-[10px] font-black uppercase tracking-wide">{warning.severity}</span><span className="font-mono text-[10px] text-slate-500">{warning.evidence_id}</span></div>
                    <p className="mt-1.5 text-sm leading-5 text-slate-600 dark:text-slate-400">{warning.message}</p>
                    <button type="button" onClick={onShowEvidence} className="mt-3 inline-flex items-center gap-1 text-xs font-bold text-emerald-700 hover:underline dark:text-emerald-300">Show evidence <ChevronRight size={13} /></button>
                </div>
            </div>
        </article>
    );
}

function WarningResults({
    warnings,
    unavailableCount,
    limit,
    onShowEvidence,
}: {
    warnings: DecisionWarning[];
    unavailableCount: number;
    limit?: number;
    onShowEvidence: (metric: DecisionWarning["evidence_metric"]) => void;
}) {
    const displayedWarnings = limit == null ? warnings : warnings.slice(0, limit);
    return (
        <div className="space-y-3">
            {displayedWarnings.length > 0 && <div className="grid gap-3 lg:grid-cols-2">{displayedWarnings.map((warning) => <RiskCard key={warning.id} warning={warning} onShowEvidence={() => onShowEvidence(warning.evidence_metric)} />)}</div>}
            {warnings.length === 0 && unavailableCount === 0 && <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/20 dark:text-emerald-300"><CheckCircle2 className="mr-2 inline" size={17} />No fundamental warning rule is triggered by the fully evaluated history.</div>}
            {unavailableCount > 0 && <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/20 dark:text-amber-300"><AlertTriangle className="mr-2 inline" size={17} />{unavailableCount} fundamental warning {unavailableCount === 1 ? "check is" : "checks are"} unavailable because required history is missing. No clean conclusion is shown for {unavailableCount === 1 ? "that rule" : "those rules"}.</div>}
        </div>
    );
}

export default function DecisionCockpit({
    ticker,
    decision,
    loading,
    error,
    adminKey,
    onUnlock,
    onUnauthorized,
    onRetry,
    onRefresh,
    onShowEvidence,
    earningsQuality = null,
    earningsQualityLoading = false,
    earningsQualityError = "",
    earningsQualityBusyPeriod = null,
    onAnalyzeEarningsPeriod,
    eventsExpectations = null,
    eventsExpectationsLoading = false,
    eventsExpectationsError = "",
    activeView,
    onViewChange,
    hideNavigation = false,
    overviewContent,
}: DecisionCockpitProps) {
    const [internalActiveTab, setInternalActiveTab] = useState<CockpitTab>("overview");
    const activeTab = activeView ?? internalActiveTab;
    const setActiveTab = (next: CockpitTab) => {
        if (activeView == null) setInternalActiveTab(next);
        onViewChange?.(next);
    };
    const [peerScope, setPeerScope] = useState<"industry" | "sector">("industry");
    const [scenarioDrafts, setScenarioDrafts] = useState<ScenarioDraft[]>(() => toScenarioDrafts(DEFAULT_SCENARIOS));
    const [workingValuation, setWorkingValuation] = useState<DecisionValuation | null>(() => decision?.valuation ?? null);
    const [valuationBusy, setValuationBusy] = useState(false);
    const [valuationError, setValuationError] = useState("");
    const [saveMessage, setSaveMessage] = useState("");
    const tickerRef = useRef(ticker);
    const valuationRequestVersionRef = useRef(0);
    tickerRef.current = ticker;

    const valuationRequestIsCurrent = (
        requestTicker: string,
        requestVersion: number,
    ) => tickerRef.current === requestTicker
        && valuationRequestVersionRef.current === requestVersion;

    useEffect(() => {
        valuationRequestVersionRef.current += 1;
        setValuationBusy(false);
        setInternalActiveTab("overview");
        setValuationError("");
        setSaveMessage("");
    }, [ticker]);

    useEffect(() => {
        if (!decision) return;
        valuationRequestVersionRef.current += 1;
        setValuationBusy(false);
        setWorkingValuation(decision.valuation);
        setScenarioDrafts(toScenarioDrafts(decision.valuation.scenarios.map((item) => ({ ...item.assumptions }))));
    }, [decision]);

    const valuation = workingValuation;
    const impliedGrowth = valuation?.implied_growth;
    const formatMoney = (value?: number | null) => money(
        value,
        decision?.metadata.currency,
    );
    const selectedPeerAvailable = useMemo(
        () => decision?.peer_comparison.metrics.filter((metric) => metric[peerScope].available).length ?? 0,
        [decision, peerScope],
    );
    const unavailableWarningCheckCount = useMemo(
        () => decision?.evidence.filter(
            (item) => item.kind === "fundamental_warning" && !item.available,
        ).length ?? 0,
        [decision],
    );
    const decisionValuationEvidence = useMemo(
        () => valuationEvidenceFingerprint(decision?.valuation),
        [decision],
    );
    const workingValuationEvidence = useMemo(
        () => valuationEvidenceFingerprint(workingValuation),
        [workingValuation],
    );
    const briefIsOutOfSync = Boolean(
        decision
        && (
            !workingValuation
            || workingValuationEvidence !== decisionValuationEvidence
        ),
    );
    const briefEvidenceKey = useMemo(() => JSON.stringify({
        metadata: decision?.metadata ?? null,
        valuationAssumptions: decision?.valuation.scenarios.map((item) => item.assumptions) ?? [],
        evidence: decision?.evidence ?? [],
        workingValuationEvidence,
    }), [decision, workingValuationEvidence]);

    const editScenario = (index: number, key: ScenarioRateField, percentValue: string) => {
        setScenarioDrafts((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, [key]: percentValue } : item));
        setSaveMessage("");
    };

    const editSharedRate = (key: "wacc" | "perpetual_growth", percentValue: string) => {
        setScenarioDrafts((current) => current.map((item) => ({ ...item, [key]: percentValue })));
        setSaveMessage("");
    };

    const parseScenarioDrafts = () => {
        const labels: Record<ScenarioRateField, string> = {
            fcf_growth_rate: "FCF growth",
            wacc: "WACC",
            perpetual_growth: "terminal growth",
        };
        return scenarioDrafts.map((draft) => {
            if (draft.operating_forecast) {
                if (!draft.forecast_as_of || !Number.isFinite(draft.terminal_roic)) throw new Error(`Enter the forecast date and terminal ROIC for ${draft.scenario}.`);
                for (const row of draft.operating_forecast) {
                    if (!row.source.trim() || ![row.revenue, row.operating_margin, row.tax_rate, row.capex, row.depreciation, row.change_in_working_capital].every(Number.isFinite)) {
                        throw new Error(`Complete all operating inputs and the source for ${draft.scenario}, year ${row.year}.`);
                    }
                }
            }
            const parsed = {} as Record<ScenarioRateField, number>;
            for (const key of Object.keys(labels) as ScenarioRateField[]) {
                const value = Number(draft[key]);
                if (!draft[key].trim() || !Number.isFinite(value)) {
                    throw new Error(`Enter a valid ${labels[key]} percentage for ${draft.scenario}.`);
                }
                parsed[key] = value / 100;
            }
            return { scenario: draft.scenario, ...parsed, ...(draft.operating_forecast ? { operating_forecast: draft.operating_forecast, terminal_roic: draft.terminal_roic, forecast_as_of: draft.forecast_as_of } : {}) };
        });
    };

    const calculate = async (inputs?: DecisionValuationScenarioInput[]): Promise<DecisionValuationScenarioInput[] | null> => {
        let calculationInputs = inputs;
        if (!calculationInputs) {
            try {
                calculationInputs = parseScenarioDrafts();
            } catch (caught) {
                setValuationError(caught instanceof Error ? caught.message : "Enter valid scenario percentages.");
                return null;
            }
        }
        const requestTicker = ticker;
        const requestVersion = valuationRequestVersionRef.current + 1;
        valuationRequestVersionRef.current = requestVersion;
        setValuationBusy(true);
        setValuationError("");
        setSaveMessage("");
        try {
            const result = await calculateDecisionValuation(
                requestTicker,
                calculationInputs,
            );
            if (!valuationRequestIsCurrent(requestTicker, requestVersion)) {
                return null;
            }
            setWorkingValuation(result);
            return calculationInputs;
        } catch (caught) {
            if (!valuationRequestIsCurrent(requestTicker, requestVersion)) {
                return null;
            }
            setValuationError(caught instanceof Error ? caught.message : "Unable to calculate these scenarios.");
            return null;
        } finally {
            if (valuationRequestIsCurrent(requestTicker, requestVersion)) {
                setValuationBusy(false);
            }
        }
    };

    const save = async () => {
        if (!adminKey) {
            onUnlock();
            return;
        }
        const calculatedInputs = await calculate();
        if (!calculatedInputs) return;
        const requestTicker = ticker;
        const requestVersion = valuationRequestVersionRef.current;
        setValuationBusy(true);
        try {
            await savePersonalValuationScenarios(
                requestTicker,
                calculatedInputs,
                adminKey,
            );
            if (!valuationRequestIsCurrent(requestTicker, requestVersion)) return;
            setSaveMessage("Saved for this ticker.");
            await onRefresh();
        } catch (caught) {
            if (caught instanceof ApiError && caught.status === 401) onUnauthorized();
            else if (valuationRequestIsCurrent(requestTicker, requestVersion)) {
                setValuationError(caught instanceof Error ? caught.message : "Unable to save these scenarios.");
            }
        } finally {
            if (valuationRequestIsCurrent(requestTicker, requestVersion)) {
                setValuationBusy(false);
            }
        }
    };

    const reset = async () => {
        setValuationError("");
        setSaveMessage("");
        const defaults = (valuation?.default_scenarios || DEFAULT_SCENARIOS).map((item) => ({ ...item }));
        setScenarioDrafts(toScenarioDrafts(defaults));
        if (!adminKey) {
            await calculate(defaults);
            return;
        }
        const requestTicker = ticker;
        const requestVersion = valuationRequestVersionRef.current + 1;
        valuationRequestVersionRef.current = requestVersion;
        setValuationBusy(true);
        try {
            await resetPersonalValuationScenarios(requestTicker, adminKey);
            if (!valuationRequestIsCurrent(requestTicker, requestVersion)) return;
            // The delete is authoritative. Do not leave saved assumptions on
            // screen while the parent refresh is delayed or records an error.
            setWorkingValuation(null);
            try {
                const defaultValuation = await calculateDecisionValuation(
                    requestTicker,
                    defaults,
                );
                if (!valuationRequestIsCurrent(requestTicker, requestVersion)) {
                    return;
                }
                setWorkingValuation(defaultValuation);
            } catch (caught) {
                if (!valuationRequestIsCurrent(requestTicker, requestVersion)) {
                    return;
                }
                setValuationError(caught instanceof Error
                    ? `Scenarios were reset, but defaults could not be recalculated: ${caught.message}`
                    : "Scenarios were reset, but defaults could not be recalculated.");
            }
            setSaveMessage("Saved scenarios reset to defaults.");
            await onRefresh();
        } catch (caught) {
            if (caught instanceof ApiError && caught.status === 401) onUnauthorized();
            else if (valuationRequestIsCurrent(requestTicker, requestVersion)) {
                setValuationError(caught instanceof Error ? caught.message : "Unable to reset scenarios.");
            }
        } finally {
            if (valuationRequestIsCurrent(requestTicker, requestVersion)) {
                setValuationBusy(false);
            }
        }
    };

    if (hideNavigation && activeTab === "overview") {
        return <div className="space-y-4" aria-label="Decision overview">
            {loading && !decision && <div className="overview-grid" aria-label="Loading decision summary"><div className="research-panel h-60 animate-pulse bg-[var(--surface-muted)]" /><div className="research-panel h-60 animate-pulse bg-[var(--surface-muted)]" /></div>}
            {error && <div className="error-panel flex flex-wrap items-center justify-between gap-2" role="alert"><span>{error}{decision ? " The previous evidence remains visible." : ""}</span><button type="button" className="research-link" onClick={onRetry}>Retry research <RefreshCw size={14} /></button></div>}
            {decision && <>
                <DecisionOverview decision={decision} valuation={valuation || decision.valuation} working={briefIsOutOfSync} onValuation={() => setActiveTab("valuation")} onRisks={() => setActiveTab("risks")} />
                {decision.summary.coverage.missing_data_reasons.length > 0 && <aside className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm dark:border-amber-900 dark:bg-amber-950/20" aria-label="Coverage limits">
                    <p className="font-medium">Coverage limits · {decision.summary.coverage.quarterly_statements}/8 quarterly statements</p>
                    <ul className="mt-1 space-y-1 text-xs leading-5 text-[var(--text-muted)]">{decision.summary.coverage.missing_data_reasons.filter((reason) => !(valuation || decision.valuation).unavailable_reasons.includes(reason)).map((reason) => <li key={reason}>{reason}</li>)}</ul>
                </aside>}
            </>}
            {overviewContent}
            {decision && <details className="research-details">
                <summary>Research context &amp; coverage</summary>
                <div className="space-y-5 p-4 sm:p-5">
                    <dl className="grid grid-cols-2 gap-4 text-sm sm:grid-cols-4">
                        <div><dt className="text-[var(--text-muted)]">Quarterly statements</dt><dd className="mt-1 font-semibold">{decision.summary.coverage.quarterly_statements}/8</dd></div>
                        <div><dt className="text-[var(--text-muted)]">Peer metrics</dt><dd className="mt-1 font-semibold">{decision.summary.coverage.peer_metrics_available}/{decision.summary.coverage.peer_metrics_total}</dd></div>
                        <div><dt className="text-[var(--text-muted)]">Published factors</dt><dd className="mt-1 font-semibold">{decision.summary.coverage.published_factor_count}</dd></div>
                        <div><dt className="text-[var(--text-muted)]">Warnings</dt><dd className="mt-1 font-semibold">{decision.risks.high_count} high · {decision.risks.warning_count} warning</dd></div>
                    </dl>
                    <EventsExpectationsPanel data={eventsExpectations} loading={eventsExpectationsLoading} error={eventsExpectationsError} currency={decision.metadata.currency} />
                    <div className="grid gap-5 xl:grid-cols-2">
                        <section><h3 className="mb-3 text-sm font-semibold">Strongest peer positions</h3><div className="grid gap-3 sm:grid-cols-3">{decision.summary.strongest_peer_metrics.map((metric) => <SummaryMetricCard key={metric.key} metric={metric} tone="strong" />)}</div></section>
                        <section><h3 className="mb-3 text-sm font-semibold">Weakest peer positions</h3><div className="grid gap-3 sm:grid-cols-3">{decision.summary.weakest_peer_metrics.map((metric) => <SummaryMetricCard key={metric.key} metric={metric} tone="weak" />)}</div></section>
                    </div>
                    <button type="button" className="research-link" onClick={() => setActiveTab("peers")}>Explore peer benchmarks <ChevronRight size={14} /></button>
                </div>
            </details>}
        </div>;
    }

    return (
        <section className="surface-panel overflow-hidden" aria-labelledby="decision-cockpit-title">
            <header className={`border-b ${hideNavigation ? "px-4 py-3 sm:px-5" : "p-5 sm:p-6"}`}>
                <div className="flex flex-col justify-between gap-4 lg:flex-row lg:items-start">
                    <div>
                        {!hideNavigation && <p className="eyebrow">Evidence-first personal research</p>}
                        <h2 id="decision-cockpit-title" className={hideNavigation ? "text-lg font-semibold" : "mt-1 text-xl font-bold sm:text-2xl"}>{hideNavigation ? tabs.find((tab) => tab.key === activeTab)?.label : "Decision Cockpit"}</h2>
                        {!hideNavigation && <p className="mt-1.5 max-w-2xl text-sm leading-6 text-slate-500">Transparent scenarios, peer context, and deterministic fundamental checks. No aggregate score.</p>}
                    </div>
                    <button type="button" onClick={adminKey ? undefined : onUnlock} className={adminKey ? "status-pill cursor-default" : "secondary-button min-h-9 px-3 py-1.5"}>
                        {adminKey ? <><CheckCircle2 size={14} /> Personal workspace unlocked</> : <><KeyRound size={14} /> Unlock personal workspace</>}
                    </button>
                </div>
                {decision && <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 border-t pt-3 text-xs text-[var(--text-muted)]">
                    <span>Price {decision.metadata.price_date || "unavailable"}</span>
                    <span>Screener {decision.metadata.screener_date || "unavailable"}</span>
                    <span>Financials {decision.metadata.financial_statement_date || "unavailable"}</span>
                    <span>Factors {decision.metadata.factor_date || "unavailable"}</span>
                </div>}
            </header>

            {!hideNavigation && <nav className="scrollbar-hide flex overflow-x-auto border-b px-3 sm:px-5" aria-label="Decision cockpit views">
                {tabs.map((tab) => {
                    const Icon = tab.icon;
                    return <button key={tab.key} type="button" onClick={() => setActiveTab(tab.key)} aria-current={activeTab === tab.key ? "page" : undefined} className={`flex shrink-0 items-center gap-2 border-b-2 px-3 py-3.5 text-xs font-bold transition-colors ${activeTab === tab.key ? "border-emerald-500 text-emerald-700 dark:text-emerald-300" : "border-transparent text-slate-500 hover:text-slate-800 dark:hover:text-slate-200"}`}><Icon size={15} />{tab.label}</button>;
                })}
            </nav>}

            {loading && !decision ? <div className="flex min-h-[360px] flex-col items-center justify-center p-8 text-center"><LoaderCircle className="animate-spin text-emerald-500" size={28} /><p className="mt-3 text-sm font-bold">Building deterministic decision evidence…</p></div> : error && !decision ? <div className="m-5 error-panel flex min-h-[260px] flex-col items-center justify-center text-center"><AlertTriangle size={28} /><p className="mt-3 max-w-lg">{error}</p><button type="button" className="secondary-button mt-4" onClick={onRetry}><RefreshCw size={15} /> Retry cockpit</button></div> : decision && (
                <div className="p-5 sm:p-6">
                    {error && <div className="error-panel mb-5">{error} The previous cockpit evidence remains visible.</div>}

                    {activeTab === "overview" && <div className="space-y-5">
                        <div className="grid gap-4 lg:grid-cols-[1.1fr_0.9fr]">
                            <article className="surface-subtle rounded-xl border p-5">
                                <p className="eyebrow">Valuation position</p>
                                <h3 className="mt-2 text-lg font-black">{valuation?.position.text || decision.summary.valuation_position.text}</h3>
                                <div className="mt-4 grid grid-cols-3 gap-2">
                                    {(valuation?.scenarios || decision.valuation.scenarios).map((scenario) => <div key={scenario.scenario} className="rounded-lg border bg-white/70 p-3 dark:bg-slate-950/30"><p className="text-[10px] font-black uppercase tracking-wide text-slate-500">{scenario.scenario}</p><p className="mt-1 font-mono text-base font-black">{scenario.available ? formatMoney(scenario.intrinsic_value_per_share) : "—"}</p></div>)}
                                </div>
                                <p className="mt-3 text-xs text-slate-500">Current price {formatMoney(valuation?.current_price ?? decision.valuation.current_price)} · assumptions {valuation?.scenario_source || decision.valuation.scenario_source}</p>
                                {impliedGrowth?.available && <div className="mt-3 rounded-lg border bg-white/70 px-3 py-2.5 dark:bg-slate-950/30"><p className="text-[10px] font-black uppercase tracking-wide text-slate-500">Market-implied 5Y FCF growth</p><p className="mt-1 font-mono text-lg font-black text-indigo-600 dark:text-indigo-300">{(impliedGrowth.implied_fcf_growth_rate! * 100).toFixed(1)}%</p><p className="mt-1 text-[10px] text-slate-500">Base WACC {(impliedGrowth.wacc * 100).toFixed(1)}% · terminal {(impliedGrowth.perpetual_growth * 100).toFixed(1)}%</p></div>}
                            </article>
                            <article className="surface-subtle rounded-xl border p-5">
                                <p className="eyebrow">Coverage facts</p>
                                <dl className="mt-3 grid grid-cols-2 gap-3 text-sm">
                                    <div><dt className="text-xs text-slate-500">Quarterly statements</dt><dd className="mt-1 font-mono text-lg font-black">{decision.summary.coverage.quarterly_statements}/8</dd></div>
                                    <div><dt className="text-xs text-slate-500">Peer metrics</dt><dd className="mt-1 font-mono text-lg font-black">{decision.summary.coverage.peer_metrics_available}/{decision.summary.coverage.peer_metrics_total}</dd></div>
                                    <div><dt className="text-xs text-slate-500">Published factors</dt><dd className="mt-1 font-mono text-lg font-black">{decision.summary.coverage.published_factor_count}</dd></div>
                                    <div><dt className="text-xs text-slate-500">Warnings</dt><dd className="mt-1 font-mono text-lg font-black">{decision.risks.high_count} high · {decision.risks.warning_count} warning</dd></div>
                                </dl>
                            </article>
                        </div>

                        <EventsExpectationsPanel
                            data={eventsExpectations}
                            loading={eventsExpectationsLoading}
                            error={eventsExpectationsError}
                            currency={decision.metadata.currency}
                        />

                        <div className="grid gap-5 xl:grid-cols-2">
                            <section><div className="mb-3 flex items-center justify-between"><h3 className="text-sm font-black">Strongest peer positions</h3><span className="text-[10px] text-slate-500">Top three with valid coverage</span></div><div className="grid gap-3 sm:grid-cols-3">{decision.summary.strongest_peer_metrics.length ? decision.summary.strongest_peer_metrics.map((metric) => <SummaryMetricCard key={metric.key} metric={metric} tone="strong" />) : <p className="col-span-full rounded-xl border p-4 text-sm text-slate-500">No peer metric has sufficient coverage.</p>}</div></section>
                            <section><div className="mb-3 flex items-center justify-between"><h3 className="text-sm font-black">Weakest peer positions</h3><span className="text-[10px] text-slate-500">Bottom three with valid coverage</span></div><div className="grid gap-3 sm:grid-cols-3">{decision.summary.weakest_peer_metrics.length ? decision.summary.weakest_peer_metrics.map((metric) => <SummaryMetricCard key={metric.key} metric={metric} tone="weak" />) : <p className="col-span-full rounded-xl border p-4 text-sm text-slate-500">No peer metric has sufficient coverage.</p>}</div></section>
                        </div>

                        <section><div className="mb-3 flex items-center justify-between"><h3 className="text-sm font-black">Triggered fundamental warnings</h3><button type="button" className="text-xs font-bold text-emerald-700 dark:text-emerald-300" onClick={() => setActiveTab("risks")}>View all evidence</button></div><WarningResults warnings={decision.risks.warnings} unavailableCount={unavailableWarningCheckCount} limit={4} onShowEvidence={onShowEvidence} /></section>

                        {decision.summary.coverage.missing_data_reasons.length > 0 && <section className="rounded-xl border p-4"><h3 className="text-xs font-black uppercase tracking-wide text-slate-500">Coverage limits</h3><ul className="mt-2 space-y-1.5 text-sm text-slate-600 dark:text-slate-400">{decision.summary.coverage.missing_data_reasons.map((reason) => <li key={reason}>• {reason}</li>)}</ul></section>}
                    </div>}

                    {activeTab === "valuation" && valuation && <div className="space-y-6">
                        <p className="text-xs text-slate-500">Model {valuation.model_version || "legacy"} · {valuation.formula.forecast_years} forecast years · Values are conditional scenarios, not confidence intervals.</p>
                        <section className="overflow-hidden rounded-xl border">
                            <div className="surface-subtle grid gap-4 p-5 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
                                <div>
                                    <p className="eyebrow">Reverse DCF growth hurdle</p>
                                    <h3 className="mt-1 text-base font-black">Market-implied initial FCF growth</h3>
                                    {impliedGrowth?.available ? <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-500">The current price is matched when TTM free cash flow grows at this rate for the first five years, then fades toward mature growth in years 6–10, holding the Base discount and terminal assumptions fixed. This is a market hurdle, not a forecast, and inherits the scenario DCF&apos;s FCF, currency, and share-unit limits.</p> : <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-500">The growth hurdle cannot be reverse-solved from the current DCF inputs.</p>}
                                </div>
                                <div className="min-w-[220px] rounded-xl border bg-white/80 p-4 dark:bg-slate-950/40">
                                    <p className="font-mono text-3xl font-black text-indigo-600 dark:text-indigo-300">{impliedGrowth?.available ? `${(impliedGrowth.implied_fcf_growth_rate! * 100).toFixed(1)}%` : "—"}</p>
                                    <p className="mt-1 text-xs font-bold text-slate-500">Initial 5-year implied FCF CAGR</p>
                                </div>
                            </div>
                            {impliedGrowth?.available ? <dl className="grid gap-px border-t bg-slate-200 text-xs dark:bg-slate-800 sm:grid-cols-3">
                                <div className="bg-white p-3.5 dark:bg-slate-950/60"><dt className="text-slate-500">Base WACC</dt><dd className="mt-1 font-mono font-black">{(impliedGrowth.wacc * 100).toFixed(1)}%</dd></div>
                                <div className="bg-white p-3.5 dark:bg-slate-950/60"><dt className="text-slate-500">Terminal growth</dt><dd className="mt-1 font-mono font-black">{(impliedGrowth.perpetual_growth * 100).toFixed(1)}%</dd></div>
                                <div className="bg-white p-3.5 dark:bg-slate-950/60"><dt className="text-slate-500">Gap vs Base growth</dt><dd className={`mt-1 font-mono font-black ${(impliedGrowth.growth_gap_to_base ?? 0) > 0 ? "text-rose-500" : (impliedGrowth.growth_gap_to_base ?? 0) < 0 ? "text-emerald-600" : ""}`}>{(impliedGrowth.growth_gap_to_base ?? 0) > 0 ? "+" : ""}{((impliedGrowth.growth_gap_to_base ?? 0) * 100).toFixed(1)} pp</dd></div>
                            </dl> : <ul className="space-y-1 border-t p-4 text-xs text-amber-700 dark:text-amber-300">{impliedGrowth?.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>}
                        </section>
                        <section className="rounded-xl border p-4">
                            <div className="flex flex-wrap items-start justify-between gap-4">
                                <div>
                                    <p className="eyebrow">Shared valuation assumptions</p>
                                    <h3 className="mt-1 text-base font-black">One company WACC, one mature growth rate</h3>
                                    <p className="mt-1 max-w-2xl text-xs leading-5 text-slate-500">All three operating cases use the same discount and terminal assumptions. The default WACC is estimated from CAPM, book debt and the tax shield; edit it here only when you have a documented alternative.</p>
                                </div>
                                <div className="grid w-full gap-3 sm:w-auto sm:grid-cols-2">
                                    <label className="text-[10px] font-bold uppercase tracking-wide text-slate-500">Shared WACC<div className="relative mt-1"><input type="number" min={3} max={25} step="0.1" value={scenarioDrafts[1]?.wacc ?? ""} disabled={valuationBusy} onChange={(event) => editSharedRate("wacc", event.target.value)} className="control-field py-2 pr-6 font-mono text-xs disabled:cursor-not-allowed disabled:opacity-60" aria-label="Shared WACC" /><span className="absolute right-2 top-1/2 -translate-y-1/2 text-xs">%</span></div></label>
                                    <label className="text-[10px] font-bold uppercase tracking-wide text-slate-500">Terminal growth<div className="relative mt-1"><input type="number" min={-2} max={6} step="0.1" value={scenarioDrafts[1]?.perpetual_growth ?? ""} disabled={valuationBusy} onChange={(event) => editSharedRate("perpetual_growth", event.target.value)} className="control-field py-2 pr-6 font-mono text-xs disabled:cursor-not-allowed disabled:opacity-60" aria-label="Shared terminal growth" /><span className="absolute right-2 top-1/2 -translate-y-1/2 text-xs">%</span></div></label>
                                </div>
                            </div>
                            {valuation.assumption_basis?.wacc && <dl className="mt-4 grid gap-px overflow-hidden rounded-lg bg-slate-200 text-xs dark:bg-slate-800 sm:grid-cols-4">
                                <div className="bg-white p-3 dark:bg-slate-950/60"><dt className="text-slate-500">Risk-free</dt><dd className="mt-1 font-mono font-black">{(valuation.assumption_basis.wacc.risk_free_rate * 100).toFixed(2)}%</dd></div>
                                <div className="bg-white p-3 dark:bg-slate-950/60"><dt className="text-slate-500">Long-run Beta</dt><dd className="mt-1 font-mono font-black">{valuation.assumption_basis.wacc.beta.toFixed(2)}</dd></div>
                                <div className="bg-white p-3 dark:bg-slate-950/60"><dt className="text-slate-500">Cost of equity</dt><dd className="mt-1 font-mono font-black">{(valuation.assumption_basis.wacc.cost_of_equity * 100).toFixed(2)}%</dd></div>
                                <div className="bg-white p-3 dark:bg-slate-950/60"><dt className="text-slate-500">Cost of debt</dt><dd className="mt-1 font-mono font-black">{valuation.assumption_basis.wacc.cost_of_debt == null ? "—" : `${(valuation.assumption_basis.wacc.cost_of_debt * 100).toFixed(2)}%`}</dd></div>
                            </dl>}
                            {valuation.assumption_basis?.wacc.notes.length ? <ul className="mt-3 space-y-1 text-xs text-amber-700 dark:text-amber-300">{valuation.assumption_basis.wacc.notes.map((note) => <li key={note}>{note}</li>)}</ul> : null}
                        </section>
                        <OperatingForecastEditor scenarios={scenarioDrafts} disabled={valuationBusy} onChange={next => { setScenarioDrafts(next); setSaveMessage(""); }} />
                        {valuation.assumption_basis?.growth.notes?.length ? <ul className="space-y-1 text-xs text-slate-500">{valuation.assumption_basis.growth.notes.map(note => <li key={note}>{note}</li>)}</ul> : null}
                        <details className="rounded-xl border p-4"><summary className="cursor-pointer text-sm font-bold">Cash flow, debt and share sources</summary>
                            <dl className="mt-3 grid gap-2 text-xs sm:grid-cols-2"><div><dt>Share basis</dt><dd>{valuation.inputs.shares_basis?.basis.replaceAll("_", " ") || "Provider statement basis unverified"} · {valuation.inputs.shares_basis?.as_of || "Date unavailable"}</dd></div><div><dt>Debt scope</dt><dd>{valuation.inputs.input_lineage?.debt?.scope.replaceAll("_", " ") || "Unavailable"}</dd></div><div><dt>Reported fiscal end</dt><dd>{valuation.inputs.reported_period_end || "Not supplied; period label shown"}</dd></div><div><dt>Filing date</dt><dd>{valuation.inputs.filing_date || "Unavailable"}</dd></div></dl>
                            {valuation.inputs.equity_bridge && <p className="mt-3 text-xs">Cash bridge: {valuation.inputs.equity_bridge.cash_basis.replaceAll("_", " ")}. Other sourced equity adjustments: {valuation.inputs.equity_bridge.equity_adjustment.toLocaleString()}. {valuation.inputs.equity_bridge.complete ? "All bridge components sourced." : "Some bridge components remain unverified."}</p>}
                            <ul className="mt-3 space-y-1 text-xs text-amber-700 dark:text-amber-300">{valuation.inputs.input_lineage?.notes?.map(note => <li key={note}>{note}</li>)}</ul>
                        </details>
                        <div className="grid gap-4 lg:grid-cols-3">
                            {scenarioDrafts.map((scenario, index) => {
                                const result = valuation.scenarios.find((item) => item.scenario === scenario.scenario);
                                return <article key={scenario.scenario} className="rounded-xl border p-4"><div className="flex items-center justify-between"><h3 className="text-sm font-black capitalize">{scenario.scenario}</h3><span className="font-mono text-lg font-black">{result?.available ? formatMoney(result.intrinsic_value_per_share) : "Unavailable"}</span></div><label className="mt-4 block text-[10px] font-bold uppercase tracking-wide text-slate-500">Initial 5Y FCF growth<div className="relative mt-1"><input type="number" min={-20} max={50} step="0.1" value={scenario.fcf_growth_rate} disabled={valuationBusy || !!scenario.operating_forecast} onChange={(event) => editScenario(index, "fcf_growth_rate", event.target.value)} className="control-field py-2 pr-6 font-mono text-xs disabled:cursor-not-allowed disabled:opacity-60" aria-label={`${scenario.scenario} FCF growth`} /><span className="absolute right-2 top-1/2 -translate-y-1/2 text-xs">%</span></div></label>{result?.available ? <p className={`mt-3 text-xs font-bold ${(result.upside_downside ?? 0) >= 0 ? "text-emerald-600" : "text-rose-500"}`}>{result.upside_downside == null ? "Current-price comparison unavailable" : `${result.upside_downside >= 0 ? "+" : ""}${(result.upside_downside * 100).toFixed(1)}% vs current price`}</p> : <ul className="mt-3 text-xs text-rose-500">{result?.reasons?.map((reason) => <li key={reason}>{reason}</li>)}</ul>}</article>;
                            })}
                        </div>
                        <div className="flex flex-wrap items-center gap-2"><button type="button" className="secondary-button" disabled={valuationBusy} onClick={() => void calculate()}><Calculator size={15} /> Calculate</button><button type="button" className="primary-button" disabled={valuationBusy} onClick={() => void save()}><Save size={15} /> {adminKey ? "Save scenarios" : "Unlock to save"}</button><button type="button" className="secondary-button" disabled={valuationBusy} onClick={() => void reset()}><RotateCcw size={15} /> Reset defaults</button>{valuationBusy && <LoaderCircle className="animate-spin text-emerald-500" size={18} />}{saveMessage && <span className="text-xs font-bold text-emerald-600">{saveMessage}</span>}</div>
                        {valuationError && <div className="error-panel" role="alert">{valuationError}</div>}
                        <section className="overflow-hidden rounded-xl border"><header className="surface-subtle border-b p-4"><h3 className="text-sm font-black">Base-case WACC / terminal sensitivity</h3><p className="mt-1 text-xs text-slate-500">Intrinsic value per share · Base FCF growth {(valuation.sensitivity.fcf_growth_rate * 100).toFixed(1)}% · WACC ±1/2 points · terminal growth ±0.5/1 point</p></header><div className="overflow-x-auto p-3"><table className="w-full min-w-[620px] border-separate border-spacing-1 text-right font-mono text-xs"><thead><tr><th className="p-2 text-left text-slate-500">Terminal ↓ / WACC →</th>{valuation.sensitivity.wacc_values.map((wacc) => <th key={wacc} className="p-2 text-slate-500">{(wacc * 100).toFixed(1)}%</th>)}</tr></thead><tbody>{valuation.sensitivity.terminal_growth_values.map((terminal, rowIndex) => <tr key={`${terminal}-${rowIndex}`}><th className="p-2 text-left text-slate-500">{(terminal * 100).toFixed(1)}%</th>{valuation.sensitivity.values[rowIndex].map((value, columnIndex) => <td key={columnIndex} title={valuation.sensitivity.cell_reasons[rowIndex][columnIndex] || undefined} className={`rounded-lg border p-2.5 font-bold ${rowIndex === 2 && columnIndex === 2 ? "border-emerald-400 bg-emerald-50 text-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-300" : "bg-slate-50 dark:bg-slate-900/50"}`}>{value == null ? "—" : formatMoney(value)}</td>)}</tr>)}</tbody></table></div></section>
                        <div className="rounded-xl border p-4 text-xs text-slate-500">Base terminal value contribution: {valuation.scenarios[1]?.terminal_share_of_enterprise_value == null ? "—" : `${(valuation.scenarios[1].terminal_share_of_enterprise_value! * 100).toFixed(1)}% of enterprise value`}. Historical continuation assumes sustainable reinvestment within FCFF; an operating forecast explicitly links mature reinvestment to growth and terminal ROIC.</div>
                        <p className="rounded-xl border p-4 text-xs leading-5 text-slate-500">Ten-year FCFF scenario: five initial growth years followed by five years of linear convergence to mature growth. A sourced operating forecast replaces this path when supplied. CFO less capex is adjusted using reported after-tax interest when available. Reported cash and debt retain their source qualifications. The continuation terminal value uses <span className="font-mono">FCFF₁₀ × (1 + g) / (WACC − g)</span>. Inputs: reported FCF {compact(valuation.inputs.reported_fcf)}, after-tax interest {compact(valuation.inputs.after_tax_interest_adjustment)}, FCFF {compact(valuation.inputs.fcf)}, cash {compact(valuation.inputs.cash)}, debt {compact(valuation.inputs.debt)}, shares {compact(valuation.inputs.shares)}.</p>
                    </div>}

                    {activeTab === "valuation" && !valuation && <div className="space-y-3">
                        {saveMessage && <p className="text-xs font-bold text-emerald-600">{saveMessage}</p>}
                        <div className={valuationError ? "error-panel" : "rounded-xl border p-5 text-sm text-slate-500"} role={valuationError ? "alert" : "status"}>
                            {valuationError || "Valuation is being refreshed with the default scenarios."}
                        </div>
                    </div>}

                    {activeTab === "peers" && <div className="space-y-5">
                        <PeerMultipleDistribution key={ticker} ticker={ticker} />
                        <details className="overflow-hidden rounded-xl border">
                            <summary className="surface-subtle cursor-pointer list-none px-4 py-3 text-sm font-black marker:hidden">
                                View all peer metrics
                                <span className="ml-2 text-xs font-normal text-slate-500">{selectedPeerAvailable}/{decision.peer_comparison.total_metric_count} currently meet coverage</span>
                            </summary>
                            <div className="space-y-4 border-t p-4">
                                <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-center"><div><h3 className="text-sm font-black">Published cross-sectional benchmarks</h3><p className="mt-1 text-xs text-slate-500">Midrank percentiles; invalid valuation multiples and negative debt/equity are excluded.</p></div><div className="flex rounded-lg border bg-slate-100 p-1 dark:bg-slate-900">{(["industry", "sector"] as const).map((scope) => <button key={scope} type="button" onClick={() => setPeerScope(scope)} className={`rounded-md px-3 py-1.5 text-xs font-bold capitalize ${peerScope === scope ? "bg-white text-emerald-700 shadow-sm dark:bg-slate-700 dark:text-emerald-300" : "text-slate-500"}`}>{scope}</button>)}</div></div>
                                <p className="text-xs text-slate-500">{peerScope === "industry" ? decision.peer_comparison.industry || "Unknown industry" : decision.peer_comparison.sector || "Unknown sector"} · {selectedPeerAvailable}/{decision.peer_comparison.total_metric_count} metrics meet the {peerScope === "industry" ? "10" : "20"}-observation threshold.</p>
                                <div className="overflow-x-auto rounded-xl border"><table className="w-full min-w-[760px] text-left text-xs"><thead className="surface-subtle"><tr><th className="px-4 py-3">Metric</th><th className="px-4 py-3">Value</th><th className="px-4 py-3">Direction</th><th className="px-4 py-3">Raw percentile</th><th className="px-4 py-3">Better-positioned</th><th className="px-4 py-3">Coverage</th></tr></thead><tbody className="divide-y">{decision.peer_comparison.metrics.map((metric) => { const scope = metric[peerScope]; const tone = betterPositionedTone(scope.desirability_percentile, scope.available); return <tr key={metric.key}><td className="px-4 py-3"><span className="font-bold">{metric.label}</span><span className="ml-2 font-mono text-[10px] text-slate-400">{metric.evidence_id}</span></td><td className="px-4 py-3 font-mono font-bold">{peerValue(metric)}</td><td className="px-4 py-3 text-slate-500">{metric.direction.replace("_", " ")}</td><td className="px-4 py-3 font-mono text-slate-500">{scope.raw_percentile == null ? "—" : `${scope.raw_percentile.toFixed(1)}th`}</td><td className="px-4 py-3 font-mono font-bold"><span className={`inline-flex min-w-[4.5rem] items-center justify-center rounded-full border px-2 py-1 text-[11px] ${tone.className}`} title={`${tone.label} Better-positioned percentile`}>{scope.desirability_percentile == null ? "—" : `${scope.desirability_percentile.toFixed(1)}th`}</span></td><td className="px-4 py-3"><span className={scope.available ? "text-emerald-600" : "text-slate-500"}>{scope.observation_count} valid</span>{!scope.available && <p className="mt-1 max-w-xs text-[10px] leading-4 text-slate-500">{scope.reason}</p>}</td></tr>; })}</tbody></table></div>
                                <div className="flex flex-wrap items-center gap-2 text-[10px] text-slate-500" role="group" aria-label="Better-positioned legend"><span className="mr-1 font-bold">Better-positioned:</span>{betterPositionedLegend.map((item) => <span key={item.label} className={`rounded-full border px-2 py-1 font-semibold ${item.className}`}>{item.label}</span>)}<span className="ml-1">Higher is better after direction adjustment.</span></div>
                            </div>
                        </details>
                    </div>}

                    {activeTab === "risks" && <div className="space-y-5">
                        <div><h3 className="text-sm font-black">Triggered fundamental rules</h3><p className="mt-1 text-xs text-slate-500">Only deterministic financial-statement rules are included in Phase 1.</p></div>
                        <WarningResults warnings={decision.risks.warnings} unavailableCount={unavailableWarningCheckCount} onShowEvidence={onShowEvidence} />
                        <EarningsQualityPanel
                            data={earningsQuality || decision.earnings_quality || null}
                            loading={earningsQualityLoading}
                            error={earningsQualityError}
                            adminKey={adminKey}
                            busyPeriod={earningsQualityBusyPeriod}
                            onUnlock={onUnlock}
                            onAnalyze={onAnalyzeEarningsPeriod}
                        />
                        <section className="rounded-xl border p-4"><h3 className="flex items-center gap-2 text-xs font-black uppercase tracking-wide text-slate-500"><Database size={14} /> Data-quality notes</h3>{decision.risks.data_quality_notes.length ? <ul className="mt-3 space-y-2 text-sm text-slate-600 dark:text-slate-400">{decision.risks.data_quality_notes.map((note) => <li key={`${note.code}-${note.message}`} className="rounded-lg bg-slate-50 p-3 dark:bg-slate-900/50"><span className="font-mono text-[10px] text-slate-400">{note.code}</span><p className="mt-1">{note.message}</p></li>)}</ul> : <p className="mt-2 text-sm text-slate-500">No data-quality limitation was recorded for these checks.</p>}</section>
                    </div>}

                    {activeTab === "brief" && <div className="space-y-5"><EventsExpectationsPanel data={eventsExpectations} loading={eventsExpectationsLoading} error={eventsExpectationsError} currency={decision.metadata.currency} detail /><div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_280px]"><AIReport ticker={ticker} evidenceKey={briefEvidenceKey} adminKey={adminKey} onUnauthorized={onUnauthorized} embedded disabledReason={briefIsOutOfSync ? "The displayed valuation uses working assumptions that are not in the server evidence yet. Save the scenarios, or reset them to the server-backed values, before generating a brief." : undefined} /><aside className="rounded-xl border p-4"><h3 className="text-xs font-black uppercase tracking-wide text-slate-500">Evidence registry</h3><p className="mt-2 text-xs leading-5 text-slate-500">The generator receives these stable records only. Unknown citations are rejected before display or caching.</p><div className="custom-scrollbar mt-3 max-h-[420px] space-y-2 overflow-y-auto">{decision.evidence.map((item) => <div key={item.id} className="flex items-start gap-2 rounded-lg bg-slate-50 p-2.5 text-xs dark:bg-slate-900/50"><span className={`font-mono font-black ${item.available ? "text-emerald-600" : "text-slate-400"}`}>{item.id}</span><div><p className="font-bold">{item.label}</p><p className="mt-0.5 text-[10px] text-slate-500">{item.available ? item.source_date || "current evidence" : "unavailable"}</p></div></div>)}</div></aside></div></div>}
                </div>
            )}
        </section>
    );
}
