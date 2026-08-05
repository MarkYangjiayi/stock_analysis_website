"use client";

import { useEffect, useMemo, useState } from "react";
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
import {
    ApiError,
    calculateDecisionValuation,
    DecisionSummaryMetric,
    DecisionSupportResponse,
    DecisionValuation,
    DecisionValuationScenarioInput,
    DecisionWarning,
    PeerMetric,
    resetPersonalValuationScenarios,
    savePersonalValuationScenarios,
} from "@/lib/api";

type CockpitTab = "overview" | "valuation" | "peers" | "risks" | "brief";

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
}

const DEFAULT_SCENARIOS: DecisionValuationScenarioInput[] = [
    { scenario: "bear", fcf_growth_rate: 0.05, wacc: 0.105, perpetual_growth: 0.02 },
    { scenario: "base", fcf_growth_rate: 0.10, wacc: 0.09, perpetual_growth: 0.025 },
    { scenario: "bull", fcf_growth_rate: 0.15, wacc: 0.08, perpetual_growth: 0.03 },
];

type ScenarioRateField = "fcf_growth_rate" | "wacc" | "perpetual_growth";
type ScenarioDraft = {
    scenario: DecisionValuationScenarioInput["scenario"];
    fcf_growth_rate: string;
    wacc: string;
    perpetual_growth: string;
};

const toScenarioDrafts = (inputs: DecisionValuationScenarioInput[]): ScenarioDraft[] => inputs.map((item) => ({
    scenario: item.scenario,
    fcf_growth_rate: (item.fcf_growth_rate * 100).toString(),
    wacc: (item.wacc * 100).toString(),
    perpetual_growth: (item.perpetual_growth * 100).toString(),
}));

const tabs: Array<{ key: CockpitTab; label: string; icon: typeof Calculator }> = [
    { key: "overview", label: "Overview", icon: BarChart3 },
    { key: "valuation", label: "Valuation", icon: Calculator },
    { key: "peers", label: "Peer Benchmarks", icon: Database },
    { key: "risks", label: "Risks", icon: FileWarning },
    { key: "brief", label: "Evidence Brief", icon: Bot },
];

const money = (value?: number | null) => value == null || !Number.isFinite(value)
    ? "—"
    : new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(value);

const compact = (value?: number | null) => value == null || !Number.isFinite(value)
    ? "—"
    : new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 2 }).format(value);

