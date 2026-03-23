"use client";

import React, { useEffect, useState } from 'react';
import { PeerComparisonResponse, fetchPeerComparison } from '@/lib/api';
import { Users, TrendingUp, TrendingDown } from 'lucide-react';

interface PeerComparisonTableProps {
    ticker: string;
    onSelectTicker?: (ticker: string) => void;
}

const formatCompact = (n: number | null | undefined): string => {
    if (n == null) return '—';
    if (Math.abs(n) >= 1e12) return (n / 1e12).toFixed(1) + 'T';
    if (Math.abs(n) >= 1e9) return (n / 1e9).toFixed(1) + 'B';
    if (Math.abs(n) >= 1e6) return (n / 1e6).toFixed(1) + 'M';
    return n.toFixed(1);
};

const fmt1 = (n: number | null | undefined): string => n == null ? '—' : n.toFixed(1) + 'x';
const fmtPct = (n: number | null | undefined): string => n == null ? '—' : (n * 100).toFixed(1) + '%';
const fmtVal = (n: number | null | undefined): string => n == null ? '—' : `$${n.toFixed(2)}`;

type ColorFn = (val: number | null | undefined, median: number | null | undefined) => string;

// For PE/PB: lower is better
const colorLower: ColorFn = (val, median) => {
    if (val == null || median == null) return 'text-slate-600 dark:text-gray-400';
    if (val < median * 0.9) return 'text-emerald-600 dark:text-emerald-400';
    if (val > median * 1.1) return 'text-rose-600 dark:text-rose-400';
    return 'text-slate-600 dark:text-gray-400';
};

// For ROE/Gross Margin/Sales Growth: higher is better
const colorHigher: ColorFn = (val, median) => {
    if (val == null || median == null) return 'text-slate-600 dark:text-gray-400';
    if (val > median * 1.1) return 'text-emerald-600 dark:text-emerald-400';
    if (val < median * 0.9) return 'text-rose-600 dark:text-rose-400';
    return 'text-slate-600 dark:text-gray-400';
};

