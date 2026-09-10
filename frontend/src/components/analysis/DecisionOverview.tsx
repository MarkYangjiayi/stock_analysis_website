"use client";

import { ArrowRight, CircleAlert, ShieldCheck } from "lucide-react";
import type { DecisionSupportResponse, DecisionValuation } from "@/lib/api";
import { formatMoney, formatSignedPercent } from "@/lib/format";

export default function DecisionOverview({
    decision, valuation, working, onValuation, onRisks,
}: {
    decision: DecisionSupportResponse;
    valuation: DecisionValuation;
    working: boolean;
    onValuation: () => void;
    onRisks: () => void;
}) {
    const money = (value?: number | null) => formatMoney(value, decision.metadata.currency);
    const scenarios = valuation.scenarios;
    const values = scenarios.flatMap((scenario) => scenario.available && Number.isFinite(scenario.intrinsic_value_per_share) ? [scenario.intrinsic_value_per_share!] : []);
    const price = valuation.current_price;
    const plotValues = price != null && Number.isFinite(price) ? [...values, price] : values;
    const low = Math.min(...plotValues);
    const high = Math.max(...plotValues);
    const position = (value: number) => 6 + ((value - low) / (high - low)) * 88;
    const warnings = [...decision.risks.warnings].sort((a, b) => Number(b.severity === "high") - Number(a.severity === "high"));
    const unavailableChecks = decision.evidence.filter((item) => item.kind === "fundamental_warning" && !item.available).length;
    const limited = unavailableChecks > 0 || decision.summary.coverage.missing_data_reasons.length > 0;

    return (
        <div className="overview-grid" data-testid="overview-summary">
            <section className="research-panel p-4 sm:p-5" aria-labelledby="valuation-outlook-title">
                <header className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1">
                    <div>
                        <h2 id="valuation-outlook-title" className="text-lg font-semibold tracking-tight">Valuation outlook</h2>
                        <p className="mt-1 text-xs text-[var(--text-muted)]">Five-year FCFF scenarios · {decision.metadata.currency || "USD"}</p>
                    </div>
                    <button type="button" className="research-link" onClick={onValuation}>View assumptions <ArrowRight size={14} /></button>
                </header>
                {working && <p className="mt-2 text-xs text-[var(--warning)]">Working valuation · not saved to server evidence</p>}
                <div className="mt-3 grid grid-cols-3 divide-x">
                    {scenarios.map((scenario) => (
                        <div key={scenario.scenario} className="min-w-0 px-2 text-center first:pl-0 last:pr-0">
                            <p className="text-sm capitalize text-[var(--text-muted)]">{scenario.scenario}</p>
                            <p className="mt-1 break-words text-xl font-semibold leading-7 tracking-tight sm:text-2xl sm:leading-7">{scenario.available ? money(scenario.intrinsic_value_per_share) : "—"}</p>
                            <p className={`mt-1 text-xs ${!scenario.available || scenario.upside_downside == null ? "text-[var(--text-muted)]" : scenario.upside_downside < 0 ? "text-[var(--negative)]" : "text-[var(--positive)]"}`}>
                                {scenario.available && scenario.upside_downside != null ? `${formatSignedPercent(scenario.upside_downside, 0)} vs price` : "Unavailable"}
                            </p>
                        </div>
                    ))}
                </div>
                {values.length >= 2 && high > low && (
                    <div className="relative mx-2 mt-2 h-10" role="img" aria-label={`Scenario range ${money(Math.min(...values))} to ${money(Math.max(...values))}. Current price ${money(price)}.`}>
                        <div className="absolute inset-x-[6%] top-8 h-1 rounded-full bg-gradient-to-r from-rose-200 via-[var(--border-strong)] to-emerald-400 dark:from-rose-900 dark:to-emerald-600" />
                        {values.map((value, index) => <span key={index} className="absolute top-[29px] h-2.5 w-2.5 -translate-x-1/2 rounded-full border-2 border-[var(--surface)] bg-[var(--brand)]" style={{ left: `${position(value)}%` }} />)}
                        {price != null && Number.isFinite(price) && <>
                            <span className="absolute top-0 whitespace-nowrap text-xs font-medium" style={{ left: `${position(price)}%`, transform: `translateX(${position(price) < 20 ? 0 : position(price) > 80 ? -100 : -50}%)` }}>Current {money(price)}</span>
                            <span className="absolute top-[22px] h-3 w-px bg-[var(--text)]" style={{ left: `${position(price)}%` }} />
                            <span className="absolute top-[29px] h-2.5 w-2.5 -translate-x-1/2 rounded-full bg-[var(--text)] ring-2 ring-[var(--surface)]" style={{ left: `${position(price)}%` }} />
                        </>}
                    </div>
                )}
                <p className="mt-1 text-xs leading-5 text-[var(--text-muted)]">{valuation.position.text}</p>
                {!valuation.available && <ul className="mt-2 space-y-1 text-xs text-[var(--warning)]">{valuation.unavailable_reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>}
            </section>

            <section className="research-panel flex flex-col p-4" aria-labelledby="key-risks-title">
                <header className="flex flex-wrap items-center justify-between gap-2">
                    <h2 id="key-risks-title" className="flex items-center gap-2 text-lg font-semibold tracking-tight"><CircleAlert size={19} className="text-[var(--warning)]" /> Key risks</h2>
                    <span className="text-xs text-[var(--text-muted)]">{warnings.length} {warnings.length === 1 ? "item" : "items"} to review</span>
                </header>
                <div className="mt-2 flex-1 divide-y">
                    {warnings.slice(0, 2).map((warning) => <div key={warning.id} className="py-2">
                        <p className="flex items-center gap-2 text-sm font-semibold"><span className={`h-1.5 w-1.5 shrink-0 rounded-full ${warning.severity === "high" ? "bg-[var(--negative)]" : "bg-[var(--warning)]"}`} />{warning.title}</p>
                        <p className="mt-1 text-xs leading-5 text-[var(--text-muted)]">{warning.message}</p>
                    </div>)}
                    {!warnings.length && <div className="py-4">
                        <ShieldCheck size={23} className={limited ? "text-[var(--warning)]" : "text-[var(--brand)]"} />
                        <p className="mt-2 text-sm font-medium">{limited ? "Risk coverage is limited" : "No triggered fundamental warnings"}</p>
                        <p className="mt-1 text-xs leading-5 text-[var(--text-muted)]">{limited ? "Some evidence is missing. Review coverage before drawing conclusions." : "Based on the available financial history and evaluated rules."}</p>
                    </div>}
                </div>
                {unavailableChecks > 0 && <p className="text-xs leading-5 text-[var(--warning)]">{unavailableChecks} warning {unavailableChecks === 1 ? "check" : "checks"} unavailable.</p>}
                <button type="button" className="research-link mt-1 self-start" onClick={onRisks}>View all evidence <ArrowRight size={14} /></button>
            </section>
        </div>
    );
}
