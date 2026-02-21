"use client";

import React from 'react';
import { ValuationMetrics } from '@/lib/api';
import { TrendingUp, TrendingDown, DollarSign, Activity, Wallet, Percent, Scale } from 'lucide-react';

interface ValuationDashboardProps {
    metrics: ValuationMetrics;
}

const ValuationDashboard: React.FC<ValuationDashboardProps> = ({ metrics }) => {
    if (!metrics) return null;

    const { ttm, valuation } = metrics;

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
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 w-full">

            {/* 1. DCF 估值卡片 */}
            <div className="bg-[#151922] border border-gray-800 rounded-xl p-5 shadow-inner hover:border-gray-700 transition flex flex-col justify-between group">
                <div className="flex items-center gap-2 mb-3">
                    <div className="p-2 bg-blue-500/10 rounded-lg group-hover:bg-blue-500/20 transition">
                        <Scale className="text-blue-400" size={18} />
                    </div>
                    <h4 className="text-sm font-semibold text-gray-400 tracking-wide">DCF Valuation</h4>
                </div>
                <div>
                    <div className="flex items-baseline gap-2">
                        <span className="text-3xl font-black text-white">
                            <span className="text-lg text-gray-500 mr-1">$</span>
                            {valuation.dcf_intrinsic_value_per_share.toFixed(2)}
                        </span>
                    </div>
                    <p className="text-xs text-gray-500 mt-2 font-medium flex items-center gap-1">
                        Current Price: <span className={isUndervalued ? 'text-emerald-400/80 font-bold' : 'text-rose-400/80 font-bold'}>
                            ${valuation.current_price.toFixed(2)}
                        </span>
                    </p>
                </div>
            </div>

            {/* 2. 安全边际 (Margin of Safety) */}
            <div className="bg-[#151922] border border-gray-800 rounded-xl p-5 shadow-inner hover:border-gray-700 transition flex flex-col justify-between group">
                <div className="flex justify-between items-start mb-3">
                    <div className="flex items-center gap-2">
                        <div className={`p-2 rounded-lg transition ${valuation.margin_of_safety > 0 ? 'bg-emerald-500/10 group-hover:bg-emerald-500/20' : 'bg-rose-500/10 group-hover:bg-rose-500/20'}`}>
                            {valuation.margin_of_safety > 0 ? <TrendingUp className="text-emerald-400" size={18} /> : <TrendingDown className="text-rose-400" size={18} />}
                        </div>
                        <h4 className="text-sm font-semibold text-gray-400 tracking-wide">Margin of Safety</h4>
                    </div>
                </div>
                <div>
                    <span className={`text-4xl font-black ${mosColorClass} drop-shadow-md`}>
                        {(valuation.margin_of_safety * 100).toFixed(1)}%
                    </span>
                    {/* 简易水平进度指示条 */}
                    <div className="w-full h-1.5 bg-gray-800 rounded-full mt-3 overflow-hidden">
                        <div
                            className={`h-full ${valuation.margin_of_safety > 0 ? 'bg-gradient-to-r from-emerald-600 to-emerald-400' : 'bg-gradient-to-r from-rose-600 to-rose-400'}`}
                            style={{ width: `${Math.min(Math.max(Math.abs(valuation.margin_of_safety * 100), 5), 100)}%` }}
                        />
                    </div>
                </div>
            </div>

            {/* 3. 盈利能力 (Revenue & Net Income) */}
            <div className="bg-[#151922] border border-gray-800 rounded-xl p-5 shadow-inner hover:border-gray-700 transition flex flex-col justify-between group">
                <div className="flex items-center gap-2 mb-3">
                    <div className="p-2 bg-purple-500/10 rounded-lg group-hover:bg-purple-500/20 transition">
                        <DollarSign className="text-purple-400" size={18} />
                    </div>
                    <h4 className="text-sm font-semibold text-gray-400 tracking-wide">TTM Earnings</h4>
                </div>
                <div className="space-y-2">
                    <div className="flex justify-between items-end border-b border-gray-800/80 pb-1">
                        <span className="text-xs text-gray-500 font-medium">Revenue</span>
                        <span className="text-lg font-bold text-gray-200">{formatCompact(ttm.revenue)}</span>
                    </div>
                    <div className="flex justify-between items-end border-b border-gray-800/80 pb-1">
                        <span className="text-xs text-gray-500 font-medium">Net Income</span>
                        <span className={`text-lg font-bold ${ttm.net_income > 0 ? 'text-emerald-400' : 'text-rose-400'}`}>
                            {formatCompact(ttm.net_income)}
                        </span>
                    </div>
                </div>
            </div>

            {/* 4. ROE 与现金盈余 (Return on Equity & FCF) */}
            <div className="bg-[#151922] border border-gray-800 rounded-xl p-5 shadow-inner hover:border-gray-700 transition flex flex-col justify-between group">
                <div className="flex items-center gap-2 mb-3">
                    <div className="p-2 bg-amber-500/10 rounded-lg group-hover:bg-amber-500/20 transition">
                        <Activity className="text-amber-400" size={18} />
                    </div>
                    <h4 className="text-sm font-semibold text-gray-400 tracking-wide">Efficiency</h4>
                </div>
                <div className="space-y-2">
                    <div className="flex justify-between items-end border-b border-gray-800/80 pb-1">
                        <span className="text-xs text-gray-500 font-medium flex items-center gap-1">ROE <Percent size={10} /></span>
                        <span className={`text-lg font-bold ${roeColorClass}`}>
                            {(ttm.roe * 100).toFixed(2)}%
                        </span>
                    </div>
                    <div className="flex justify-between items-end border-b border-gray-800/80 pb-1">
                        <span className="text-xs text-gray-500 font-medium flex items-center gap-1">FCF <Wallet size={12} /></span>
                        <span className={`text-lg font-bold text-gray-200`}>
                            {formatCompact(ttm.free_cash_flow)}
                        </span>
                    </div>
                </div>
            </div>

        </div>
    );
};

export default ValuationDashboard;
