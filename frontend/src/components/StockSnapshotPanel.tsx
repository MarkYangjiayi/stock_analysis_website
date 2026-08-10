"use client";

import { useEffect, useState } from "react";
import {
    Activity,
    BarChart3,
    ChevronDown,
    CircleDollarSign,
    Gauge,
    Landmark,
    RefreshCw,
    TrendingUp,
} from "lucide-react";
import type { MarketSnapshotMetric, MarketSnapshotResponse } from "@/lib/api";

type SnapshotField = {
    key: string;
    label: string;
    description: string;
};

type SnapshotGroup = {
    id: string;
    label: string;
    eyebrow: string;
    icon: typeof Activity;
    fields: SnapshotField[];
};

const field = (key: string, label: string, description: string): SnapshotField => ({ key, label, description });

const GROUPS: SnapshotGroup[] = [
    {
        id: "overview",
        label: "Company overview",
        eyebrow: "Scale & ownership",
        icon: Landmark,
        fields: [
            field("index_membership", "Index", "Current published index memberships."),
            field("market_cap", "Market Cap", "Equity market value at the Screener snapshot date."),
            field("enterprise_value", "Enterprise Value", "Market capitalization plus debt less cash."),
            field("sales_ttm", "Sales TTM", "Revenue from four contiguous reported quarters."),
            field("net_income_ttm", "Net Income TTM", "Net income from four contiguous reported quarters."),
            field("book_per_share", "Book / Share", "Latest shareholder equity divided by split-adjusted shares."),
            field("cash_per_share", "Cash / Share", "Latest cash and short-term investments divided by split-adjusted shares."),
            field("shares_outstanding", "Shares Outstanding", "Provider-reported shares outstanding."),
            field("shares_float", "Float", "Shares available for public trading."),
            field("short_float", "Short Float", "Short interest as a percentage of float."),
            field("ipo_date", "IPO Date", "Provider-published initial public offering date."),
        ],
    },
    {
        id: "valuation",
        label: "Valuation",
        eyebrow: "Market multiples",
        icon: CircleDollarSign,
        fields: [
            field("pe_ratio", "P/E", "Price divided by trailing earnings; unavailable for non-positive earnings."),
            field("forward_pe", "Forward P/E", "Price divided by forward consensus earnings."),
            field("peg_ratio", "PEG", "P/E relative to expected five-year earnings growth."),
            field("ps_ratio", "P/S", "Market capitalization divided by trailing sales."),
            field("pb_ratio", "P/B", "Market capitalization divided by positive book equity."),
            field("price_cash", "P/Cash", "Market capitalization divided by cash."),
            field("price_fcf", "P/FCF", "Market capitalization divided by positive trailing free cash flow."),
            field("ev_sales", "EV/Sales", "Enterprise value divided by trailing revenue."),
            field("ev_ebitda", "EV/EBITDA", "Enterprise value divided by positive EBITDA."),
        ],
    },
    {
        id: "growth",
        label: "Growth",
        eyebrow: "Earnings & sales",
        icon: TrendingUp,
        fields: [
            field("eps_ttm", "EPS TTM", "Provider-published trailing earnings per share."),
            field("eps_next_quarter", "EPS Next Quarter", "Current forward-quarter consensus EPS, with next-quarter fallback."),
            field("eps_next_year", "EPS Next Year", "Next fiscal-year consensus EPS."),
            field("eps_growth_this_year", "EPS Growth This Year", "Consensus current fiscal-year EPS growth."),
            field("eps_growth_next_year", "EPS Growth Next Year", "Consensus next fiscal-year EPS growth."),
            field("eps_growth_qoq", "EPS Growth Q/Q", "Most recent quarter EPS growth versus the comparable prior-year quarter."),
            field("eps_growth_ttm", "EPS Growth TTM", "Trailing EPS growth versus the previous trailing period."),
            field("eps_growth_3yr", "EPS Growth 3Y", "Three-year annualized EPS growth."),
            field("eps_growth_5yr", "EPS Growth 5Y", "Five-year annualized EPS growth."),
            field("sales_growth_qoq", "Sales Growth Q/Q", "Most recent quarter revenue growth versus the comparable prior-year quarter."),
            field("sales_growth_ttm", "Sales Growth TTM", "Trailing revenue growth versus the previous trailing period."),
            field("sales_growth_3yr", "Sales Growth 3Y", "Three-year annualized sales growth."),
            field("sales_growth_5yr", "Sales Growth 5Y", "Five-year annualized sales growth."),
        ],
    },
    {
        id: "quality",
        label: "Profitability & health",
        eyebrow: "Returns, margins & balance sheet",
        icon: BarChart3,
        fields: [
            field("roa", "ROA", "Trailing return on assets."),
            field("roe", "ROE", "Trailing return on positive shareholder equity."),
            field("roic", "ROIC", "After-tax operating profit divided by invested capital."),
            field("gross_margin", "Gross Margin", "Trailing gross profit divided by revenue."),
            field("operating_margin", "Operating Margin", "Trailing operating income divided by revenue."),
            field("net_profit_margin", "Net Margin", "Trailing net income divided by revenue."),
            field("current_ratio", "Current Ratio", "Current assets divided by current liabilities."),
            field("quick_ratio", "Quick Ratio", "Current assets less inventory divided by current liabilities."),
            field("debt_to_equity", "Debt / Equity", "Total debt divided by positive shareholder equity."),
            field("lt_debt_to_equity", "LT Debt / Equity", "Long-term debt divided by positive shareholder equity."),
            field("insider_ownership", "Insider Ownership", "Provider-reported insider ownership."),
            field("institutional_ownership", "Institutional Ownership", "Provider-reported institutional ownership."),
            field("payout_ratio", "Payout", "Dividend payout relative to positive earnings."),
        ],
    },
    {
        id: "technicals",
        label: "Technicals",
        eyebrow: "Trend, range & volume",
        icon: Gauge,
        fields: [
            field("sma20_distance", "SMA20", "Price distance from the 20-session simple moving average."),
            field("sma50_distance", "SMA50", "Price distance from the 50-session simple moving average."),
            field("sma200_distance", "SMA200", "Price distance from the 200-session simple moving average."),
            field("high_52w", "52W High", "Highest adjusted close in the latest 252-session window and current distance from it."),
            field("low_52w", "52W Low", "Lowest adjusted close in the latest 252-session window and current distance from it."),
            field("volatility_1w", "Volatility 1W", "Average daily high-low range over five sessions."),
            field("volatility_1m", "Volatility 1M", "Average daily high-low range over 21 sessions."),
            field("atr_14", "ATR (14)", "Fourteen-session Wilder average true range."),
            field("rsi_14", "RSI (14)", "Fourteen-session relative strength index."),
            field("beta_1yr", "Beta", "One-year adjusted-return beta versus the benchmark."),
            field("relative_volume", "Relative Volume", "Current volume divided by the 63-session average."),
            field("average_volume_3m", "Average Volume", "Average volume over 63 trading sessions."),
            field("volume", "Volume", "Latest published session volume."),
        ],
    },
    {
        id: "performance",
        label: "Performance & outlook",
        eyebrow: "Returns, dividends & analysts",
        icon: Activity,
        fields: [
            field("performance_1w", "Performance 1W", "Adjusted-price return over five sessions."),
            field("performance_1m", "Performance 1M", "Adjusted-price return over 21 sessions."),
            field("performance_3m", "Performance 3M", "Adjusted-price return over 63 sessions."),
            field("performance_6m", "Performance 6M", "Adjusted-price return over 126 sessions."),
            field("performance_ytd", "Performance YTD", "Adjusted-price return since the prior year-end close."),
            field("performance_1yr", "Performance 1Y", "Adjusted-price return over 252 sessions."),
            field("performance_3yr", "Performance 3Y", "Adjusted-price return since the closest observation at least three years ago."),
            field("performance_5yr", "Performance 5Y", "Adjusted-price return since the closest observation at least five years ago."),
            field("performance_10yr", "Performance 10Y", "Adjusted-price return since the closest observation at least ten years ago."),
            field("dividend_estimate", "Dividend Estimate", "Provider-published forward annual dividend per share."),
            field("dividend_ttm", "Dividend TTM", "Cash dividends with ex-dates in the latest 12 months."),
            field("dividend_ex_date", "Dividend Ex-Date", "Provider-published ex-dividend date."),
            field("dividend_growth_3yr", "Dividend Growth 3Y", "Three-year annualized dividend growth."),
            field("dividend_growth_5yr", "Dividend Growth 5Y", "Five-year annualized dividend growth."),
            field("analyst_recommendation", "Recommendation", "Analyst scale from 1 (Strong Buy) to 5 (Strong Sell)."),
            field("target_price", "Target Price", "Provider-published Wall Street target price."),
            field("prev_close", "Previous Close", "Adjusted close from the preceding available session."),
            field("price", "Price", "Latest adjusted close."),
            field("change", "Change", "Latest adjusted close-to-close return."),
        ],
    },
];

