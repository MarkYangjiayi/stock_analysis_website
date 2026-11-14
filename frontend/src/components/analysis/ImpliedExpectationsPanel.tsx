"use client";

import { useState } from "react";
import type { DecisionImpliedExpectations } from "@/lib/api";
import { SegmentedControl } from "@/components/ui/SegmentedControl";

const pct = (value: number, digits = 1) => `${(value * 100).toFixed(digits)}%`;
const multiple = (value: number) => `${value.toFixed(2)}×`;
const known = (values: Array<number | null | undefined>) => values.filter((value): value is number => value != null);
const range = (values: number[], format: (value: number) => string) => {
    const low = Math.min(...values);
    const high = Math.max(...values);
    return low === high ? format(low) : `${format(low)} to ${format(high)}`;
};

const MARGIN_LABELS = { trailing: "trailing", consensus: "consensus", five_year_high: "5Y-high" } as const;

/** Where the required value sits against the evidence: a band for the references, a tick for the need. */
function HurdleTrack({ needed, references }: { needed: number | null; references: number[] }) {
    const values = needed == null ? references : [...references, needed];
    const low = Math.min(...values);
    const high = Math.max(...values);
    const pad = (high - low) * 0.12 || Math.abs(high) * 0.12 || 0.01;
    const at = (value: number) => ((value - low + pad) / (high - low + 2 * pad)) * 100;
    return (
        <div className="relative h-5" aria-hidden="true">
            <div className="absolute inset-x-0 top-1/2 h-px bg-line" />
            <div
                className="absolute top-1/2 h-1.5 min-w-1.5 -translate-y-1/2 rounded-full bg-fg-muted/50"
                style={{ left: `${at(Math.min(...references))}%`, right: `${100 - at(Math.max(...references))}%` }}
            />
            {needed != null && <div className="absolute top-1/2 h-4 w-0.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-accent" style={{ left: `${at(needed)}%` }} />}
        </div>
    );
}