const PeerComparisonTable: React.FC<PeerComparisonTableProps> = ({ ticker, onSelectTicker }) => {
    const [data, setData] = useState<PeerComparisonResponse | null>(null);
    const [loading, setLoading] = useState(false);

    useEffect(() => {
        if (!ticker) return;
        setLoading(true);
        fetchPeerComparison(ticker)
            .then(setData)
            .catch(() => setData(null))
            .finally(() => setLoading(false));
    }, [ticker]);

    const medians = data?.industry_medians;

    return (
        <div className="bg-white dark:bg-[#191D26] border border-gray-200 dark:border-gray-800 rounded-2xl shadow-sm dark:shadow-inner overflow-hidden">
            <div className="p-4 border-b border-gray-200 dark:border-gray-800 bg-slate-50 dark:bg-[#141820] flex items-center gap-3">
                <div className="p-2 bg-blue-500/10 rounded-xl">
                    <Users className="text-blue-400" size={18} />
                </div>
                <div>
                    <h3 className="font-semibold text-slate-800 dark:text-gray-200 text-sm">Peer Comparison</h3>
                    {data?.sector && <p className="text-xs text-slate-500 dark:text-gray-500">{data.sector} sector</p>}
                </div>
            </div>

            {loading && (
                <div className="p-6 flex items-center justify-center gap-3 text-slate-500 dark:text-gray-500">
                    <div className="animate-spin h-4 w-4 border-2 border-emerald-500/30 border-t-emerald-500 rounded-full"></div>
                    <span className="text-sm">Loading peers...</span>
                </div>
            )}

            {!loading && !data && (
                <div className="p-6 text-center text-sm text-slate-400 dark:text-gray-600">
                    No peer data available for this ticker.
                </div>
            )}

            {!loading && data && data.peers.length > 0 && (
                <div className="overflow-x-auto">
                    <table className="w-full text-xs">
                        <thead>
                            <tr className="border-b border-gray-100 dark:border-gray-800 bg-slate-50 dark:bg-[#1a1f2e]">
                                <th className="text-left p-3 font-semibold text-slate-500 dark:text-gray-500 uppercase tracking-wide min-w-[120px]">Company</th>
                                <th className="text-right p-3 font-semibold text-slate-500 dark:text-gray-500 uppercase tracking-wide">Mkt Cap</th>
                                <th className="text-right p-3 font-semibold text-slate-500 dark:text-gray-500 uppercase tracking-wide">Price</th>
                                <th className="text-right p-3 font-semibold text-slate-500 dark:text-gray-500 uppercase tracking-wide">P/E</th>
                                <th className="text-right p-3 font-semibold text-slate-500 dark:text-gray-500 uppercase tracking-wide">P/B</th>
                                <th className="text-right p-3 font-semibold text-slate-500 dark:text-gray-500 uppercase tracking-wide">ROE</th>
                                <th className="text-right p-3 font-semibold text-slate-500 dark:text-gray-500 uppercase tracking-wide">Gr. Margin</th>
                                <th className="text-right p-3 font-semibold text-slate-500 dark:text-gray-500 uppercase tracking-wide">5Y SG</th>
                            </tr>
                        </thead>
                        <tbody>
                            {data.peers.map((peer) => {
                                const isCurrent = peer.is_current;
                                return (
                                    <tr
                                        key={peer.ticker}
                                        className={`border-b border-gray-50 dark:border-gray-800/50 transition-colors ${
                                            isCurrent
                                                ? 'bg-emerald-50 dark:bg-emerald-500/5 border-l-2 border-l-emerald-500'
                                                : 'hover:bg-slate-50 dark:hover:bg-gray-800/30'
                                        }`}
                                    >
                                        <td className="p-3">
                                            <button
                                                onClick={() => onSelectTicker?.(peer.ticker)}
                                                className="text-left group"
                                            >
                                                <div className={`font-bold ${isCurrent ? 'text-emerald-600 dark:text-emerald-400' : 'text-slate-800 dark:text-gray-200 group-hover:text-emerald-600 dark:group-hover:text-emerald-400'} transition-colors`}>
                                                    {peer.ticker}
                                                </div>
                                                <div className="text-slate-400 dark:text-gray-600 truncate max-w-[120px]">{peer.name}</div>
                                            </button>
                                        </td>
                                        <td className="p-3 text-right text-slate-600 dark:text-gray-400 font-medium">
                                            {formatCompact(peer.market_cap)}
                                        </td>
                                        <td className="p-3 text-right text-slate-800 dark:text-gray-200 font-semibold">
                                            {fmtVal(peer.close)}
                                        </td>
                                        <td className={`p-3 text-right font-semibold ${colorLower(peer.pe_ratio, medians?.pe_ratio)}`}>
                                            {fmt1(peer.pe_ratio)}
                                        </td>
                                        <td className={`p-3 text-right font-semibold ${colorLower(peer.pb_ratio, medians?.pb_ratio)}`}>
                                            {fmt1(peer.pb_ratio)}
                                        </td>
                                        <td className={`p-3 text-right font-semibold ${colorHigher(peer.roe, medians?.roe)}`}>
                                            {fmtPct(peer.roe)}
                                        </td>
                                        <td className={`p-3 text-right font-semibold ${colorHigher(peer.gross_margin, medians?.gross_margin)}`}>
                                            {fmtPct(peer.gross_margin)}
                                        </td>
                                        <td className={`p-3 text-right font-semibold ${colorHigher(peer.sales_growth_5yr, medians?.sales_growth_5yr)}`}>
                                            {fmtPct(peer.sales_growth_5yr)}
                                        </td>
                                    </tr>
                                );
                            })}

                            {/* Industry Median Row */}
                            {medians && (
                                <tr className="bg-slate-100 dark:bg-[#1a1f2e] border-t-2 border-gray-200 dark:border-gray-700">
                                    <td className="p-3">
                                        <div className="flex items-center gap-1.5">
                                            <TrendingUp size={12} className="text-slate-400 dark:text-gray-500" />
                                            <span className="font-bold text-slate-500 dark:text-gray-500 uppercase text-[10px] tracking-widest">Industry Median</span>
                                        </div>
                                    </td>
                                    <td className="p-3 text-right text-slate-500 dark:text-gray-500">—</td>
                                    <td className="p-3 text-right text-slate-500 dark:text-gray-500">—</td>
                                    <td className="p-3 text-right font-bold text-slate-600 dark:text-gray-400">{fmt1(medians.pe_ratio)}</td>
                                    <td className="p-3 text-right font-bold text-slate-600 dark:text-gray-400">{fmt1(medians.pb_ratio)}</td>
                                    <td className="p-3 text-right font-bold text-slate-600 dark:text-gray-400">{fmtPct(medians.roe)}</td>
                                    <td className="p-3 text-right font-bold text-slate-600 dark:text-gray-400">{fmtPct(medians.gross_margin)}</td>
                                    <td className="p-3 text-right font-bold text-slate-600 dark:text-gray-400">{fmtPct(medians.sales_growth_5yr)}</td>
                                </tr>
                            )}
                        </tbody>
                    </table>
                    <p className="p-2 text-[10px] text-slate-400 dark:text-gray-600 text-right">
                        <TrendingDown size={10} className="inline mr-1 text-rose-400" />Highlighted current stock · Green = better than median · Red = worse
                    </p>
                </div>
            )}
        </div>
    );
};

export default PeerComparisonTable;
