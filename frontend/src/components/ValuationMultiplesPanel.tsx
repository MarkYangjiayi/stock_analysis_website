"use client";

import React from 'react';
import { ValuationMetrics } from '@/lib/api';
import { TrendingUp, TrendingDown, Minus } from 'lucide-react';

interface ValuationMultiplesPanelProps {
    metrics: ValuationMetrics;
}

const formatCompact = (num: number | null | undefined): string => {
    if (num == null) return 'N/A';
    if (Math.abs(num) >= 1e12) return (num / 1e12).toFixed(2) + 'T';
    if (Math.abs(num) >= 1e9) return (num / 1e9).toFixed(2) + 'B';
    if (Math.abs(num) >= 1e6) return (num / 1e6).toFixed(2) + 'M';
    return num.toLocaleString(undefined, { maximumFractionDigits: 2 });
};

const formatPct = (num: number | null | undefined): string => {
    if (num == null) return 'N/A';
    return (num * 100).toFixed(1) + '%';
};

const formatX = (num: number | null | undefined): string => {
    if (num == null) return 'N/A';
    return num.toFixed(1) + 'x';
};

const formatNum = (num: number | null | undefined, decimals = 2): string => {
    if (num == null) return 'N/A';
    return num.toFixed(decimals);
};

interface MetricCardProps {
    label: string;
    value: string;
    hint?: string;
    sentiment?: 'positive' | 'negative' | 'neutral';
}

const MetricCard: React.FC<MetricCardProps> = ({ label, value, hint, sentiment = 'neutral' }) => {
    const isNA = value === 'N/A';
    const valueColor = isNA
        ? 'text-slate-400 dark:text-gray-500'
        : sentiment === 'positive'
            ? 'text-emerald-600 dark:text-emerald-400'
            : sentiment === 'negative'
                ? 'text-rose-600 dark:text-rose-400'
                : 'text-slate-800 dark:text-gray-100';

    const SentimentIcon = sentiment === 'positive'
        ? TrendingUp
        : sentiment === 'negative'
            ? TrendingDown
            : Minus;

    return (
        <div className="bg-slate-50 dark:bg-[#1a1f2e] border border-gray-200 dark:border-gray-800/70 rounded-lg p-3 flex flex-col gap-1 hover:border-emerald-500/30 transition-all">
            <span className="text-xs font-semibold text-slate-500 dark:text-gray-500 uppercase tracking-wide">{label}</span>
            <div className="flex items-center justify-between gap-2">
                <span className={`text-base font-black ${valueColor}`}>{value}</span>
                {!isNA && sentiment !== 'neutral' && (
                    <SentimentIcon size={14} className={sentiment === 'positive' ? 'text-emerald-500' : 'text-rose-500'} />
                )}
            </div>
            {hint && <span className="text-xs text-slate-400 dark:text-gray-600">{hint}</span>}
        </div>
    );
};

interface SectionProps {
    title: string;
    children: React.ReactNode;
}

const Section: React.FC<SectionProps> = ({ title, children }) => (
    <div>
        <h5 className="text-xs font-bold text-slate-400 dark:text-gray-500 uppercase tracking-widest mb-3 px-1">{title}</h5>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6 gap-2">
            {children}
        </div>
    </div>
);

