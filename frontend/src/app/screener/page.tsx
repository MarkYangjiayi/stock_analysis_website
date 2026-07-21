"use client";

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { CalendarDays, ChevronLeft, ChevronRight, FilterX, Loader2, SlidersHorizontal } from "lucide-react";
import { fetchScreener, ScreenerPayload, ScreenerResult } from "@/lib/api";
import { DEFAULT_SCREENER_FILTERS, ScreenerFilters, useAppStore } from "@/store/useAppStore";

const LIMIT = 50;
const FILTER_KEYS: Array<keyof ScreenerFilters> = [
    "sector", "market_cap", "pe", "rsi", "price_ma50", "roe",
    "debt_to_equity", "fcf", "gross_margin", "sales_growth_5yr",
    "sort_by", "sort_desc",
];

const SECTORS = [
    "Technology", "Healthcare", "Financial Services", "Consumer Cyclical",
    "Consumer Defensive", "Communication Services", "Industrials", "Energy",
    "Basic Materials", "Real Estate", "Utilities",
];

type TabName = "Overview" | "Fundamentals" | "Technical";

const selectClass = "control-field mt-1.5";

function FilterField({ label, value, onChange, children }: {
    label: string;
    value: string;
    onChange: (value: string) => void;
    children: React.ReactNode;
}) {
    return (
        <label className="block text-xs font-bold uppercase tracking-[0.1em] text-slate-500 dark:text-slate-400">
            {label}
            <select className={selectClass} value={value} onChange={(event) => onChange(event.target.value)}>
                {children}
            </select>
        </label>
    );
}

const formatMarketCap = (value: unknown) => {
    const num = Number(value);
    if (!Number.isFinite(num)) return "—";
    if (num >= 1e12) return `${(num / 1e12).toFixed(2)}T`;
    if (num >= 1e9) return `${(num / 1e9).toFixed(2)}B`;
    if (num >= 1e6) return `${(num / 1e6).toFixed(2)}M`;
    return num.toLocaleString();
};

const formatNumber = (value: unknown, digits = 2) => {
    const num = Number(value);
    return Number.isFinite(num) ? num.toFixed(digits) : "—";
};

const formatPercent = (value: unknown) => {
    const num = Number(value);
    return Number.isFinite(num) ? `${(num * 100).toFixed(1)}%` : "—";
};

function buildPayload(filters: ScreenerFilters, page: number): ScreenerPayload {
    const payload: ScreenerPayload = {
        limit: LIMIT,
        offset: page * LIMIT,
        sort_by: filters.sort_by,
        sort_desc: filters.sort_desc === "desc",
    };
    if (filters.sector) payload.sector = filters.sector;
    if (filters.market_cap === "mega") payload.market_cap_min = 200e9;
    if (filters.market_cap === "large") { payload.market_cap_min = 10e9; payload.market_cap_max = 200e9; }
    if (filters.market_cap === "mid") { payload.market_cap_min = 2e9; payload.market_cap_max = 10e9; }
    if (filters.market_cap === "small") payload.market_cap_max = 2e9;
    if (filters.pe === "under15") payload.pe_max = 15;
    if (filters.pe === "over50") payload.pe_min = 50;
    if (filters.rsi === "oversold") payload.rsi_14_max = 30;
    if (filters.rsi === "overbought") payload.rsi_14_min = 70;
    if (filters.price_ma50 === "above") payload.price_above_ma50 = true;
    if (filters.price_ma50 === "below") payload.price_below_ma50 = true;
    if (filters.roe === "over15") payload.roe_min = 0.15;
    if (filters.roe === "over30") payload.roe_min = 0.30;
    if (filters.debt_to_equity === "under1") payload.debt_to_equity_max = 1;
    if (filters.debt_to_equity === "under05") payload.debt_to_equity_max = 0.5;
    if (filters.fcf === "positive") payload.fcf_min = 0;
    if (filters.fcf === "high") payload.fcf_min = 1e9;
    if (filters.gross_margin === "over30") payload.gross_margin_min = 0.30;
    if (filters.gross_margin === "over50") payload.gross_margin_min = 0.50;
    if (filters.sales_growth_5yr === "over10") payload.sales_growth_5yr_min = 0.10;
    if (filters.sales_growth_5yr === "over20") payload.sales_growth_5yr_min = 0.20;
    return payload;
}