const peerValue = (metric: Pick<PeerMetric, "value" | "format"> | DecisionSummaryMetric) => {
    if (metric.value == null) return "—";
    if (metric.format === "percent") return `${(metric.value * 100).toFixed(1)}%`;
    if (metric.format === "multiple") return `${metric.value.toFixed(1)}×`;
    return metric.value.toFixed(2);
};

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
}: DecisionCockpitProps) {
    const [activeTab, setActiveTab] = useState<CockpitTab>("overview");
    const [peerScope, setPeerScope] = useState<"industry" | "sector">("industry");
    const [scenarioDrafts, setScenarioDrafts] = useState<ScenarioDraft[]>(() => toScenarioDrafts(DEFAULT_SCENARIOS));
    const [workingValuation, setWorkingValuation] = useState<DecisionValuation | null>(() => decision?.valuation ?? null);
    const [valuationBusy, setValuationBusy] = useState(false);
    const [valuationError, setValuationError] = useState("");
    const [saveMessage, setSaveMessage] = useState("");

    useEffect(() => {
        setActiveTab("overview");
        setValuationError("");
        setSaveMessage("");
    }, [ticker]);

    useEffect(() => {
        if (!decision) return;
        setWorkingValuation(decision.valuation);
        setScenarioDrafts(toScenarioDrafts(decision.valuation.scenarios.map((item) => ({ ...item.assumptions }))));
    }, [decision]);

    const valuation = workingValuation;
    const selectedPeerAvailable = useMemo(
        () => decision?.peer_comparison.metrics.filter((metric) => metric[peerScope].available).length ?? 0,
        [decision, peerScope],
    );
    const briefEvidenceKey = useMemo(() => JSON.stringify({
        metadata: decision?.metadata ?? null,
        valuationAssumptions: decision?.valuation.scenarios.map((item) => item.assumptions) ?? [],
        evidence: decision?.evidence ?? [],
    }), [decision]);

    const editScenario = (index: number, key: ScenarioRateField, percentValue: string) => {
        setScenarioDrafts((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, [key]: percentValue } : item));
        setSaveMessage("");
    };

    const parseScenarioDrafts = () => {
        const labels: Record<ScenarioRateField, string> = {
            fcf_growth_rate: "FCF growth",
            wacc: "WACC",
            perpetual_growth: "terminal growth",
        };
        return scenarioDrafts.map((draft) => {
            const parsed = {} as Record<ScenarioRateField, number>;
            for (const key of Object.keys(labels) as ScenarioRateField[]) {
                const value = Number(draft[key]);
                if (!draft[key].trim() || !Number.isFinite(value)) {
                    throw new Error(`Enter a valid ${labels[key]} percentage for ${draft.scenario}.`);
                }
                parsed[key] = value / 100;
            }
            return { scenario: draft.scenario, ...parsed };
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
        setValuationBusy(true);
        setValuationError("");
        setSaveMessage("");
        try {
            const result = await calculateDecisionValuation(ticker, calculationInputs);
            setWorkingValuation(result);
            return calculationInputs;
        } catch (caught) {
            setValuationError(caught instanceof Error ? caught.message : "Unable to calculate these scenarios.");
            return null;
        } finally {
            setValuationBusy(false);
        }
    };

    const save = async () => {
        if (!adminKey) {
            onUnlock();
            return;
        }
        const calculatedInputs = await calculate();
        if (!calculatedInputs) return;
        setValuationBusy(true);
        try {
            await savePersonalValuationScenarios(ticker, calculatedInputs, adminKey);
            setSaveMessage("Saved for this ticker.");
            await onRefresh();
        } catch (caught) {
            if (caught instanceof ApiError && caught.status === 401) onUnauthorized();
            else setValuationError(caught instanceof Error ? caught.message : "Unable to save these scenarios.");
        } finally {
            setValuationBusy(false);
        }
    };

    const reset = async () => {
        setValuationError("");
        setSaveMessage("");
        const defaults = DEFAULT_SCENARIOS.map((item) => ({ ...item }));
        setScenarioDrafts(toScenarioDrafts(defaults));
        if (!adminKey) {
            await calculate(defaults);
            return;
        }
        setValuationBusy(true);
        try {
            await resetPersonalValuationScenarios(ticker, adminKey);
            // The delete is authoritative. Do not leave saved assumptions on
            // screen while the parent refresh is delayed or records an error.
            setWorkingValuation(null);
            try {
                const defaultValuation = await calculateDecisionValuation(ticker, defaults);
                setWorkingValuation(defaultValuation);
            } catch (caught) {
                setValuationError(caught instanceof Error
                    ? `Scenarios were reset, but defaults could not be recalculated: ${caught.message}`
                    : "Scenarios were reset, but defaults could not be recalculated.");
            }
            setSaveMessage("Saved scenarios reset to defaults.");
            await onRefresh();
        } catch (caught) {
            if (caught instanceof ApiError && caught.status === 401) onUnauthorized();
            else setValuationError(caught instanceof Error ? caught.message : "Unable to reset scenarios.");
        } finally {
            setValuationBusy(false);
        }
    };

    return (
        <section className="surface-panel overflow-hidden" aria-labelledby="decision-cockpit-title">
            <header className="border-b p-5 sm:p-6">
                <div className="flex flex-col justify-between gap-4 lg:flex-row lg:items-start">
                    <div>
                        <p className="eyebrow">Evidence-first personal research</p>
                        <h2 id="decision-cockpit-title" className="mt-1 text-xl font-black sm:text-2xl">Decision Cockpit</h2>
                        <p className="mt-1.5 max-w-2xl text-sm leading-6 text-slate-500">Transparent scenarios, peer context, and deterministic fundamental checks. No aggregate score.</p>
                    </div>
                    <button type="button" onClick={adminKey ? undefined : onUnlock} className={adminKey ? "status-pill cursor-default" : "secondary-button min-h-9 px-3 py-1.5"}>
                        {adminKey ? <><CheckCircle2 size={14} /> Personal workspace unlocked</> : <><KeyRound size={14} /> Unlock personal workspace</>}
                    </button>
                </div>
                {decision && <div className="mt-4 flex flex-wrap gap-x-5 gap-y-1 border-t pt-3 font-mono text-[10px] text-slate-500">
                    <span>Price {decision.metadata.price_date || "unavailable"}</span>
                    <span>Screener {decision.metadata.screener_date || "unavailable"}</span>
                    <span>Financials {decision.metadata.financial_statement_date || "unavailable"}</span>
                    <span>Factors {decision.metadata.factor_date || "unavailable"}</span>
                </div>}
            </header>

            <nav className="scrollbar-hide flex overflow-x-auto border-b px-3 sm:px-5" aria-label="Decision cockpit views">
                {tabs.map((tab) => {
                    const Icon = tab.icon;
                    return <button key={tab.key} type="button" onClick={() => setActiveTab(tab.key)} aria-current={activeTab === tab.key ? "page" : undefined} className={`flex shrink-0 items-center gap-2 border-b-2 px-3 py-3.5 text-xs font-bold transition-colors ${activeTab === tab.key ? "border-emerald-500 text-emerald-700 dark:text-emerald-300" : "border-transparent text-slate-500 hover:text-slate-800 dark:hover:text-slate-200"}`}><Icon size={15} />{tab.label}</button>;
                })}
            </nav>

            {loading && !decision ? <div className="flex min-h-[360px] flex-col items-center justify-center p-8 text-center"><LoaderCircle className="animate-spin text-emerald-500" size={28} /><p className="mt-3 text-sm font-bold">Building deterministic decision evidence…</p></div> : error && !decision ? <div className="m-5 error-panel flex min-h-[260px] flex-col items-center justify-center text-center"><AlertTriangle size={28} /><p className="mt-3 max-w-lg">{error}</p><button type="button" className="secondary-button mt-4" onClick={onRetry}><RefreshCw size={15} /> Retry cockpit</button></div> : decision && (
                <div className="p-5 sm:p-6">
                    {error && <div className="error-panel mb-5">{error} The previous cockpit evidence remains visible.</div>}

                    {activeTab === "overview" && <div className="space-y-5">
                        <div className="grid gap-4 lg:grid-cols-[1.1fr_0.9fr]">
                            <article className="surface-subtle rounded-xl border p-5">
                                <p className="eyebrow">Valuation position</p>
                                <h3 className="mt-2 text-lg font-black">{valuation?.position.text || decision.summary.valuation_position.text}</h3>
                                <div className="mt-4 grid grid-cols-3 gap-2">
                                    {(valuation?.scenarios || decision.valuation.scenarios).map((scenario) => <div key={scenario.scenario} className="rounded-lg border bg-white/70 p-3 dark:bg-slate-950/30"><p className="text-[10px] font-black uppercase tracking-wide text-slate-500">{scenario.scenario}</p><p className="mt-1 font-mono text-base font-black">{scenario.available ? money(scenario.intrinsic_value_per_share) : "—"}</p></div>)}
                                </div>
                                <p className="mt-3 text-xs text-slate-500">Current price {money(valuation?.current_price ?? decision.valuation.current_price)} · assumptions {valuation?.scenario_source || decision.valuation.scenario_source}</p>
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

                        <div className="grid gap-5 xl:grid-cols-2">
                            <section><div className="mb-3 flex items-center justify-between"><h3 className="text-sm font-black">Strongest peer positions</h3><span className="text-[10px] text-slate-500">Top three with valid coverage</span></div><div className="grid gap-3 sm:grid-cols-3">{decision.summary.strongest_peer_metrics.length ? decision.summary.strongest_peer_metrics.map((metric) => <SummaryMetricCard key={metric.key} metric={metric} tone="strong" />) : <p className="col-span-full rounded-xl border p-4 text-sm text-slate-500">No peer metric has sufficient coverage.</p>}</div></section>
                            <section><div className="mb-3 flex items-center justify-between"><h3 className="text-sm font-black">Weakest peer positions</h3><span className="text-[10px] text-slate-500">Bottom three with valid coverage</span></div><div className="grid gap-3 sm:grid-cols-3">{decision.summary.weakest_peer_metrics.length ? decision.summary.weakest_peer_metrics.map((metric) => <SummaryMetricCard key={metric.key} metric={metric} tone="weak" />) : <p className="col-span-full rounded-xl border p-4 text-sm text-slate-500">No peer metric has sufficient coverage.</p>}</div></section>
                        </div>

                        <section><div className="mb-3 flex items-center justify-between"><h3 className="text-sm font-black">Triggered fundamental warnings</h3><button type="button" className="text-xs font-bold text-emerald-700 dark:text-emerald-300" onClick={() => setActiveTab("risks")}>View all evidence</button></div>{decision.risks.warnings.length ? <div className="grid gap-3 lg:grid-cols-2">{decision.risks.warnings.slice(0, 4).map((warning) => <RiskCard key={warning.id} warning={warning} onShowEvidence={() => onShowEvidence(warning.evidence_metric)} />)}</div> : <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/20 dark:text-emerald-300"><CheckCircle2 className="mr-2 inline" size={17} />No fundamental warning rule is triggered by available history.</div>}</section>

                        {decision.summary.coverage.missing_data_reasons.length > 0 && <section className="rounded-xl border p-4"><h3 className="text-xs font-black uppercase tracking-wide text-slate-500">Coverage limits</h3><ul className="mt-2 space-y-1.5 text-sm text-slate-600 dark:text-slate-400">{decision.summary.coverage.missing_data_reasons.map((reason) => <li key={reason}>• {reason}</li>)}</ul></section>}
                    </div>}

                    {activeTab === "valuation" && valuation && <div className="space-y-6">
                        <div className="grid gap-4 lg:grid-cols-3">
                            {scenarioDrafts.map((scenario, index) => {
                                const result = valuation.scenarios.find((item) => item.scenario === scenario.scenario);
                                return <article key={scenario.scenario} className="rounded-xl border p-4"><div className="flex items-center justify-between"><h3 className="text-sm font-black capitalize">{scenario.scenario}</h3><span className="font-mono text-lg font-black">{result?.available ? money(result.intrinsic_value_per_share) : "Unavailable"}</span></div><div className="mt-4 grid grid-cols-3 gap-2">{([['fcf_growth_rate', 'FCF growth', -20, 50], ['wacc', 'WACC', 3, 25], ['perpetual_growth', 'Terminal', -2, 6]] as const).map(([key, label, min, max]) => <label key={key} className="text-[10px] font-bold uppercase tracking-wide text-slate-500">{label}<div className="relative mt-1"><input type="number" min={min} max={max} step="0.1" value={scenario[key]} onChange={(event) => editScenario(index, key, event.target.value)} className="control-field py-2 pr-6 font-mono text-xs" aria-label={`${scenario.scenario} ${label}`} /><span className="absolute right-2 top-1/2 -translate-y-1/2 text-xs">%</span></div></label>)}</div>{result?.available ? <p className={`mt-3 text-xs font-bold ${(result.upside_downside ?? 0) >= 0 ? "text-emerald-600" : "text-rose-500"}`}>{result.upside_downside == null ? "Current-price comparison unavailable" : `${result.upside_downside >= 0 ? "+" : ""}${(result.upside_downside * 100).toFixed(1)}% vs current price`}</p> : <ul className="mt-3 text-xs text-rose-500">{result?.reasons?.map((reason) => <li key={reason}>{reason}</li>)}</ul>}</article>;
                            })}
                        </div>
                        <div className="flex flex-wrap items-center gap-2"><button type="button" className="secondary-button" disabled={valuationBusy} onClick={() => void calculate()}><Calculator size={15} /> Calculate</button><button type="button" className="primary-button" disabled={valuationBusy} onClick={() => void save()}><Save size={15} /> {adminKey ? "Save scenarios" : "Unlock to save"}</button><button type="button" className="secondary-button" disabled={valuationBusy} onClick={() => void reset()}><RotateCcw size={15} /> Reset defaults</button>{valuationBusy && <LoaderCircle className="animate-spin text-emerald-500" size={18} />}{saveMessage && <span className="text-xs font-bold text-emerald-600">{saveMessage}</span>}</div>
                        {valuationError && <div className="error-panel" role="alert">{valuationError}</div>}
                        <section className="overflow-hidden rounded-xl border"><header className="surface-subtle border-b p-4"><h3 className="text-sm font-black">Base-case sensitivity</h3><p className="mt-1 text-xs text-slate-500">Intrinsic value per share · growth ±5/10 points · WACC ±1/2 points · terminal growth {(valuation.sensitivity.terminal_growth * 100).toFixed(1)}%</p></header><div className="overflow-x-auto p-3"><table className="w-full min-w-[620px] border-separate border-spacing-1 text-right font-mono text-xs"><thead><tr><th className="p-2 text-left text-slate-500">Growth ↓ / WACC →</th>{valuation.sensitivity.wacc_values.map((wacc) => <th key={wacc} className="p-2 text-slate-500">{(wacc * 100).toFixed(1)}%</th>)}</tr></thead><tbody>{valuation.sensitivity.growth_values.map((growth, rowIndex) => <tr key={`${growth}-${rowIndex}`}><th className="p-2 text-left text-slate-500">{(growth * 100).toFixed(1)}%</th>{valuation.sensitivity.values[rowIndex].map((value, columnIndex) => <td key={columnIndex} title={valuation.sensitivity.cell_reasons[rowIndex][columnIndex] || undefined} className={`rounded-lg border p-2.5 font-bold ${rowIndex === 2 && columnIndex === 2 ? "border-emerald-400 bg-emerald-50 text-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-300" : "bg-slate-50 dark:bg-slate-900/50"}`}>{value == null ? "—" : money(value)}</td>)}</tr>)}</tbody></table></div></section>
                        <p className="rounded-xl border p-4 text-xs leading-5 text-slate-500">Five-year FCF forecast. Cash is added, debt is deducted, and terminal value uses <span className="font-mono">FCF₅ × (1 + g) / (WACC − g)</span>. Inputs: FCF {compact(valuation.inputs.fcf)}, cash {compact(valuation.inputs.cash)}, debt {compact(valuation.inputs.debt)}, shares {compact(valuation.inputs.shares)}.</p>
                    </div>}

                    {activeTab === "valuation" && !valuation && <div className="space-y-3">
                        {saveMessage && <p className="text-xs font-bold text-emerald-600">{saveMessage}</p>}
                        <div className={valuationError ? "error-panel" : "rounded-xl border p-5 text-sm text-slate-500"} role={valuationError ? "alert" : "status"}>
                            {valuationError || "Valuation is being refreshed with the default scenarios."}
                        </div>
                    </div>}

                    {activeTab === "peers" && <div className="space-y-4">
                        <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-center"><div><h3 className="text-sm font-black">Published cross-sectional benchmarks</h3><p className="mt-1 text-xs text-slate-500">Midrank percentiles; invalid valuation multiples and negative debt/equity are excluded.</p></div><div className="flex rounded-lg border bg-slate-100 p-1 dark:bg-slate-900">{(["industry", "sector"] as const).map((scope) => <button key={scope} type="button" onClick={() => setPeerScope(scope)} className={`rounded-md px-3 py-1.5 text-xs font-bold capitalize ${peerScope === scope ? "bg-white text-emerald-700 shadow-sm dark:bg-slate-700 dark:text-emerald-300" : "text-slate-500"}`}>{scope}</button>)}</div></div>
                        <p className="text-xs text-slate-500">{peerScope === "industry" ? decision.peer_comparison.industry || "Unknown industry" : decision.peer_comparison.sector || "Unknown sector"} · {selectedPeerAvailable}/20 metrics meet the {peerScope === "industry" ? "10" : "20"}-observation threshold.</p>
                        <div className="overflow-x-auto rounded-xl border"><table className="w-full min-w-[760px] text-left text-xs"><thead className="surface-subtle"><tr><th className="px-4 py-3">Metric</th><th className="px-4 py-3">Value</th><th className="px-4 py-3">Direction</th><th className="px-4 py-3">Raw percentile</th><th className="px-4 py-3">Better-positioned</th><th className="px-4 py-3">Coverage</th></tr></thead><tbody className="divide-y">{decision.peer_comparison.metrics.map((metric) => { const scope = metric[peerScope]; return <tr key={metric.key}><td className="px-4 py-3"><span className="font-bold">{metric.label}</span><span className="ml-2 font-mono text-[10px] text-slate-400">{metric.evidence_id}</span></td><td className="px-4 py-3 font-mono font-bold">{peerValue(metric)}</td><td className="px-4 py-3 text-slate-500">{metric.direction.replace("_", " ")}</td><td className="px-4 py-3 font-mono">{scope.raw_percentile == null ? "—" : `${scope.raw_percentile.toFixed(1)}th`}</td><td className="px-4 py-3 font-mono font-bold">{scope.desirability_percentile == null ? "—" : `${scope.desirability_percentile.toFixed(1)}th`}</td><td className="px-4 py-3"><span className={scope.available ? "text-emerald-600" : "text-slate-500"}>{scope.observation_count} valid</span>{!scope.available && <p className="mt-1 max-w-xs text-[10px] leading-4 text-slate-500">{scope.reason}</p>}</td></tr>; })}</tbody></table></div>
                    </div>}

                    {activeTab === "risks" && <div className="space-y-5">
                        <div><h3 className="text-sm font-black">Triggered fundamental rules</h3><p className="mt-1 text-xs text-slate-500">Only deterministic financial-statement rules are included in Phase 1.</p></div>
                        {decision.risks.warnings.length ? <div className="grid gap-3 lg:grid-cols-2">{decision.risks.warnings.map((warning) => <RiskCard key={warning.id} warning={warning} onShowEvidence={() => onShowEvidence(warning.evidence_metric)} />)}</div> : <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-5 text-sm text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/20 dark:text-emerald-300"><CheckCircle2 className="mr-2 inline" size={17} />No rule is triggered by the available periods.</div>}
                        <section className="rounded-xl border p-4"><h3 className="flex items-center gap-2 text-xs font-black uppercase tracking-wide text-slate-500"><Database size={14} /> Data-quality notes</h3>{decision.risks.data_quality_notes.length ? <ul className="mt-3 space-y-2 text-sm text-slate-600 dark:text-slate-400">{decision.risks.data_quality_notes.map((note) => <li key={`${note.code}-${note.message}`} className="rounded-lg bg-slate-50 p-3 dark:bg-slate-900/50"><span className="font-mono text-[10px] text-slate-400">{note.code}</span><p className="mt-1">{note.message}</p></li>)}</ul> : <p className="mt-2 text-sm text-slate-500">No data-quality limitation was recorded for these checks.</p>}</section>
                    </div>}

                    {activeTab === "brief" && <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_280px]"><AIReport ticker={ticker} evidenceKey={briefEvidenceKey} adminKey={adminKey} onUnauthorized={onUnauthorized} embedded /><aside className="rounded-xl border p-4"><h3 className="text-xs font-black uppercase tracking-wide text-slate-500">Evidence registry</h3><p className="mt-2 text-xs leading-5 text-slate-500">The generator receives these stable records only. Unknown citations are rejected before display or caching.</p><div className="custom-scrollbar mt-3 max-h-[420px] space-y-2 overflow-y-auto">{decision.evidence.map((item) => <div key={item.id} className="flex items-start gap-2 rounded-lg bg-slate-50 p-2.5 text-xs dark:bg-slate-900/50"><span className={`font-mono font-black ${item.available ? "text-emerald-600" : "text-slate-400"}`}>{item.id}</span><div><p className="font-bold">{item.label}</p><p className="mt-0.5 text-[10px] text-slate-500">{item.available ? item.source_date || "current evidence" : "unavailable"}</p></div></div>)}</div></aside></div>}
                </div>
            )}
        </section>
    );
}