const SIGNED_FIELDS = new Set([
    "change",
    "eps_growth_this_year",
    "eps_growth_next_year",
    "eps_growth_qoq",
    "eps_growth_ttm",
    "eps_growth_3yr",
    "eps_growth_5yr",
    "sales_growth_qoq",
    "sales_growth_ttm",
    "sales_growth_3yr",
    "sales_growth_5yr",
    "roa",
    "roe",
    "roic",
    "gross_margin",
    "operating_margin",
    "net_profit_margin",
    "sma20_distance",
    "sma50_distance",
    "sma200_distance",
    "performance_1w",
    "performance_1m",
    "performance_3m",
    "performance_6m",
    "performance_ytd",
    "performance_1yr",
    "performance_3yr",
    "performance_5yr",
    "performance_10yr",
    "dividend_growth_3yr",
    "dividend_growth_5yr",
]);

const currencySymbol = (currency: string | null) => currency === "USD" || !currency ? "$" : `${currency} `;

const formatNumber = (value: number, maximumFractionDigits = 2) => new Intl.NumberFormat("en-US", {
    maximumFractionDigits,
}).format(value);

const formatCompact = (value: number) => new Intl.NumberFormat("en-US", {
    notation: "compact",
    maximumFractionDigits: 2,
}).format(value);