/** What the price requires: the return consensus offers, and the hurdle on each lever (rules in docs/implied_expectations.md). */
export default function ImpliedExpectationsPanel({ data }: { data: DecisionImpliedExpectations }) {
    const [requiredReturn, setRequiredReturn] = useState(String(data.default_required_return));
    const { inputs, hurdles, implied_return: impliedReturn } = data;
    const hurdle = hurdles?.find((item) => String(item.required_return) === requiredReturn);

    if (!data.available || !inputs || !hurdle || !impliedReturn) {
        return (
            <section className="rounded-lg border" aria-label="Implied expectations">
                <div className="surface-subtle rounded-t-lg p-5">
                    <p className="eyebrow">Implied expectations</p>
                    <h3 className="mt-1 text-base font-semibold">What the price requires</h3>
                </div>
                <ul className="space-y-1 border-t p-4 text-xs text-caution">{data.reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
            </section>
        );
    }

    const { history, consensus } = data.references;
    const required = Number(requiredReturn);
    const gap = impliedReturn.rate == null ? null : impliedReturn.rate - required;
    const levers = [
        {
            label: "Revenue growth, 5 years",
            holding: `at ${MARGIN_LABELS[impliedReturn.margin_basis]} margin ${pct(impliedReturn.target_margin)}`,
            needed: hurdle.growth_by_margin.find((item) => item.basis === impliedReturn.margin_basis)?.revenue_growth ?? null,
            format: pct,
            references: known([history.revenue_growth, consensus?.next_year_growth, consensus?.following_year_growth]),
            evidence: [
                consensus && `Consensus ${range([consensus.next_year_growth, consensus.following_year_growth], pct)}`,
                history.revenue_growth != null && `${history.revenue_growth_years}Y history ${pct(history.revenue_growth)}`,
            ],
        },
        ...(consensus ? [
            {
                label: "Operating margin",
                holding: "at consensus growth",
                needed: hurdle.margin_at_consensus_growth,
                format: pct,
                references: known([inputs.operating_margin_ttm, consensus.operating_margin, history.operating_margin_low, history.operating_margin_high]),
                evidence: [
                    `Now ${pct(inputs.operating_margin_ttm)}`,
                    consensus.operating_margin != null && `consensus ${pct(consensus.operating_margin)}`,
                    history.operating_margin_low != null && history.operating_margin_high != null
                        && `5Y ${range([history.operating_margin_low, history.operating_margin_high], pct)}`,
                ],
            },
            {
                label: "Revenue per $1 of capital",
                holding: "at consensus growth and margin",
                needed: hurdle.sales_to_capital_at_consensus,
                format: multiple,
                references: [inputs.sales_to_capital],
                evidence: [`Now ${multiple(inputs.sales_to_capital)}`],
            },
        ] : []),
    ];

    return (
        <section className="overflow-hidden rounded-lg border" aria-label="Implied expectations">
            <div className="surface-subtle grid gap-4 p-5 lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
                <div>
                    <p className="eyebrow">Implied expectations</p>
                    <h3 className="mt-1 text-base font-semibold">What the price requires</h3>
                    <p className="mt-2 max-w-2xl text-sm leading-6 text-fg-muted">Solved from revenue growth, operating margin and reinvestment, not trailing free cash flow. Hurdles, not forecasts.</p>
                </div>
                <div className="min-w-[220px] rounded-lg border bg-surface p-4">
                    <p className="text-xs font-medium text-fg-muted">Return if consensus holds</p>
                    <p className="mt-1 font-mono text-3xl font-semibold tabular-nums">
                        {impliedReturn.rate != null ? pct(impliedReturn.rate) : impliedReturn.status === "below_range" ? `< ${pct(impliedReturn.minimum_rate, 0)}` : "—"}
                    </p>
                    <p className="mt-1 font-mono text-xs text-fg-muted">
                        {gap != null ? `${gap >= 0 ? "+" : "−"}${Math.abs(gap * 100).toFixed(1)} pp vs ${pct(required, 0)} required`
                            : impliedReturn.status === "below_range" ? "Below the lowest rate solved" : "No current consensus"}
                    </p>
                </div>
            </div>

            <div className="border-t p-4 sm:p-5">
                <div className="flex flex-wrap items-center justify-between gap-3">
                    <div>
                        <h4 className="text-sm font-semibold">To earn {pct(required, 0)} a year</h4>
                        <p className="mt-0.5 text-xs text-fg-muted">{consensus ? "Each row solves one lever while the others follow consensus." : "No current consensus, so only the growth hurdle is solved."} Bar: evidence range. Tick: what the price needs.</p>
                    </div>
                    <SegmentedControl
                        label="Required return"
                        value={requiredReturn}
                        options={data.required_returns.map((rate) => ({ value: String(rate), label: pct(rate, 0) }))}
                        onChange={setRequiredReturn}
                    />
                </div>
                <div className="mt-3 divide-y">
                    {levers.map((lever) => (
                        <div key={lever.label} className="grid gap-x-5 gap-y-2 py-3 sm:grid-cols-[minmax(0,1fr)_6.5rem_minmax(0,1.2fr)] sm:items-center">
                            <div>
                                <p className="text-sm font-medium">{lever.label}</p>
                                <p className="text-xs text-fg-muted">{lever.holding}</p>
                            </div>
                            <p className="font-mono text-lg font-semibold tabular-nums sm:text-right">
                                {lever.needed != null ? lever.format(lever.needed) : <span className="text-sm font-medium text-fg-muted" title="Nothing in the search range matches the price: this lever alone cannot close the gap.">No solution</span>}
                            </p>
                            <div>
                                <HurdleTrack needed={lever.needed} references={lever.references} />
                                <p className="font-mono text-xs text-fg-muted">{lever.evidence.filter(Boolean).join(" · ")}</p>
                            </div>
                        </div>
                    ))}
                </div>
            </div>

            <dl className="grid gap-px border-t bg-line text-xs sm:grid-cols-3">
                {hurdle.growth_by_margin.map((item) => (
                    <div key={item.basis} className="bg-surface p-3.5">
                        <dt className="text-fg-muted">Growth at {MARGIN_LABELS[item.basis]} margin {pct(item.target_margin)}</dt>
                        <dd className="mt-1 font-mono font-semibold tabular-nums">{item.revenue_growth != null ? pct(item.revenue_growth) : "No solution"}</dd>
                    </div>
                ))}
            </dl>
            <p className="border-t px-4 py-3 text-xs leading-5 text-fg-muted sm:px-5">
                {data.model.forecast_years}-year forecast · terminal growth {pct(data.model.terminal_growth)} · no excess return after year {data.model.forecast_years}
                {consensus?.operating_margin != null && " · consensus margin is approximated from EPS"}
            </p>
        </section>
    );
}
