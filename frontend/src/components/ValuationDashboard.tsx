"use client";

import { BarChart3, CircleDollarSign, Gauge, Landmark, Scale, TrendingUp } from "lucide-react";
import { ValuationMetrics } from "@/lib/api";

const compact = (value: number, currency = true) => {
    if (!Number.isFinite(value)) return "—";
    const prefix = currency ? "$" : "";
    const absolute = Math.abs(value);
    if (absolute >= 1e12) return `${prefix}${(value / 1e12).toFixed(2)}T`;
    if (absolute >= 1e9) return `${prefix}${(value / 1e9).toFixed(2)}B`;
    if (absolute >= 1e6) return `${prefix}${(value / 1e6).toFixed(2)}M`;
    return `${prefix}${value.toFixed(2)}`;
};

export default function ValuationDashboard({ metrics }: { metrics: ValuationMetrics }) {
    const { ttm, valuation, balance_sheet_latest: balance } = metrics;
    const leverage = balance.total_stockholder_equity > 0 ? balance.total_liabilities / balance.total_stockholder_equity : null;
    const items = [
        { label: "Illustrative DCF", value: compact(valuation.dcf_intrinsic_value_per_share), sub: `Market ${compact(valuation.current_price)}`, icon: Scale, tone: "text-indigo-500" },
        { label: "Margin of safety", value: `${(valuation.margin_of_safety * 100).toFixed(1)}%`, sub: valuation.margin_of_safety >= 0 ? "Below modeled value" : "Above modeled value", icon: Gauge, tone: valuation.margin_of_safety >= 0 ? "text-emerald-500" : "text-rose-500" },
        { label: "TTM revenue", value: compact(ttm.revenue), sub: "Trailing twelve months", icon: BarChart3, tone: "text-sky-500" },
        { label: "TTM net income", value: compact(ttm.net_income), sub: "Trailing twelve months", icon: TrendingUp, tone: "text-violet-500" },
        { label: "Free cash flow", value: compact(ttm.free_cash_flow), sub: "Trailing twelve months", icon: CircleDollarSign, tone: "text-emerald-500" },
        { label: "Return on equity", value: `${(ttm.roe * 100).toFixed(1)}%`, sub: "TTM earnings / latest equity", icon: Landmark, tone: "text-amber-500" },
    ];

    return (
        <section className="surface-panel p-5 sm:p-6" aria-labelledby="valuation-title">
            <div className="flex flex-col justify-between gap-2 border-b pb-4 sm:flex-row sm:items-end">
                <div>
                    <p className="eyebrow">Fundamental snapshot</p>
                    <h2 id="valuation-title" className="mt-1 text-lg font-black">Valuation & operating profile</h2>
                </div>
                <span className="text-xs text-slate-500">Illustrative model, not an investment recommendation</span>
            </div>
            <div className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
                {items.map(({ label, value, sub, icon: Icon, tone }) => (
                    <div key={label} className="surface-subtle rounded-xl border p-4">
                        <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                            <Icon className={tone} size={16} /> {label}
                        </div>
                        <p className={`mt-3 whitespace-nowrap font-mono text-[clamp(1.35rem,2.2vw,1.8rem)] font-black tracking-tight ${tone}`}>{value}</p>
                        <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{sub}</p>
                    </div>
                ))}
            </div>
            <div className="mt-4 grid gap-2 rounded-xl border p-3 text-xs text-slate-500 dark:text-slate-400 sm:grid-cols-4">
                <span>Growth: <strong className="text-slate-700 dark:text-slate-200">{(valuation.assumptions.fcf_growth_rate_5yr * 100).toFixed(1)}%</strong></span>
                <span>WACC: <strong className="text-slate-700 dark:text-slate-200">{(valuation.assumptions.wacc * 100).toFixed(1)}%</strong></span>
                <span>Terminal: <strong className="text-slate-700 dark:text-slate-200">{(valuation.assumptions.perpetual_growth * 100).toFixed(1)}%</strong></span>
                <span>Liabilities / equity: <strong className="text-slate-700 dark:text-slate-200">{leverage == null ? "—" : `${leverage.toFixed(2)}×`}</strong></span>
            </div>
        </section>
    );
}