const ValuationMultiplesPanel: React.FC<ValuationMultiplesPanelProps> = ({ metrics }) => {
    if (!metrics) return null;

    const { multiples, profitability, financial_health, growth_metrics } = metrics;

    const mktCapStr = multiples?.market_cap ? formatCompact(multiples.market_cap) : 'N/A';
    const evStr = multiples?.enterprise_value ? formatCompact(multiples.enterprise_value) : 'N/A';

    return (
        <div className="bg-white dark:bg-[#191D26] border border-gray-200 dark:border-gray-800 rounded-2xl p-5 shadow-sm dark:shadow-inner space-y-5">
            <div className="flex items-center justify-between">
                <h4 className="text-sm font-bold text-slate-700 dark:text-gray-200 tracking-wide">Key Financial Metrics</h4>
                <div className="flex gap-3 text-xs text-slate-500 dark:text-gray-500">
                    {multiples?.market_cap != null && (
                        <span>Mkt Cap: <span className="font-bold text-slate-700 dark:text-gray-300">{mktCapStr}</span></span>
                    )}
                    {multiples?.enterprise_value != null && (
                        <span>EV: <span className="font-bold text-slate-700 dark:text-gray-300">{evStr}</span></span>
                    )}
                </div>
            </div>

            {/* Valuation Multiples */}
            {multiples && (
                <Section title="Valuation">
                    <MetricCard
                        label="P/E"
                        value={formatX(multiples.pe_ratio)}
                        sentiment={multiples.pe_ratio != null ? (multiples.pe_ratio < 15 ? 'positive' : multiples.pe_ratio > 40 ? 'negative' : 'neutral') : 'neutral'}
                    />
                    <MetricCard
                        label="P/B"
                        value={formatX(multiples.pb_ratio)}
                        sentiment={multiples.pb_ratio != null ? (multiples.pb_ratio < 3 ? 'positive' : multiples.pb_ratio > 8 ? 'negative' : 'neutral') : 'neutral'}
                    />
                    <MetricCard
                        label="P/S"
                        value={formatX(multiples.ps_ratio)}
                        sentiment={multiples.ps_ratio != null ? (multiples.ps_ratio < 3 ? 'positive' : multiples.ps_ratio > 10 ? 'negative' : 'neutral') : 'neutral'}
                    />
                    <MetricCard
                        label="P/FCF"
                        value={formatX(multiples.pfcf_ratio)}
                        sentiment={multiples.pfcf_ratio != null ? (multiples.pfcf_ratio < 20 ? 'positive' : multiples.pfcf_ratio > 50 ? 'negative' : 'neutral') : 'neutral'}
                    />
                    <MetricCard
                        label="EV/EBITDA"
                        value={formatX(multiples.ev_ebitda)}
                        sentiment={multiples.ev_ebitda != null ? (multiples.ev_ebitda < 12 ? 'positive' : multiples.ev_ebitda > 25 ? 'negative' : 'neutral') : 'neutral'}
                    />
                    <MetricCard
                        label="EV/Revenue"
                        value={formatX(multiples.ev_revenue)}
                        sentiment={multiples.ev_revenue != null ? (multiples.ev_revenue < 3 ? 'positive' : multiples.ev_revenue > 10 ? 'negative' : 'neutral') : 'neutral'}
                    />
                    <MetricCard
                        label="EPS (TTM)"
                        value={multiples.eps != null ? `$${formatNum(multiples.eps)}` : 'N/A'}
                        sentiment={multiples.eps != null ? (multiples.eps > 0 ? 'positive' : 'negative') : 'neutral'}
                    />
                    <MetricCard
                        label="Net Debt"
                        value={multiples.net_debt != null ? formatCompact(multiples.net_debt) : 'N/A'}
                        sentiment={multiples.net_debt != null ? (multiples.net_debt < 0 ? 'positive' : 'neutral') : 'neutral'}
                        hint={multiples.net_debt != null && multiples.net_debt < 0 ? 'Net cash' : undefined}
                    />
                </Section>
            )}

            {/* Profitability */}
            {profitability && (
                <Section title="Profitability">
                    <MetricCard
                        label="Gross Margin"
                        value={formatPct(profitability.gross_margin)}
                        sentiment={profitability.gross_margin != null ? (profitability.gross_margin > 0.4 ? 'positive' : profitability.gross_margin < 0.1 ? 'negative' : 'neutral') : 'neutral'}
                    />
                    <MetricCard
                        label="Op. Margin"
                        value={formatPct(profitability.operating_margin)}
                        sentiment={profitability.operating_margin != null ? (profitability.operating_margin > 0.15 ? 'positive' : profitability.operating_margin < 0 ? 'negative' : 'neutral') : 'neutral'}
                    />
                    <MetricCard
                        label="Net Margin"
                        value={formatPct(profitability.net_margin)}
                        sentiment={profitability.net_margin != null ? (profitability.net_margin > 0.1 ? 'positive' : profitability.net_margin < 0 ? 'negative' : 'neutral') : 'neutral'}
                    />
                    <MetricCard
                        label="ROE"
                        value={formatPct(profitability.roe)}
                        sentiment={profitability.roe != null ? (profitability.roe > 0.15 ? 'positive' : profitability.roe < 0 ? 'negative' : 'neutral') : 'neutral'}
                    />
                    <MetricCard
                        label="ROA"
                        value={formatPct(profitability.roa)}
                        sentiment={profitability.roa != null ? (profitability.roa > 0.05 ? 'positive' : profitability.roa < 0 ? 'negative' : 'neutral') : 'neutral'}
                    />
                    <MetricCard
                        label="FCF Conv."
                        value={formatX(profitability.fcf_conversion)}
                        hint="FCF / Net Income"
                        sentiment={profitability.fcf_conversion != null ? (profitability.fcf_conversion > 0.8 ? 'positive' : profitability.fcf_conversion < 0 ? 'negative' : 'neutral') : 'neutral'}
                    />
                </Section>
            )}

            {/* Financial Health */}
            {financial_health && (
                <Section title="Financial Health">
                    <MetricCard
                        label="Current Ratio"
                        value={formatNum(financial_health.current_ratio)}
                        sentiment={financial_health.current_ratio != null ? (financial_health.current_ratio > 1.5 ? 'positive' : financial_health.current_ratio < 1 ? 'negative' : 'neutral') : 'neutral'}
                    />
                    <MetricCard
                        label="Debt/Equity"
                        value={formatX(financial_health.debt_to_equity)}
                        sentiment={financial_health.debt_to_equity != null ? (financial_health.debt_to_equity < 0.5 ? 'positive' : financial_health.debt_to_equity > 2 ? 'negative' : 'neutral') : 'neutral'}
                    />
                    <MetricCard
                        label="Net Debt/EBITDA"
                        value={formatX(financial_health.net_debt_ebitda)}
                        sentiment={financial_health.net_debt_ebitda != null ? (financial_health.net_debt_ebitda < 2 ? 'positive' : financial_health.net_debt_ebitda > 4 ? 'negative' : 'neutral') : 'neutral'}
                    />
                    <MetricCard
                        label="Int. Coverage"
                        value={formatX(financial_health.interest_coverage)}
                        hint="EBIT / Interest"
                        sentiment={financial_health.interest_coverage != null ? (financial_health.interest_coverage > 5 ? 'positive' : financial_health.interest_coverage < 2 ? 'negative' : 'neutral') : 'neutral'}
                    />
                </Section>
            )}

            {/* Growth */}
            {growth_metrics && (
                <Section title="Growth">
                    <MetricCard
                        label="Rev. YoY"
                        value={formatPct(growth_metrics.revenue_yoy)}
                        sentiment={growth_metrics.revenue_yoy != null ? (growth_metrics.revenue_yoy > 0.1 ? 'positive' : growth_metrics.revenue_yoy < 0 ? 'negative' : 'neutral') : 'neutral'}
                    />
                    <MetricCard
                        label="Rev. CAGR 3Y"
                        value={formatPct(growth_metrics.revenue_cagr_3yr)}
                        sentiment={growth_metrics.revenue_cagr_3yr != null ? (growth_metrics.revenue_cagr_3yr > 0.1 ? 'positive' : growth_metrics.revenue_cagr_3yr < 0 ? 'negative' : 'neutral') : 'neutral'}
                    />
                    <MetricCard
                        label="EPS YoY"
                        value={formatPct(growth_metrics.eps_yoy)}
                        sentiment={growth_metrics.eps_yoy != null ? (growth_metrics.eps_yoy > 0.1 ? 'positive' : growth_metrics.eps_yoy < 0 ? 'negative' : 'neutral') : 'neutral'}
                    />
                    <MetricCard
                        label="EPS CAGR 3Y"
                        value={formatPct(growth_metrics.eps_cagr_3yr)}
                        sentiment={growth_metrics.eps_cagr_3yr != null ? (growth_metrics.eps_cagr_3yr > 0.1 ? 'positive' : growth_metrics.eps_cagr_3yr < 0 ? 'negative' : 'neutral') : 'neutral'}
                    />
                    <MetricCard
                        label="FCF YoY"
                        value={formatPct(growth_metrics.fcf_yoy)}
                        sentiment={growth_metrics.fcf_yoy != null ? (growth_metrics.fcf_yoy > 0.1 ? 'positive' : growth_metrics.fcf_yoy < 0 ? 'negative' : 'neutral') : 'neutral'}
                    />
                </Section>
            )}
        </div>
    );
};

export default ValuationMultiplesPanel;