function StockCard({ stock }: { stock: ScreenerResult }) {
    return (
        <Link href={`/?ticker=${encodeURIComponent(stock.ticker)}`} className="block rounded-xl border bg-white p-4 transition-colors hover:border-emerald-400 dark:bg-[#121920]">
            <div className="flex items-start justify-between gap-4">
                <div className="min-w-0">
                    <div className="flex items-center gap-2">
                        <span className="font-mono text-sm font-black text-emerald-600 dark:text-emerald-400">{stock.ticker.replace(".US", "")}</span>
                        {stock.sector && <span className="truncate rounded-md bg-slate-100 px-2 py-0.5 text-[11px] text-slate-500 dark:bg-slate-800 dark:text-slate-400">{stock.sector}</span>}
                    </div>
                    <p className="mt-1 truncate text-sm font-semibold text-slate-800 dark:text-slate-100">{stock.name || "Unnamed security"}</p>
                </div>
                <div className="shrink-0 text-right">
                    <p className="font-mono text-base font-black text-slate-900 dark:text-white">{stock.close == null ? "—" : `$${stock.close.toFixed(2)}`}</p>
                    <p className="mt-0.5 text-xs text-slate-500">{formatMarketCap(stock.market_cap)}</p>
                </div>
            </div>
            <dl className="mt-4 grid grid-cols-4 gap-2 border-t pt-3 text-center">
                {[["P/E", formatNumber(stock.pe_ratio)], ["ROE", formatPercent(stock.roe)], ["D/E", formatNumber(stock.debt_to_equity)], ["5Y growth", formatPercent(stock.sales_growth_5yr)]].map(([label, value]) => (
                    <div key={label}>
                        <dt className="text-[10px] uppercase tracking-wide text-slate-400">{label}</dt>
                        <dd className="mt-1 text-xs font-bold text-slate-700 dark:text-slate-200">{value}</dd>
                    </div>
                ))}
            </dl>
        </Link>
    );
}

