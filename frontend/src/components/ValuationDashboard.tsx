"use client";

import React from 'react';
import { ValuationMetrics } from '@/lib/api';
import { TrendingUp, TrendingDown, DollarSign, Activity, Wallet, Percent, Scale, Target, BarChart } from 'lucide-react';
import FactorRadarChart from './FactorRadarChart';

interface ValuationDashboardProps {
    metrics: ValuationMetrics;
}

const ValuationDashboard: React.FC<ValuationDashboardProps> = ({ metrics }) => {
    if (!metrics) return null;

    const { ttm, valuation, factor_scores } = metrics;

    const has52w = valuation.high_52w != null && valuation.low_52w != null;
    const rangePosition = has52w && valuation.high_52w !== valuation.low_52w
        ? Math.max(0, Math.min(100, ((valuation.current_price - valuation.low_52w!) / (valuation.high_52w! - valuation.low_52w!)) * 100))
        : null;

    // 美化数字格式化函数
    const formatCompact = (num: number) => {
        if (num >= 1e9) {
            return (num / 1e9).toFixed(2) + ' B';
        }
        if (num >= 1e6) {
            return (num / 1e6).toFixed(2) + ' M';
        }
        return num.toLocaleString(undefined, { maximumFractionDigits: 2 });
    };

    const isUndervalued = valuation.dcf_intrinsic_value_per_share > valuation.current_price;
    const mosColorClass = valuation.margin_of_safety > 0 ? 'text-emerald-400' : 'text-rose-400';
    const roeColorClass = ttm.roe > 0 ? 'text-emerald-400' : 'text-rose-400';

    return (
        <React.Fragment>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 w-full shrink-0">

                {/* 1. DCF 估值卡片 */}
                <div className="bg-white dark:bg-[#151922] border border-gray-200 dark:border-gray-800 rounded-xl p-5 shadow-sm dark:shadow-inner hover:border-emerald-500/30 transition-all flex flex-col justify-between group">
                    <div className="flex items-center gap-2 mb-3">
                        <div className="p-2 bg-blue-50 dark:bg-blue-500/10 rounded-lg group-hover:bg-blue-100 dark:group-hover:bg-blue-500/20 transition">
                            <Scale className="text-blue-600 dark:text-blue-400" size={18} />
                        </div>
                        <h4 className="text-sm font-semibold text-slate-500 dark:text-gray-400 tracking-wide">DCF Valuation</h4>
                    </div>
                    <div>
                        <div className="flex items-baseline gap-2">
                            <span className="text-3xl font-black text-slate-900 dark:text-white">
                                <span className="text-lg text-slate-400 dark:text-gray-500 mr-1">$</span>
                                {valuation.dcf_intrinsic_value_per_share.toFixed(2)}
                            </span>
                        </div>
                        <p className="text-xs text-slate-500 dark:text-gray-500 mt-2 font-medium flex items-center gap-1">
                            Current Price: <span className={isUndervalued ? 'text-emerald-500 dark:text-emerald-400/80 font-bold' : 'text-rose-500 dark:text-rose-400/80 font-bold'}>
                                ${valuation.current_price.toFixed(2)}
                            </span>
                        </p>
                    </div>
                </div>

                {/* 2. 安全边际 (Margin of Safety) */}
                <div className="bg-white dark:bg-[#151922] border border-gray-200 dark:border-gray-800 rounded-xl p-5 shadow-sm dark:shadow-inner hover:border-emerald-500/30 transition-all flex flex-col justify-between group">
                    <div className="flex justify-between items-start mb-3">
                        <div className="flex items-center gap-2">
                            <div className={`p-2 rounded-lg transition ${valuation.margin_of_safety > 0 ? 'bg-emerald-50 dark:bg-emerald-500/10 group-hover:bg-emerald-100 dark:group-hover:bg-emerald-500/20' : 'bg-rose-50 dark:bg-rose-500/10 group-hover:bg-rose-100 dark:group-hover:bg-rose-500/20'}`}>
                                {valuation.margin_of_safety > 0 ? <TrendingUp className="text-emerald-600 dark:text-emerald-400" size={18} /> : <TrendingDown className="text-rose-600 dark:text-rose-400" size={18} />}
                            </div>
                            <h4 className="text-sm font-semibold text-slate-500 dark:text-gray-400 tracking-wide">Margin of Safety</h4>
                        </div>
                    </div>
                    <div>
                        <span className={`text-4xl font-black ${mosColorClass} drop-shadow-sm dark:drop-shadow-md`}>
                            {(valuation.margin_of_safety * 100).toFixed(1)}%
                        </span>
                        {/* 简易水平进度指示条 */}
                        <div className="w-full h-1.5 bg-slate-100 dark:bg-gray-800 rounded-full mt-3 overflow-hidden">
                            <div
                                className={`h-full ${valuation.margin_of_safety > 0 ? 'bg-gradient-to-r from-emerald-500 to-emerald-400 dark:from-emerald-600 dark:to-emerald-400' : 'bg-gradient-to-r from-rose-500 to-rose-400 dark:from-rose-600 dark:to-rose-400'}`}
                                style={{ width: `${Math.min(Math.max(Math.abs(valuation.margin_of_safety * 100), 5), 100)}%` }}
                            />
                        </div>
                    </div>
                </div>

                {/* 3. 盈利能力 (Revenue & Net Income) */}
                <div className="bg-white dark:bg-[#151922] border border-gray-200 dark:border-gray-800 rounded-xl p-5 shadow-sm dark:shadow-inner hover:border-emerald-500/30 transition-all flex flex-col justify-between group">
                    <div className="flex items-center gap-2 mb-3">
                        <div className="p-2 bg-purple-50 dark:bg-purple-500/10 rounded-lg group-hover:bg-purple-100 dark:group-hover:bg-purple-500/20 transition">
                            <DollarSign className="text-purple-600 dark:text-purple-400" size={18} />
                        </div>
                        <h4 className="text-sm font-semibold text-slate-500 dark:text-gray-400 tracking-wide">TTM Earnings</h4>
                    </div>
                    <div className="space-y-2">
                        <div className="flex justify-between items-end border-b border-gray-100 dark:border-gray-800/80 pb-1">
                            <span className="text-xs text-slate-500 dark:text-gray-500 font-medium">Revenue</span>
                            <span className="text-lg font-bold text-slate-900 dark:text-gray-200">{formatCompact(ttm.revenue)}</span>
                        </div>
                        <div className="flex justify-between items-end border-b border-gray-100 dark:border-gray-800/80 pb-1">
                            <span className="text-xs text-slate-500 dark:text-gray-500 font-medium">Net Income</span>
                            <span className={`text-lg font-bold ${ttm.net_income > 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-600 dark:text-rose-400'}`}>
                                {formatCompact(ttm.net_income)}
                            </span>
                        </div>
                    </div>
                </div>

                {/* 4. ROE 与现金盈余 (Return on Equity & FCF) */}
                <div className="bg-white dark:bg-[#151922] border border-gray-200 dark:border-gray-800 rounded-xl p-5 shadow-sm dark:shadow-inner hover:border-emerald-500/30 transition-all flex flex-col justify-between group">
                    <div className="flex items-center gap-2 mb-3">
                        <div className="p-2 bg-amber-50 dark:bg-amber-500/10 rounded-lg group-hover:bg-amber-100 dark:group-hover:bg-amber-500/20 transition">
                            <Activity className="text-amber-500 dark:text-amber-400" size={18} />
                        </div>
                        <h4 className="text-sm font-semibold text-slate-500 dark:text-gray-400 tracking-wide">Efficiency</h4>
                    </div>
                    <div className="space-y-2">
                        <div className="flex justify-between items-end border-b border-gray-100 dark:border-gray-800/80 pb-1">
                            <span className="text-xs text-slate-500 dark:text-gray-500 font-medium flex items-center gap-1">ROE <Percent size={10} /></span>
                            <span className={`text-lg font-bold ${roeColorClass}`}>
                                {(ttm.roe * 100).toFixed(2)}%
                            </span>
                        </div>
                        <div className="flex justify-between items-end border-b border-gray-100 dark:border-gray-800/80 pb-1">
                            <span className="text-xs text-slate-500 dark:text-gray-500 font-medium flex items-center gap-1">FCF <Wallet size={12} /></span>
                            <span className={`text-lg font-bold text-slate-900 dark:text-gray-200`}>
                                {formatCompact(ttm.free_cash_flow)}
                            </span>
                        </div>
                    </div>
                </div>

                {/* 5. 52-Week Range */}
            {has52w && (
                <div className="bg-white dark:bg-[#151922] border border-gray-200 dark:border-gray-800 rounded-xl p-5 shadow-sm dark:shadow-inner hover:border-emerald-500/30 transition-all flex flex-col justify-between group">
                    <div className="flex items-center gap-2 mb-3">
                        <div className="p-2 bg-sky-50 dark:bg-sky-500/10 rounded-lg group-hover:bg-sky-100 dark:group-hover:bg-sky-500/20 transition">
                            <BarChart className="text-sky-600 dark:text-sky-400" size={18} />
                        </div>
                        <h4 className="text-sm font-semibold text-slate-500 dark:text-gray-400 tracking-wide">52-Week Range</h4>
                    </div>
                    <div className="space-y-3">
                        <div className="flex justify-between text-xs font-semibold">
                            <span className="text-rose-500 dark:text-rose-400">${valuation.low_52w!.toFixed(2)} L</span>
                            <span className="text-emerald-500 dark:text-emerald-400">H ${valuation.high_52w!.toFixed(2)}</span>
                        </div>
                        {/* Range bar */}
                        <div className="relative w-full h-2 bg-slate-100 dark:bg-gray-800 rounded-full overflow-visible">
                            <div
                                className="absolute inset-y-0 left-0 bg-gradient-to-r from-rose-400 via-amber-400 to-emerald-400 rounded-full"
                                style={{ width: '100%', opacity: 0.35 }}
                            />
                            {rangePosition != null && (
                                <div
                                    className="absolute top-1/2 -translate-y-1/2 w-3 h-3 bg-white dark:bg-gray-100 border-2 border-emerald-500 rounded-full shadow-md z-10"
                                    style={{ left: `calc(${rangePosition}% - 6px)` }}
                                />
                            )}
                        </div>
                        {valuation.pct_from_52w_high != null && (
                            <p className="text-xs text-slate-500 dark:text-gray-500 font-medium">
                                <span className={valuation.pct_from_52w_high >= 0 ? 'text-emerald-500' : 'text-rose-400'}>
                                    {(valuation.pct_from_52w_high * 100).toFixed(1)}%
                                </span> from 52W High
                            </p>
                        )}
                    </div>
                </div>
            )}

            </div>

            {/* 6. 多因子评分系统 (Multi-Factor Scoring) - 雷达图展示 */}

            {factor_scores && (
                <div className="flex-1 min-h-0 overflow-hidden flex flex-col bg-white dark:bg-[#151922] border border-gray-200 dark:border-gray-800 rounded-xl p-6 shadow-sm dark:shadow-inner hover:border-emerald-500/30 transition-all w-full">
                    <div className="flex items-center gap-2 mb-2">
                        <div className="p-2 bg-indigo-50 dark:bg-indigo-500/10 rounded-lg transition">
                            <Target className="text-indigo-600 dark:text-indigo-400" size={20} />
                        </div>
                        <h4 className="text-base font-bold text-slate-900 dark:text-gray-200 tracking-wide">Multi-Factor Scoring (0-100)</h4>
                    </div>

                    {/* 插入 ECharts 雷达图模块 */}
                    <div className="flex-1 min-h-[300px]">
                        <FactorRadarChart scores={factor_scores} />
                    </div>
                </div>
            )}
        </React.Fragment>
    );
};

export default ValuationDashboard;