export const formatSnapshotValue = (
    value: MarketSnapshotMetric["value"],
    unit: MarketSnapshotMetric["unit"],
    currency: string | null,
) => {
    if (value == null || value === "") return "—";
    if (Array.isArray(value)) return value.length ? value.join(" · ") : "—";
    if (typeof value === "string") return value;
    if (unit === "percent") return `${value >= 0 ? "" : "−"}${formatNumber(Math.abs(value) * 100, 2)}%`;
    if (unit === "multiple") return `${formatNumber(value, 2)}×`;
    if (unit === "ratio") return formatNumber(value, 2);
    if (unit === "integer") return formatCompact(value);
    if (unit === "currency") {
        const formatted = Math.abs(value) >= 1_000_000 ? formatCompact(Math.abs(value)) : formatNumber(Math.abs(value), 2);
        return `${value < 0 ? "−" : ""}${currencySymbol(currency)}${formatted}`;
    }
    return formatNumber(value, 2);
};

const formatSecondary = (metric: MarketSnapshotMetric, currency: string | null) => {
    if (metric.secondary_value == null || metric.secondary_unit == null) return null;
    return formatSnapshotValue(metric.secondary_value, metric.secondary_unit, currency);
};

const MetricRow = ({
    definition,
    metric,
    currency,
}: {
    definition: SnapshotField;
    metric?: MarketSnapshotMetric;
    currency: string | null;
}) => {
    const value = metric?.value ?? null;
    const numericValue = typeof value === "number" ? value : null;
    const signed = SIGNED_FIELDS.has(definition.key) && numericValue != null && numericValue !== 0;
    const valueClass = signed
        ? numericValue > 0
            ? "text-emerald-600 dark:text-emerald-400"
            : "text-rose-500 dark:text-rose-400"
        : "text-[var(--text)]";
    const tooltip = metric?.unavailable_reason || `${definition.description}${metric?.source_date ? ` Source date: ${metric.source_date}.` : ""}`;
    const secondary = metric ? formatSecondary(metric, currency) : null;

    return (
        <div className="grid min-h-9 grid-cols-[minmax(0,1fr)_auto] items-center gap-3 border-t px-3 py-2 first:border-t-0" data-testid={`snapshot-metric-${definition.key}`}>
            <span className="truncate text-xs font-semibold text-slate-500 dark:text-slate-400" title={tooltip} tabIndex={0}>
                {definition.label}
            </span>
            <div className="flex min-w-0 items-center justify-end gap-1.5 text-right">
                <span className={`font-mono text-xs font-black sm:text-[13px] ${valueClass}`} title={tooltip}>
                    {metric ? formatSnapshotValue(metric.value, metric.unit, currency) : "—"}
                </span>
                {secondary && <span className="font-mono text-[10px] text-slate-400">{secondary}</span>}
                {metric?.percentile != null && metric.percentile_scope && (
                    <span className="rounded border border-emerald-200 bg-emerald-50 px-1 py-0.5 text-[9px] font-black text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-300" title={`${metric.percentile_scope} desirability percentile`}>
                        P{Math.round(metric.percentile)}
                    </span>
                )}
            </div>
        </div>
    );
};

const SnapshotSkeleton = () => (
    <section className="surface-panel overflow-hidden" aria-label="Loading market snapshot">
        <div className="surface-subtle border-b px-5 py-4">
            <div className="h-4 w-36 animate-pulse rounded bg-slate-200 dark:bg-slate-700" />
        </div>
        <div className="grid gap-3 p-4 md:grid-cols-2 xl:grid-cols-3">
            {Array.from({ length: 6 }, (_, index) => <div key={index} className="h-64 animate-pulse rounded-xl bg-slate-100 dark:bg-slate-800" />)}
        </div>
    </section>
);

