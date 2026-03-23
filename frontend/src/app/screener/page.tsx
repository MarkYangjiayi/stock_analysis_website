"use client";

import React, { useState, useEffect, useCallback, useRef } from "react";
import Link from 'next/link';
import debounce from 'lodash.debounce';
import { useAppStore } from '@/store/useAppStore';
import { API_BASE_URL } from '@/lib/api';
import { Download } from 'lucide-react';

export default function ScreenerPage() {
    const [activeTab, setActiveTab] = useState("Descriptive");
    const { filters, results, totalCount, page, setScreenerState } = useAppStore();

    const [loading, setLoading] = useState(false);
    const limit = 50;

    // Use a ref to track initial mount
    const isFirstRender = useRef(true);

    const tabs = ["Descriptive", "Fundamental", "Technical"];

    const buildApiPayload = () => {
        const payload: any = {
            limit,
            offset: page * limit,
            sort_by: filters.sort_by,
            sort_desc: filters.sort_desc === "desc",
        };

        if (filters.sector) payload.sector = filters.sector;

        if (filters.market_cap) {
            if (filters.market_cap === "mega") payload.market_cap_min = 200000000000;
            if (filters.market_cap === "large") { payload.market_cap_min = 10000000000; payload.market_cap_max = 200000000000; }
            if (filters.market_cap === "mid") { payload.market_cap_min = 2000000000; payload.market_cap_max = 10000000000; }
            if (filters.market_cap === "small") payload.market_cap_max = 2000000000;
        }

        if (filters.pe) {
            if (filters.pe === "under15") payload.pe_max = 15;
            if (filters.pe === "over50") payload.pe_min = 50;
        }

        if (filters.rsi) {
            if (filters.rsi === "oversold") payload.rsi_14_max = 30;
            if (filters.rsi === "overbought") payload.rsi_14_min = 70;
        }

        if (filters.price_ma50) {
            if (filters.price_ma50 === "above") payload.price_above_ma50 = true;
            if (filters.price_ma50 === "below") payload.price_below_ma50 = true;
        }

        if (filters.roe) {
            if (filters.roe === "over15") payload.roe_min = 0.15;
            if (filters.roe === "over30") payload.roe_min = 0.30;
        }

        if (filters.debt_to_equity) {
            if (filters.debt_to_equity === "under1") payload.debt_to_equity_max = 1.0;
            if (filters.debt_to_equity === "under05") payload.debt_to_equity_max = 0.5;
        }

        if (filters.fcf) {
            if (filters.fcf === "positive") payload.fcf_min = 0;
            if (filters.fcf === "high") payload.fcf_min = 1000000000;
        }

        if (filters.gross_margin) {
            if (filters.gross_margin === "over30") payload.gross_margin_min = 0.30;
            if (filters.gross_margin === "over50") payload.gross_margin_min = 0.50;
        }

        if (filters.sales_growth_5yr) {
            if (filters.sales_growth_5yr === "over10") payload.sales_growth_5yr_min = 0.10;
            if (filters.sales_growth_5yr === "over20") payload.sales_growth_5yr_min = 0.20;
        }

        return payload;
    };

    const fetchResults = async () => {
        setLoading(true);
        try {
            const payload = buildApiPayload();
            const res = await fetch(`${API_BASE_URL}/api/stocks/screener`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
            if (res.ok) {
                const data = await res.json();
                setScreenerState({ results: data.items || [], totalCount: data.total || 0 });
            } else {
                console.error("Failed to fetch screener results");
            }
        } catch (e) {
            console.error(e);
        } finally {
            setLoading(false);
        }
    };

    // Safe debounced fetch logic
    const debouncedFetch = useCallback(debounce(() => fetchResults(), 300), [filters, page]);

    useEffect(() => {
        // Condition: If this is the FIRST render and we already have cached results, DO NOT fetch.
        if (isFirstRender.current) {
            isFirstRender.current = false;
            if (results.length > 0) {
                return; // Use cache
            }
        }

        debouncedFetch();
        return () => debouncedFetch.cancel();
    }, [debouncedFetch, results.length]);

    const handleFilterChange = (key: string, value: string) => {
        setScreenerState({
            filters: { ...filters, [key]: value },
            page: 0 // Reset to page 0 on any filter change
        });
    };

    const handleNextPage = () => {
        if ((page + 1) * limit < totalCount) setScreenerState({ page: page + 1 });
    };

    const handlePrevPage = () => {
        if (page > 0) setScreenerState({ page: page - 1 });
    };

    const formatMarketCap = (value: any) => {
        if (value === null || value === undefined) return "-";
        const num = Number(value);
        if (isNaN(num)) return "-";
        if (num >= 1e12) return (num / 1e12).toFixed(2) + "T";
        if (num >= 1e9) return (num / 1e9).toFixed(2) + "B";
        if (num >= 1e6) return (num / 1e6).toFixed(2) + "M";
        return num.toLocaleString();
    };

    const formatPE = (value: any) => {
        if (value === null || value === undefined) return "-";
        const num = Number(value);
        if (isNaN(num) || num <= 0) return "-";
        return num.toFixed(2);
    };

    const handleExportCsv = () => {
        const headers = ['Ticker', 'Company', 'Sector', 'Market Cap', 'Price', 'P/E', 'P/B', 'RSI', 'ROE', 'D/E', 'Gross Margin', '5Y SG', 'FCF', 'Div Yield'];
        const rows = results.map((s: any) => [
            s.ticker, s.name ?? '', s.sector ?? '',
            s.market_cap ?? '', s.close ?? '',
            s.pe_ratio ?? '', s.pb_ratio ?? '', s.rsi_14 ?? '',
            s.roe != null ? (s.roe * 100).toFixed(2) : '',
            s.debt_to_equity ?? '',
            s.gross_margin != null ? (s.gross_margin * 100).toFixed(2) : '',
            s.sales_growth_5yr != null ? (s.sales_growth_5yr * 100).toFixed(2) : '',
            s.fcf ?? '',
            s.dividend_yield != null ? (s.dividend_yield * 100).toFixed(2) : '',
        ]);
        const csvContent = [headers, ...rows].map(r => r.map(String).join(',')).join('\n');
        const blob = new Blob([csvContent], { type: 'text/csv' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `screener_${new Date().toISOString().split('T')[0]}.csv`;
        a.click();
        URL.revokeObjectURL(url);
    };

    const totalPages = Math.ceil(totalCount / limit);

    const generatePagination = () => {
        if (totalPages <= 7) {
            return Array.from({ length: totalPages }, (_, i) => i);
        }

        if (page < 4) {
            return [0, 1, 2, 3, 4, '...', totalPages - 1];
        }

        if (page > totalPages - 5) {
            return [0, '...', totalPages - 5, totalPages - 4, totalPages - 3, totalPages - 2, totalPages - 1];
        }

        return [0, '...', page - 1, page, page + 1, '...', totalPages - 1];
    };

    return (
        <div className="h-full w-full overflow-y-auto bg-slate-50 dark:bg-[#0E1117] text-slate-900 dark:text-gray-100 p-6 md:p-8 font-sans selection:bg-emerald-500/30">
            <div className="max-w-7xl mx-auto space-y-8 animate-in fade-in slide-in-from-bottom-8 duration-700 ease-out fill-mode-both">

                {/* Header */}
                <div className="flex items-center justify-between border-b border-gray-200 dark:border-gray-800 pb-4">
                    <div className="flex items-center gap-4">
                        <Link href="/" className="text-slate-500 dark:text-gray-400 hover:text-slate-900 dark:hover:text-white transition-colors">
                            <span className="text-2xl font-black tracking-widest text-transparent bg-clip-text bg-gradient-to-r from-emerald-400 to-teal-200">
                                Quantify
                            </span>
                        </Link>
                        <span className="text-slate-600 dark:text-gray-600 text-xl font-light">/</span>
                        <h1 className="text-2xl font-bold tracking-wide text-slate-900 dark:text-white"><span className="text-emerald-400">Screener</span></h1>
                    </div>
                    <div className="flex items-center gap-3">
                        <div className="text-sm font-medium text-emerald-400/80 bg-emerald-500/10 px-3 py-1 rounded-full border border-emerald-500/20 shadow-[0_0_10px_rgba(16,185,129,0.1)]">
                            {loading ? "Scanning market..." : `Matches: ${totalCount.toLocaleString()}`}
                        </div>
                        {results.length > 0 && (
                            <button
                                onClick={handleExportCsv}
                                className="flex items-center gap-2 px-3 py-1.5 bg-white dark:bg-[#191D26] border border-gray-200 dark:border-gray-700 text-slate-600 dark:text-gray-300 text-sm font-semibold rounded-lg hover:border-emerald-500/50 hover:text-emerald-500 transition-colors shadow-sm"
                            >
                                <Download size={14} />
                                CSV
                            </button>
                        )}
                    </div>
                </div>

                {/* Filter Panel */}
                <div className="bg-white dark:bg-[#191D26] border border-gray-200 dark:border-gray-800 rounded-2xl overflow-hidden shadow-2xl">
                    {/* Tabs */}
                    <div className="flex border-b border-gray-200 dark:border-gray-800 bg-white dark:bg-[#151922]">
                        {tabs.map((tab) => (
                            <button
                                key={tab}
                                onClick={() => setActiveTab(tab)}
                                className={`px-6 py-4 text-sm font-bold tracking-wide transition-all ${activeTab === tab
                                    ? "text-emerald-400 border-b-2 border-emerald-500 bg-white dark:bg-[#191D26]"
                                    : "text-slate-500 dark:text-gray-400 hover:text-slate-900 dark:hover:text-gray-200 hover:bg-slate-100 dark:hover:bg-[#1E222D]"
                                    }`}
                            >
                                {tab}
                            </button>
                        ))}
                    </div>

                    {/* Tab Content */}
                    <div className="p-6">
                        <div className="grid grid-cols-1 md:grid-cols-4 gap-6">

                            {/* Common Controls (Always Visible) */}
                            <div className="space-y-1">
                                <label className="text-xs text-slate-500 dark:text-gray-500 uppercase font-semibold">Sort By</label>
                                <select
                                    value={filters.sort_by}
                                    onChange={(e) => handleFilterChange("sort_by", e.target.value)}
                                    className="w-full bg-white dark:bg-[#151922] border border-gray-200 dark:border-gray-700 text-sm rounded-xl px-4 py-3 text-slate-800 dark:text-gray-200 focus:outline-none focus:border-emerald-500/50 hover:border-gray-600 transition-colors shadow-inner"
                                >
                                    <option value="market_cap">Market Cap</option>
                                    <option value="pe_ratio">P/E Ratio</option>
                                    <option value="roe">ROE</option>
                                    <option value="debt_to_equity">Debt to Equity</option>
                                    <option value="sales_growth_5yr">5Yr Sales Gwth</option>
                                    <option value="gross_margin">Gross Margin</option>
                                    <option value="fcf">Free Cash Flow</option>
                                    <option value="volume">Volume</option>
                                    <option value="rsi_14">RSI (14)</option>
                                    <option value="close">Price</option>
                                </select>
                            </div>

                            <div className="space-y-1">
                                <label className="text-xs text-slate-500 dark:text-gray-500 uppercase font-semibold">Order</label>
                                <select
                                    value={filters.sort_desc}
                                    onChange={(e) => handleFilterChange("sort_desc", e.target.value)}
                                    className="w-full bg-white dark:bg-[#151922] border border-gray-200 dark:border-gray-700 text-sm rounded-xl px-4 py-3 text-slate-800 dark:text-gray-200 focus:outline-none focus:border-emerald-500/50 hover:border-gray-600 transition-colors shadow-inner"
                                >
                                    <option value="desc">Descending</option>
                                    <option value="asc">Ascending</option>
                                </select>
                            </div>

                            {/* Descriptive Tab */}
                            {activeTab === "Descriptive" && (
                                <>
                                    <div className="space-y-1">
                                        <label className="text-xs text-slate-500 dark:text-gray-500 uppercase font-semibold">Sector</label>
                                        <select
                                            value={filters.sector}
                                            onChange={(e) => handleFilterChange("sector", e.target.value)}
                                            className="w-full bg-white dark:bg-[#151922] border border-gray-200 dark:border-gray-700 text-sm rounded-xl px-4 py-3 text-slate-800 dark:text-gray-200 focus:outline-none focus:border-emerald-500/50 hover:border-gray-600 transition-colors shadow-inner"
                                        >
                                            <option value="">Any</option>
                                            <option value="Technology">Technology</option>
                                            <option value="Healthcare">Healthcare</option>
                                            <option value="Financial Services">Financial Services</option>
                                            <option value="Consumer Cyclical">Consumer Cyclical</option>
                                            <option value="Industrials">Industrials</option>
                                        </select>
                                    </div>
                                    <div className="space-y-1">
                                        <label className="text-xs text-slate-500 dark:text-gray-500 uppercase font-semibold">Market Cap</label>
                                        <select
                                            value={filters.market_cap}
                                            onChange={(e) => handleFilterChange("market_cap", e.target.value)}
                                            className="w-full bg-white dark:bg-[#151922] border border-gray-200 dark:border-gray-700 text-sm rounded-xl px-4 py-3 text-slate-800 dark:text-gray-200 focus:outline-none focus:border-emerald-500/50 hover:border-gray-600 transition-colors shadow-inner"
                                        >
                                            <option value="">Any</option>
                                            <option value="mega">Mega (&gt; $200B)</option>
                                            <option value="large">Large ($10B - $200B)</option>
                                            <option value="mid">Mid ($2B - $10B)</option>
                                            <option value="small">Small (&lt; $2B)</option>
                                        </select>
                                    </div>
                                </>
                            )}

                            {/* Fundamental Tab */}
                            {activeTab === "Fundamental" && (
                                <>
                                    <div className="space-y-1">
                                        <label className="text-xs text-slate-500 dark:text-gray-500 uppercase font-semibold">P/E Ratio</label>
                                        <select
                                            value={filters.pe}
                                            onChange={(e) => handleFilterChange("pe", e.target.value)}
                                            className="w-full bg-white dark:bg-[#151922] border border-gray-200 dark:border-gray-700 text-sm rounded-xl px-4 py-3 text-slate-800 dark:text-gray-200 focus:outline-none focus:border-emerald-500/50 hover:border-gray-600 transition-colors shadow-inner"
                                        >
                                            <option value="">Any</option>
                                            <option value="under15">Value (&lt; 15)</option>
                                            <option value="over50">Growth (&gt; 50)</option>
                                        </select>
                                    </div>
                                    <div className="space-y-1">
                                        <label className="text-xs text-slate-500 dark:text-gray-500 uppercase font-semibold">Return on Equity</label>
                                        <select
                                            value={filters.roe}
                                            onChange={(e) => handleFilterChange("roe", e.target.value)}
                                            className="w-full bg-white dark:bg-[#151922] border border-gray-200 dark:border-gray-700 text-sm rounded-xl px-4 py-3 text-slate-800 dark:text-gray-200 focus:outline-none focus:border-emerald-500/50 hover:border-gray-600 transition-colors shadow-inner"
                                        >
                                            <option value="">Any</option>
                                            <option value="over15">Good (&gt; 15%)</option>
                                            <option value="over30">Exceptional (&gt; 30%)</option>
                                        </select>
                                    </div>
                                    <div className="space-y-1">
                                        <label className="text-xs text-slate-500 dark:text-gray-500 uppercase font-semibold">Debt to Equity</label>
                                        <select
                                            value={filters.debt_to_equity}
                                            onChange={(e) => handleFilterChange("debt_to_equity", e.target.value)}
                                            className="w-full bg-white dark:bg-[#151922] border border-gray-200 dark:border-gray-700 text-sm rounded-xl px-4 py-3 text-slate-800 dark:text-gray-200 focus:outline-none focus:border-emerald-500/50 hover:border-gray-600 transition-colors shadow-inner"
                                        >
                                            <option value="">Any</option>
                                            <option value="under1">Healthy (&lt; 1.0)</option>
                                            <option value="under05">Conservative (&lt; 0.5)</option>
                                        </select>
                                    </div>
                                    <div className="space-y-1">
                                        <label className="text-xs text-slate-500 dark:text-gray-500 uppercase font-semibold">5Yr Sales Growth</label>
                                        <select
                                            value={filters.sales_growth_5yr}
                                            onChange={(e) => handleFilterChange("sales_growth_5yr", e.target.value)}
                                            className="w-full bg-white dark:bg-[#151922] border border-gray-200 dark:border-gray-700 text-sm rounded-xl px-4 py-3 text-slate-800 dark:text-gray-200 focus:outline-none focus:border-emerald-500/50 hover:border-gray-600 transition-colors shadow-inner"
                                        >
                                            <option value="">Any</option>
                                            <option value="over10">Growing (&gt; 10%)</option>
                                            <option value="over20">High Growth (&gt; 20%)</option>
                                        </select>
                                    </div>
                                    <div className="space-y-1">
                                        <label className="text-xs text-slate-500 dark:text-gray-500 uppercase font-semibold">Gross Margin</label>
                                        <select
                                            value={filters.gross_margin}
                                            onChange={(e) => handleFilterChange("gross_margin", e.target.value)}
                                            className="w-full bg-white dark:bg-[#151922] border border-gray-200 dark:border-gray-700 text-sm rounded-xl px-4 py-3 text-slate-800 dark:text-gray-200 focus:outline-none focus:border-emerald-500/50 hover:border-gray-600 transition-colors shadow-inner"
                                        >
                                            <option value="">Any</option>
                                            <option value="over30">Wide (&gt; 30%)</option>
                                            <option value="over50">Moat (&gt; 50%)</option>
                                        </select>
                                    </div>
                                    <div className="space-y-1">
                                        <label className="text-xs text-slate-500 dark:text-gray-500 uppercase font-semibold">FCF (Free Cash Flow)</label>
                                        <select
                                            value={filters.fcf}
                                            onChange={(e) => handleFilterChange("fcf", e.target.value)}
                                            className="w-full bg-white dark:bg-[#151922] border border-gray-200 dark:border-gray-700 text-sm rounded-xl px-4 py-3 text-slate-800 dark:text-gray-200 focus:outline-none focus:border-emerald-500/50 hover:border-gray-600 transition-colors shadow-inner"
                                        >
                                            <option value="">Any</option>
                                            <option value="positive">Positive (&gt; 0)</option>
                                            <option value="high">Cash Cow (&gt; 1B)</option>
                                        </select>
                                    </div>
                                </>
                            )}

                            {/* Technical Tab */}
                            {activeTab === "Technical" && (
                                <>
                                    <div className="space-y-1">
                                        <label className="text-xs text-slate-500 dark:text-gray-500 uppercase font-semibold">Price vs MA50</label>
                                        <select
                                            value={filters.price_ma50}
                                            onChange={(e) => handleFilterChange("price_ma50", e.target.value)}
                                            className="w-full bg-white dark:bg-[#151922] border border-gray-200 dark:border-gray-700 text-sm rounded-xl px-4 py-3 text-slate-800 dark:text-gray-200 focus:outline-none focus:border-emerald-500/50 hover:border-gray-600 transition-colors shadow-inner"
                                        >
                                            <option value="">Any</option>
                                            <option value="above">Price &gt; MA50 (Bullish)</option>
                                            <option value="below">Price &lt; MA50 (Bearish)</option>
                                        </select>
                                    </div>
                                    <div className="space-y-1">
                                        <label className="text-xs text-slate-500 dark:text-gray-500 uppercase font-semibold">RSI (14)</label>
                                        <select
                                            value={filters.rsi}
                                            onChange={(e) => handleFilterChange("rsi", e.target.value)}
                                            className="w-full bg-white dark:bg-[#151922] border border-gray-200 dark:border-gray-700 text-sm rounded-xl px-4 py-3 text-slate-800 dark:text-gray-200 focus:outline-none focus:border-emerald-500/50 hover:border-gray-600 transition-colors shadow-inner"
                                        >
                                            <option value="">Any</option>
                                            <option value="oversold">Oversold (&lt; 30)</option>
                                            <option value="overbought">Overbought (&gt; 70)</option>
                                        </select>
                                    </div>
                                </>
                            )}
                        </div>
                    </div>
                </div>

                {/* Results Table */}
                <div className="bg-white dark:bg-[#191D26] border border-gray-200 dark:border-gray-800 rounded-2xl overflow-hidden shadow-2xl">
                    <div className="overflow-x-auto">
                        <table className="w-full text-left text-sm whitespace-nowrap">
                            <thead className="bg-slate-50 dark:bg-[#141820] text-slate-500 dark:text-gray-400 font-medium text-xs uppercase tracking-wide">
                                <tr>
                                    <th className="px-4 py-3 rounded-tl-xl text-left">Ticker</th>
                                    <th className="px-4 py-3 text-left">Company</th>
                                    <th className="px-4 py-3 text-left">Sector</th>
                                    <th className="px-4 py-3 text-right">Mkt Cap</th>
                                    <th className="px-4 py-3 text-right">Price</th>
                                    <th className="px-4 py-3 text-right">P/E</th>
                                    <th className="px-4 py-3 text-right">P/B</th>
                                    <th className="px-4 py-3 text-right">RSI</th>
                                    <th className="px-4 py-3 text-right">ROE</th>
                                    <th className="px-4 py-3 text-right">D/E</th>
                                    <th className="px-4 py-3 text-right">Gr. Margin</th>
                                    <th className="px-4 py-3 text-right">5Y SG</th>
                                    <th className="px-4 py-3 text-right rounded-tr-xl">FCF</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-gray-200 dark:divide-gray-800">
                                {results.length === 0 ? (
                                    <tr>
                                        <td colSpan={13} className="px-6 py-12 text-center text-slate-500 dark:text-gray-500">
                                            {loading ? "Cruising the markets..." : "No stocks match your strict criteria."}
                                        </td>
                                    </tr>
                                ) : (
                                    results.map((stock: any) => (
                                        <tr key={stock.ticker} className="hover:bg-slate-100 dark:hover:bg-gray-800/40 border-b border-gray-200 dark:border-gray-800/50 transition-colors group">
                                            <td className="px-4 py-3 font-bold text-emerald-400 group-hover:text-emerald-300">
                                                <Link href={`/?ticker=${stock.ticker}`}>
                                                    {stock.ticker.split('.')[0]}
                                                </Link>
                                            </td>
                                            <td className="px-4 py-3 text-slate-700 dark:text-gray-300 truncate max-w-[160px] text-sm" title={stock.name}>{stock.name || "-"}</td>
                                            <td className="px-4 py-3 text-slate-500 dark:text-gray-400 truncate max-w-[100px]">
                                                <span className="bg-slate-200 dark:bg-[#2B2B43] px-2 py-0.5 rounded border border-gray-200 dark:border-gray-700 text-xs">
                                                    {stock.sector || "-"}
                                                </span>
                                            </td>
                                            <td className="px-4 py-3 text-right text-slate-800 dark:text-gray-200 font-medium text-sm">{formatMarketCap(stock.market_cap)}</td>
                                            <td className="px-4 py-3 text-right font-bold text-slate-900 dark:text-white">${stock.close?.toFixed(2) || "-"}</td>
                                            <td className="px-4 py-3 text-right text-slate-700 dark:text-gray-300 text-sm">{formatPE(stock.pe_ratio)}</td>
                                            <td className="px-4 py-3 text-right text-slate-700 dark:text-gray-300 text-sm">{stock.pb_ratio ? stock.pb_ratio.toFixed(1) + 'x' : "-"}</td>
                                            <td className={`px-4 py-3 text-right font-semibold text-sm ${stock.rsi_14 < 30 ? 'text-emerald-600 dark:text-emerald-400' : stock.rsi_14 > 70 ? 'text-rose-600 dark:text-rose-400' : 'text-slate-600 dark:text-gray-400'}`}>
                                                {stock.rsi_14 ? stock.rsi_14.toFixed(1) : "-"}
                                            </td>
                                            <td className={`px-4 py-3 text-right font-semibold text-sm ${stock.roe > 0.15 ? 'text-emerald-600 dark:text-emerald-400' : 'text-slate-500 dark:text-gray-400'}`}>
                                                {stock.roe ? `${(stock.roe * 100).toFixed(1)}%` : "-"}
                                            </td>
                                            <td className={`px-4 py-3 text-right font-semibold text-sm ${stock.debt_to_equity < 1.0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-500 dark:text-rose-400'}`}>
                                                {stock.debt_to_equity ? stock.debt_to_equity.toFixed(2) : "-"}
                                            </td>
                                            <td className="px-4 py-3 text-right text-slate-700 dark:text-gray-300 text-sm">
                                                {stock.gross_margin ? `${(stock.gross_margin * 100).toFixed(1)}%` : "-"}
                                            </td>
                                            <td className={`px-4 py-3 text-right font-semibold text-sm ${stock.sales_growth_5yr > 0.1 ? 'text-emerald-600 dark:text-emerald-400' : stock.sales_growth_5yr < 0 ? 'text-rose-500 dark:text-rose-400' : 'text-slate-600 dark:text-gray-400'}`}>
                                                {stock.sales_growth_5yr ? `${(stock.sales_growth_5yr * 100).toFixed(1)}%` : "-"}
                                            </td>
                                            <td className={`px-4 py-3 text-right font-semibold text-sm ${stock.fcf > 0 ? 'text-emerald-600 dark:text-emerald-400' : stock.fcf < 0 ? 'text-rose-500 dark:text-rose-400' : 'text-slate-500 dark:text-gray-500'}`}>
                                                {stock.fcf != null ? formatMarketCap(stock.fcf) : "-"}
                                            </td>
                                        </tr>
                                    ))
                                )}
                            </tbody>
                        </table>
                    </div>

                    {/* Pagination Footer */}
                    <div className="p-4 flex flex-col md:flex-row items-center justify-between gap-4 bg-slate-50 dark:bg-[#141820]">
                        <span className="text-slate-500 dark:text-gray-500 text-sm font-medium">
                            Showing {results.length > 0 ? page * limit + 1 : 0} to {Math.min((page + 1) * limit, totalCount)} of {totalCount}
                        </span>

                        {totalPages > 0 && (
                            <div className="flex items-center gap-1">
                                {/* Prev Button */}
                                <button
                                    onClick={handlePrevPage}
                                    disabled={page === 0 || loading}
                                    className="px-3 py-2 bg-white dark:bg-[#151922] text-slate-500 dark:text-gray-400 rounded-lg border border-gray-200 dark:border-gray-800 hover:border-emerald-500/50 hover:text-emerald-400 disabled:opacity-50 disabled:cursor-not-allowed transition-all"
                                >
                                    &lt;
                                </button>

                                {/* Page Numbers */}
                                {generatePagination().map((item, index) => {
                                    if (item === '...') {
                                        return (
                                            <span key={`ellipsis-${index}`} className="px-3 text-slate-500 dark:text-gray-500">
                                                ...
                                            </span>
                                        );
                                    }

                                    const pageNum = item as number;
                                    const isActive = pageNum === page;

                                    return (
                                        <button
                                            key={`page-${pageNum}`}
                                            onClick={() => setScreenerState({ page: pageNum })}
                                            className={`min-w-[40px] h-10 flex items-center justify-center rounded-lg border text-sm font-bold transition-all ${isActive
                                                ? 'bg-emerald-500 text-slate-900 dark:text-white border-emerald-400 shadow-[0_0_10px_rgba(16,185,129,0.3)]'
                                                : 'bg-white dark:bg-[#151922] text-slate-500 dark:text-gray-400 border-gray-200 dark:border-gray-800 hover:border-emerald-500/50 hover:text-emerald-400'
                                                }`}
                                        >
                                            {pageNum + 1}
                                        </button>
                                    );
                                })}

                                {/* Next Button */}
                                <button
                                    onClick={handleNextPage}
                                    disabled={page >= totalPages - 1 || loading}
                                    className="px-3 py-2 bg-white dark:bg-[#151922] text-slate-500 dark:text-gray-400 rounded-lg border border-gray-200 dark:border-gray-800 hover:border-emerald-500/50 hover:text-emerald-400 disabled:opacity-50 disabled:cursor-not-allowed transition-all"
                                >
                                    &gt;
                                </button>
                            </div>
                        )}
                    </div>
                </div>

            </div>
        </div>
    );
}