function ScreenerContent() {
    const searchParams = useSearchParams();
    const router = useRouter();
    const pathname = usePathname();
    const { filters, results, totalCount, page, asOfDate, setScreenerState } = useAppStore();
    const [activeTab, setActiveTab] = useState<TabName>("Overview");
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [ready, setReady] = useState(false);
    const initialized = useRef(false);

    useEffect(() => {
        if (initialized.current) return;
        initialized.current = true;
        const nextFilters = { ...filters };
        let hasUrlState = false;
        for (const key of FILTER_KEYS) {
            const value = searchParams.get(key);
            if (value !== null) {
                nextFilters[key] = value;
                hasUrlState = true;
            }
        }
        const requestedPage = Number(searchParams.get("page"));
        setScreenerState({
            ...(hasUrlState ? { filters: nextFilters } : {}),
            ...(Number.isInteger(requestedPage) && requestedPage > 0 ? { page: requestedPage - 1 } : {}),
        });
        setReady(true);
    }, [filters, searchParams, setScreenerState]);

    useEffect(() => {
        if (!ready) return;
        const params = new URLSearchParams();
        for (const key of FILTER_KEYS) {
            const value = filters[key];
            if (value && value !== DEFAULT_SCREENER_FILTERS[key]) params.set(key, value);
        }
        if (page > 0) params.set("page", String(page + 1));
        const query = params.toString();
        router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
    }, [filters, page, pathname, ready, router]);

    const payload = useMemo(() => buildPayload(filters, page), [filters, page]);

    useEffect(() => {
        if (!ready) return;
        const controller = new AbortController();
        const timeout = window.setTimeout(async () => {
            setLoading(true);
            setError("");
            try {
                const data = await fetchScreener(payload, controller.signal);
                setScreenerState({
                    results: data.items,
                    totalCount: data.total,
                    asOfDate: data.as_of_date,
                });
            } catch (caught) {
                if (caught instanceof DOMException && caught.name === "AbortError") return;
                setError(caught instanceof Error ? caught.message : "Unable to load the market snapshot.");
            } finally {
                if (!controller.signal.aborted) setLoading(false);
            }
        }, 250);
        return () => {
            window.clearTimeout(timeout);
            controller.abort();
        };
    }, [payload, ready, setScreenerState]);

    const handleFilterChange = useCallback((key: keyof ScreenerFilters, value: string) => {
        setLoading(true);
        setScreenerState({ filters: { ...filters, [key]: value }, page: 0 });
    }, [filters, setScreenerState]);

    const resetFilters = () => {
        setLoading(true);
        setScreenerState({ filters: { ...DEFAULT_SCREENER_FILTERS }, page: 0 });
    };
    const totalPages = Math.ceil(totalCount / LIMIT);
    const initialLoading = loading && totalCount === 0 && results.length === 0;

    return (
        <div className="app-page">
            <div className="page-container">
                <header className="flex flex-col justify-between gap-4 border-b pb-5 sm:flex-row sm:items-end">
                    <div>
                        <p className="eyebrow">Published market snapshot</p>
                        <h1 className="page-title mt-1">Equity Screener</h1>
                        <p className="page-description">Filter the quality-gated US equity universe across size, fundamentals, and technical conditions.</p>
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                        {asOfDate && <span className="status-pill"><CalendarDays size={13} /> Data as of {asOfDate}</span>}
                        <span className="rounded-full border bg-white px-3 py-1 text-xs font-bold text-slate-600 dark:bg-slate-900 dark:text-slate-300">
                            {loading ? (asOfDate ? "Refreshing…" : "Loading snapshot…") : `${totalCount.toLocaleString()} matches`}
                        </span>
                    </div>
                </header>

                <section className="surface-panel overflow-hidden" aria-label="Screener filters">
                    <div className="flex items-center justify-between border-b px-3 sm:px-5">
                        <div className="flex min-w-0 overflow-x-auto" role="tablist" aria-label="Filter categories">
                            {(["Overview", "Fundamentals", "Technical"] as TabName[]).map((tab) => (
                                <button
                                    key={tab}
                                    type="button"
                                    role="tab"
                                    aria-selected={activeTab === tab}
                                    onClick={() => setActiveTab(tab)}
                                    className={`shrink-0 border-b-2 px-3 py-4 text-sm font-bold sm:px-4 ${activeTab === tab ? "border-emerald-500 text-emerald-700 dark:text-emerald-300" : "border-transparent text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-100"}`}
                                >
                                    {tab}
                                </button>
                            ))}
                        </div>
                        <button type="button" onClick={resetFilters} className="secondary-button ml-1 min-h-9 shrink-0 px-2 py-1.5 sm:ml-3 sm:px-3" title="Reset all filters">
                            <FilterX size={15} /><span className="hidden sm:inline">Reset</span>
                        </button>
                    </div>

                    <div className="grid gap-4 p-4 sm:grid-cols-2 sm:p-5 lg:grid-cols-4">
                        <FilterField label="Sort by" value={filters.sort_by} onChange={(value) => handleFilterChange("sort_by", value)}>
                            <option value="market_cap">Market cap</option><option value="pe_ratio">P/E ratio</option><option value="roe">ROE</option><option value="debt_to_equity">Debt to equity</option><option value="sales_growth_5yr">5Y sales growth</option><option value="gross_margin">Gross margin</option><option value="fcf">Free cash flow</option><option value="volume">Volume</option><option value="rsi_14">RSI (14)</option><option value="close">Price</option>
                        </FilterField>
                        <FilterField label="Order" value={filters.sort_desc} onChange={(value) => handleFilterChange("sort_desc", value)}>
                            <option value="desc">Descending</option><option value="asc">Ascending</option>
                        </FilterField>

                        {activeTab === "Overview" && <>
                            <FilterField label="Sector" value={filters.sector} onChange={(value) => handleFilterChange("sector", value)}>
                                <option value="">All sectors</option>{SECTORS.map((sector) => <option key={sector} value={sector}>{sector}</option>)}
                            </FilterField>
                            <FilterField label="Market cap" value={filters.market_cap} onChange={(value) => handleFilterChange("market_cap", value)}>
                                <option value="">Any size</option><option value="mega">Mega (&gt; $200B)</option><option value="large">Large ($10B–$200B)</option><option value="mid">Mid ($2B–$10B)</option><option value="small">Small (&lt; $2B)</option>
                            </FilterField>
                        </>}

                        {activeTab === "Fundamentals" && <>
                            <FilterField label="P/E ratio" value={filters.pe} onChange={(value) => handleFilterChange("pe", value)}><option value="">Any</option><option value="under15">Under 15</option><option value="over50">Over 50</option></FilterField>
                            <FilterField label="Return on equity" value={filters.roe} onChange={(value) => handleFilterChange("roe", value)}><option value="">Any</option><option value="over15">Over 15%</option><option value="over30">Over 30%</option></FilterField>
                            <FilterField label="Debt to equity" value={filters.debt_to_equity} onChange={(value) => handleFilterChange("debt_to_equity", value)}><option value="">Any</option><option value="under1">Under 1.0</option><option value="under05">Under 0.5</option></FilterField>
                            <FilterField label="Free cash flow" value={filters.fcf} onChange={(value) => handleFilterChange("fcf", value)}><option value="">Any</option><option value="positive">Positive</option><option value="high">Over $1B</option></FilterField>
                            <FilterField label="Gross margin" value={filters.gross_margin} onChange={(value) => handleFilterChange("gross_margin", value)}><option value="">Any</option><option value="over30">Over 30%</option><option value="over50">Over 50%</option></FilterField>
                            <FilterField label="5Y sales growth" value={filters.sales_growth_5yr} onChange={(value) => handleFilterChange("sales_growth_5yr", value)}><option value="">Any</option><option value="over10">Over 10%</option><option value="over20">Over 20%</option></FilterField>
                        </>}

                        {activeTab === "Technical" && <>
                            <FilterField label="Price vs MA50" value={filters.price_ma50} onChange={(value) => handleFilterChange("price_ma50", value)}><option value="">Any</option><option value="above">Above MA50</option><option value="below">Below MA50</option></FilterField>
                            <FilterField label="RSI (14)" value={filters.rsi} onChange={(value) => handleFilterChange("rsi", value)}><option value="">Any</option><option value="oversold">Oversold (&lt; 30)</option><option value="overbought">Overbought (&gt; 70)</option></FilterField>
                        </>}
                    </div>
                </section>

                {error && <div className="error-panel" role="alert">{error} Previous results remain visible.</div>}

                <section className="surface-panel overflow-hidden" aria-busy={loading}>
                    <div className="flex items-center justify-between border-b px-4 py-3 lg:hidden">
                        <span className="flex items-center gap-2 text-sm font-bold"><SlidersHorizontal size={15} /> Results</span>
                        {loading && <Loader2 className="animate-spin text-emerald-500" size={16} />}
                    </div>

                    <div className="grid gap-3 p-3 lg:hidden">
                        {!loading && results.length === 0
                            ? <p className="py-12 text-center text-sm text-slate-500">No securities match these filters.</p>
                            : results.map((stock) => <StockCard key={stock.ticker} stock={stock} />)}
                    </div>

                    <div className="hidden overflow-x-auto lg:block">
                        <table className="w-full whitespace-nowrap text-left text-sm">
                            <thead className="surface-subtle text-xs font-bold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                                <tr><th className="px-5 py-4">Ticker</th><th className="px-5 py-4">Company</th><th className="px-5 py-4">Sector</th><th className="px-5 py-4 text-right">Market cap</th><th className="px-5 py-4 text-right">Price</th><th className="px-5 py-4 text-right">P/E</th><th className="px-5 py-4 text-right">ROE</th><th className="px-5 py-4 text-right">D/E</th><th className="px-5 py-4 text-right">Gross margin</th><th className="px-5 py-4 text-right">5Y growth</th></tr>
                            </thead>
                            <tbody className="divide-y">
                                {results.length === 0 ? <tr><td colSpan={10} className="px-6 py-16 text-center text-slate-500">{loading ? "Loading the published market snapshot…" : "No securities match these filters."}</td></tr> : results.map((stock) => (
                                    <tr key={stock.ticker} className="transition-colors hover:bg-slate-50 dark:hover:bg-slate-800/40">
                                        <td className="px-5 py-3.5"><Link href={`/?ticker=${encodeURIComponent(stock.ticker)}`} className="font-mono font-black text-emerald-600 hover:underline dark:text-emerald-400">{stock.ticker.replace(".US", "")}</Link></td>
                                        <td className="max-w-[220px] truncate px-5 py-3.5 font-semibold" title={stock.name || undefined}>{stock.name || "—"}</td>
                                        <td className="max-w-[150px] truncate px-5 py-3.5 text-slate-500 dark:text-slate-400">{stock.sector || "—"}</td>
                                        <td className="px-5 py-3.5 text-right font-mono">{formatMarketCap(stock.market_cap)}</td>
                                        <td className="px-5 py-3.5 text-right font-mono font-bold">{stock.close == null ? "—" : `$${stock.close.toFixed(2)}`}</td>
                                        <td className="px-5 py-3.5 text-right font-mono">{formatNumber(stock.pe_ratio)}</td>
                                        <td className="px-5 py-3.5 text-right font-mono">{formatPercent(stock.roe)}</td>
                                        <td className="px-5 py-3.5 text-right font-mono">{formatNumber(stock.debt_to_equity)}</td>
                                        <td className="px-5 py-3.5 text-right font-mono">{formatPercent(stock.gross_margin)}</td>
                                        <td className="px-5 py-3.5 text-right font-mono">{formatPercent(stock.sales_growth_5yr)}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>

                    <footer className="surface-subtle flex flex-col gap-3 border-t px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
                        <p className="text-xs text-slate-500 dark:text-slate-400">
                            {initialLoading ? "Loading published snapshot…" : totalCount > 0 ? `${(page * LIMIT + 1).toLocaleString()}–${Math.min((page + 1) * LIMIT, totalCount).toLocaleString()} of ${totalCount.toLocaleString()}` : "No results"}
                        </p>
                        <div className="flex items-center justify-between gap-2 sm:justify-end">
                            <button type="button" className="secondary-button min-h-9 px-3 py-1.5" disabled={page === 0 || loading} onClick={() => { setLoading(true); setScreenerState({ page: page - 1 }); }}><ChevronLeft size={16} /> Previous</button>
                            <span className="px-2 text-xs font-semibold text-slate-500">{initialLoading ? "Loading…" : `Page ${totalPages ? page + 1 : 0} of ${totalPages}`}</span>
                            <button type="button" className="secondary-button min-h-9 px-3 py-1.5" disabled={page >= totalPages - 1 || loading} onClick={() => { setLoading(true); setScreenerState({ page: page + 1 }); }}>Next <ChevronRight size={16} /></button>
                        </div>
                    </footer>
                </section>
            </div>
        </div>
    );
}

export default function ScreenerPage() {
    return (
        <Suspense fallback={<div className="app-page"><div className="page-container"><div className="surface-panel h-[560px] animate-pulse" /></div></div>}>
            <ScreenerContent />
        </Suspense>
    );
}