export default function StockSnapshotPanel({
    data,
    loading,
    error,
    onRetry,
}: {
    data: MarketSnapshotResponse | null;
    loading: boolean;
    error: string;
    onRetry: () => void;
}) {
    const [expanded, setExpanded] = useState<Set<string>>(() => new Set(["overview"]));
    const [isDesktop, setIsDesktop] = useState(false);

    useEffect(() => {
        if (typeof window.matchMedia !== "function") return;
        const media = window.matchMedia("(min-width: 768px)");
        const synchronize = () => {
            setIsDesktop(media.matches);
            setExpanded(
                media.matches ? new Set(GROUPS.map((group) => group.id)) : new Set(["overview"]),
            );
        };
        synchronize();
        media.addEventListener("change", synchronize);
        return () => media.removeEventListener("change", synchronize);
    }, []);

    if (loading && !data) return <SnapshotSkeleton />;
    if (error && !data) {
        return (
            <section className="surface-panel p-5" role="alert">
                <p className="eyebrow">Market Snapshot</p>
                <p className="mt-2 text-sm text-rose-500">{error}</p>
                <button type="button" className="secondary-button mt-4" onClick={onRetry}><RefreshCw size={14} /> Retry</button>
            </section>
        );
    }
    if (!data) return null;

    const toggle = (id: string) => setExpanded((current) => {
        const next = new Set(current);
        if (next.has(id)) next.delete(id); else next.add(id);
        return next;
    });
    const coveragePercent = Math.round(data.coverage.ratio * 100);
    const dates = [
        ["Price", data.source_dates.price],
        ["Screener", data.source_dates.screener],
        ["Financials", data.source_dates.financials],
    ].filter((item): item is [string, string] => Boolean(item[1]));

    return (
        <section className="surface-panel overflow-hidden" data-testid="market-snapshot-panel">
            <header className="surface-subtle flex flex-col gap-3 border-b px-5 py-4 lg:flex-row lg:items-center lg:justify-between">
                <div>
                    <p className="eyebrow">Fast fundamentals</p>
                    <div className="mt-1 flex flex-wrap items-center gap-2">
                        <h2 className="text-lg font-black tracking-[-0.02em]">Market Snapshot</h2>
                        <span className="status-pill">{data.coverage.available}/{data.coverage.total} · {coveragePercent}% covered</span>
                    </div>
                </div>
                <div className="flex flex-wrap gap-2 text-[11px] font-semibold text-slate-500 dark:text-slate-400">
                    {dates.map(([label, value]) => <span key={label} className="rounded-lg border bg-[var(--surface)] px-2.5 py-1">{label} {value}</span>)}
                </div>
            </header>
            {error && (
                <div className="mx-4 mt-4 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-300">
                    <span>{error} The previous snapshot remains visible.</span>
                    <button type="button" className="secondary-button" onClick={onRetry}><RefreshCw size={14} /> Retry</button>
                </div>
            )}
            <div className="grid items-start gap-3 p-3 sm:p-4 md:grid-cols-2 xl:grid-cols-3">
                {GROUPS.map((group) => {
                    const Icon = group.icon;
                    const isExpanded = expanded.has(group.id);
                    const isVisible = isDesktop || isExpanded;
                    return (
                        <article key={group.id} className="overflow-hidden rounded-xl border bg-[var(--surface)]">
                            <button
                                type="button"
                                className="surface-subtle flex w-full items-center justify-between gap-3 px-3 py-3 text-left disabled:cursor-default"
                                aria-expanded={isVisible}
                                disabled={isDesktop}
                                onClick={() => toggle(group.id)}
                            >
                                <span className="flex min-w-0 items-center gap-2.5">
                                    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-[var(--brand-soft)] text-[var(--brand-strong)]"><Icon size={16} /></span>
                                    <span className="min-w-0"><span className="eyebrow block truncate">{group.eyebrow}</span><span className="block truncate text-sm font-black">{group.label}</span></span>
                                </span>
                                <ChevronDown size={16} className={`shrink-0 transition-transform md:hidden ${isExpanded ? "rotate-180" : ""}`} />
                            </button>
                            <div className={isVisible ? "block" : "hidden"}>
                                {group.fields.map((definition) => (
                                    <MetricRow key={definition.key} definition={definition} metric={data.metrics[definition.key]} currency={data.currency} />
                                ))}
                            </div>
                        </article>
                    );
                })}
            </div>
        </section>
    );
}
