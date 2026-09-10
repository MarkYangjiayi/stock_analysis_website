"use client";

import { useState } from "react";
import type { DecisionValuationScenarioInput, OperatingForecastYear } from "@/lib/api";

type Draft = Pick<DecisionValuationScenarioInput, "scenario" | "operating_forecast" | "terminal_roic" | "forecast_as_of"> & {
    fcf_growth_rate: string; wacc: string; perpetual_growth: string;
};
const columns: Array<{ key: Exclude<keyof OperatingForecastYear, "year" | "source">; label: string; scale: number }> = [
    { key: "revenue", label: "Revenue", scale: 1e6 },
    { key: "operating_margin", label: "EBIT margin %", scale: 0.01 },
    { key: "tax_rate", label: "Tax %", scale: 0.01 },
    { key: "capex", label: "Capex", scale: 1e6 },
    { key: "depreciation", label: "D&A", scale: 1e6 },
    { key: "change_in_working_capital", label: "Δ working capital", scale: 1e6 },
];
const blankRows = (): OperatingForecastYear[] => Array.from({ length: 10 }, (_, i) => ({
    year: i + 1, revenue: NaN, operating_margin: NaN, tax_rate: NaN,
    capex: NaN, depreciation: NaN, change_in_working_capital: NaN, source: "",
}));

export default function OperatingForecastEditor({ scenarios, disabled, onChange }: {
    scenarios: Draft[]; disabled: boolean; onChange: (next: Draft[]) => void;
}) {
    const [selected, setSelected] = useState(1);
    const enabled = !!scenarios[1]?.operating_forecast;
    const current = scenarios[selected];
    const edit = (change: Partial<Draft>) => onChange(scenarios.map((s, i) => i === selected ? { ...s, ...change } : s));
    return <details className="rounded-xl border p-4">
        <summary className="cursor-pointer text-sm font-bold">Annual operating forecast</summary>
        <p className="mt-2 text-xs leading-5 text-slate-500">Use a documented revenue, margin and investment path when historical cash flow cannot represent the business. Early cash flows may be negative. All three cases need ten annual assumptions and a positive sustainable terminal cash flow. Amounts are in millions of the reporting currency. Positive Δ working capital consumes cash.</p>
        <label className="mt-3 flex items-center gap-2 text-sm"><input type="checkbox" checked={enabled} disabled={disabled} onChange={event => onChange(scenarios.map(s => ({ ...s,
            operating_forecast: event.target.checked ? blankRows() : undefined,
            terminal_roic: event.target.checked ? Number(s.wacc) / 100 : undefined,
            forecast_as_of: event.target.checked ? new Date().toISOString().slice(0, 10) : undefined,
        })))} /> Use operating forecasts for all cases</label>
        {enabled && current && <div className="mt-4 space-y-3">
            <div className="flex gap-2">{scenarios.map((s, i) => <button key={s.scenario} type="button" className={selected === i ? "primary-button" : "secondary-button"} onClick={() => setSelected(i)}>{s.scenario}</button>)}</div>
            <div className="grid gap-3 sm:grid-cols-3">
                <label className="text-xs">Assumptions as of<input className="control-field mt-1" type="date" value={current.forecast_as_of || ""} disabled={disabled} onChange={event => edit({ forecast_as_of: event.target.value })} /></label>
                <label className="text-xs">Terminal ROIC %<input aria-label={`${current.scenario} terminal ROIC`} className="control-field mt-1" type="number" min={0.1} max={100} step="0.1" value={current.terminal_roic == null || !Number.isFinite(current.terminal_roic) ? "" : current.terminal_roic * 100} disabled={disabled} onChange={event => edit({ terminal_roic: event.target.value === "" ? NaN : Number(event.target.value) / 100 })} /></label>
                <label className="text-xs">Assumption source / rationale<input aria-label={`${current.scenario} forecast source`} className="control-field mt-1" placeholder="Forecast source, date and rationale" value={current.operating_forecast?.[0]?.source || ""} disabled={disabled} onChange={event => edit({ operating_forecast: current.operating_forecast?.map(row => ({ ...row, source: event.target.value })) })} /></label>
            </div>
            <div className="overflow-x-auto"><table className="w-full min-w-[860px] text-xs"><thead><tr><th>Year</th>{columns.map(c => <th key={c.key} className="p-2 text-left">{c.label}</th>)}</tr></thead><tbody>
                {current.operating_forecast?.map((row, ri) => <tr key={row.year}><th className="p-2">{row.year}</th>{columns.map(c => <td key={c.key} className="p-1"><input aria-label={`${current.scenario} year ${row.year} ${c.label}`} className="control-field w-28 font-mono" type="number" step="any" disabled={disabled}
                    value={Number.isFinite(row[c.key]) ? Number((row[c.key] / c.scale).toPrecision(12)) : ""}
                    onChange={event => edit({ operating_forecast: current.operating_forecast?.map((r, i) => i === ri ? { ...r, [c.key]: event.target.value === "" ? NaN : Number(event.target.value) * c.scale } : r) })} /></td>)}</tr>)}
            </tbody></table></div>
            <p className="text-xs text-slate-500">FCFF = EBIT after tax − capex + D&A − Δ working capital. Terminal reinvestment = max(growth, 0) / ROIC × terminal operating profit after tax. ROIC initially equals WACC; document any change. Initial FCF-growth controls are unused in this mode.</p>
        </div>}
    </details>;
}
