"use client";

import React, { useState, useMemo } from 'react';
import { DCFInputs } from '@/lib/api';
import { Calculator, ChevronDown, ChevronUp } from 'lucide-react';

interface DCFCalculatorProps {
    dcfInputs: DCFInputs;
    currentPrice: number;
}

// Replicate Python DCF formula
function calcDCF(ttm_fcf: number, cash: number, total_debt: number, shares: number, wacc: number, growthRate: number, terminalRate: number): number {
    if (ttm_fcf <= 0 || shares <= 0) return 0;
    const w = wacc / 100;
    const g = growthRate / 100;
    const t = terminalRate / 100;
    if (w <= t) return 0;

    let dcf5yr = 0;
    for (let i = 1; i <= 5; i++) {
        const projFcf = ttm_fcf * Math.pow(1 + g, i);
        dcf5yr += projFcf / Math.pow(1 + w, i);
    }
    const terminalValue = (ttm_fcf * Math.pow(1 + g, 5) * (1 + t)) / (w - t);
    const pvTv = terminalValue / Math.pow(1 + w, 5);
    const ev = dcf5yr + pvTv;
    const equityValue = ev + cash - total_debt;
    return equityValue / shares;
}

const PRESETS = {
    bear: { wacc: 11, growth: 5, terminal: 2 },
    base: { wacc: 9, growth: 10, terminal: 2.5 },
    bull: { wacc: 8, growth: 15, terminal: 3 },
};

const DCFCalculator: React.FC<DCFCalculatorProps> = ({ dcfInputs, currentPrice }) => {
    const [wacc, setWacc] = useState(9);
    const [growthRate, setGrowthRate] = useState(10);
    const [terminalRate, setTerminalRate] = useState(2.5);
    const [showSensitivity, setShowSensitivity] = useState(false);

    const intrinsicValue = useMemo(() =>
        calcDCF(dcfInputs.ttm_fcf, dcfInputs.cash, dcfInputs.total_debt, dcfInputs.shares_outstanding, wacc, growthRate, terminalRate),
        [dcfInputs, wacc, growthRate, terminalRate]
    );

    const marginOfSafety = intrinsicValue > 0 && currentPrice > 0
        ? (intrinsicValue - currentPrice) / intrinsicValue
        : 0;

    const isUndervalued = marginOfSafety > 0;

    // Sensitivity table: 5 WACC values x 5 growth rate values
    const waccRange = [wacc - 2, wacc - 1, wacc, wacc + 1, wacc + 2];
    const growthRange = [growthRate + 6, growthRate + 3, growthRate, growthRate - 3, growthRate - 6];

    const sensitivityTable = useMemo(() =>
        growthRange.map(g => waccRange.map(w =>
            calcDCF(dcfInputs.ttm_fcf, dcfInputs.cash, dcfInputs.total_debt, dcfInputs.shares_outstanding, w, g, terminalRate)
        )),
        // eslint-disable-next-line react-hooks/exhaustive-deps
        [dcfInputs, wacc, growthRate, terminalRate]
    );

    const applyPreset = (preset: keyof typeof PRESETS) => {
        setWacc(PRESETS[preset].wacc);
        setGrowthRate(PRESETS[preset].growth);
        setTerminalRate(PRESETS[preset].terminal);
    };

    const formatCompact = (n: number) => {
        if (Math.abs(n) >= 1e9) return (n / 1e9).toFixed(1) + 'B';
        if (Math.abs(n) >= 1e6) return (n / 1e6).toFixed(1) + 'M';
        return n.toFixed(0);
    };

    return (
        <div className="bg-white dark:bg-[#191D26] border border-gray-200 dark:border-gray-800 rounded-2xl p-5 shadow-sm dark:shadow-inner">
            <div className="flex items-center gap-2 mb-4">
                <div className="p-2 bg-violet-50 dark:bg-violet-500/10 rounded-xl">
                    <Calculator className="text-violet-600 dark:text-violet-400" size={18} />
                </div>
                <h4 className="text-sm font-bold text-slate-700 dark:text-gray-200 tracking-wide">Interactive DCF Model</h4>
                <div className="ml-auto flex gap-1.5">
                    {(['bear', 'base', 'bull'] as const).map(p => (
                        <button
                            key={p}
                            onClick={() => applyPreset(p)}
                            className={`px-2.5 py-1 text-xs font-bold rounded-md border transition-colors ${
                                p === 'bear' ? 'border-rose-500/40 text-rose-500 hover:bg-rose-500/10 dark:text-rose-400' :
                                p === 'bull' ? 'border-emerald-500/40 text-emerald-600 hover:bg-emerald-500/10 dark:text-emerald-400' :
                                'border-gray-300 dark:border-gray-600 text-slate-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800'
                            }`}
                        >
                            {p.charAt(0).toUpperCase() + p.slice(1)}
                        </button>
                    ))}
                </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-5">
                {/* WACC Slider */}
                <div>
                    <div className="flex justify-between items-center mb-1">
                        <label className="text-xs font-semibold text-slate-500 dark:text-gray-400 uppercase tracking-wide">WACC</label>
                        <span className="text-sm font-black text-slate-800 dark:text-gray-200">{wacc.toFixed(1)}%</span>
                    </div>
                    <input
                        type="range" min={5} max={15} step={0.5} value={wacc}
                        onChange={e => setWacc(parseFloat(e.target.value))}
                        className="w-full h-1.5 bg-slate-200 dark:bg-gray-700 rounded-full appearance-none cursor-pointer accent-violet-500"
                    />
                    <div className="flex justify-between text-[10px] text-slate-400 dark:text-gray-600 mt-0.5">
                        <span>5%</span><span>15%</span>
                    </div>
                </div>

                {/* FCF Growth Rate Slider */}
                <div>
                    <div className="flex justify-between items-center mb-1">
                        <label className="text-xs font-semibold text-slate-500 dark:text-gray-400 uppercase tracking-wide">FCF Growth (5Y)</label>
                        <span className="text-sm font-black text-slate-800 dark:text-gray-200">{growthRate.toFixed(1)}%</span>
                    </div>
                    <input
                        type="range" min={0} max={30} step={0.5} value={growthRate}
                        onChange={e => setGrowthRate(parseFloat(e.target.value))}
                        className="w-full h-1.5 bg-slate-200 dark:bg-gray-700 rounded-full appearance-none cursor-pointer accent-emerald-500"
                    />
                    <div className="flex justify-between text-[10px] text-slate-400 dark:text-gray-600 mt-0.5">
                        <span>0%</span><span>30%</span>
                    </div>
                </div>

                {/* Terminal Growth Slider */}
                <div>
                    <div className="flex justify-between items-center mb-1">
                        <label className="text-xs font-semibold text-slate-500 dark:text-gray-400 uppercase tracking-wide">Terminal Growth</label>
                        <span className="text-sm font-black text-slate-800 dark:text-gray-200">{terminalRate.toFixed(1)}%</span>
                    </div>
                    <input
                        type="range" min={1} max={4} step={0.1} value={terminalRate}
                        onChange={e => setTerminalRate(parseFloat(e.target.value))}
                        className="w-full h-1.5 bg-slate-200 dark:bg-gray-700 rounded-full appearance-none cursor-pointer accent-amber-500"
                    />
                    <div className="flex justify-between text-[10px] text-slate-400 dark:text-gray-600 mt-0.5">
                        <span>1%</span><span>4%</span>
                    </div>
                </div>
            </div>

            {/* Results */}
            <div className="grid grid-cols-3 gap-3 mb-4">
                <div className="bg-slate-50 dark:bg-[#151922] rounded-xl p-3 border border-gray-200 dark:border-gray-800 text-center">
                    <p className="text-xs text-slate-500 dark:text-gray-500 font-semibold mb-1">Intrinsic Value</p>
                    <p className="text-xl font-black text-slate-900 dark:text-white">
                        {intrinsicValue > 0 ? `$${intrinsicValue.toFixed(2)}` : 'N/A'}
                    </p>
                </div>
                <div className="bg-slate-50 dark:bg-[#151922] rounded-xl p-3 border border-gray-200 dark:border-gray-800 text-center">
                    <p className="text-xs text-slate-500 dark:text-gray-500 font-semibold mb-1">Current Price</p>
                    <p className="text-xl font-black text-slate-900 dark:text-white">${currentPrice.toFixed(2)}</p>
                </div>
                <div className={`rounded-xl p-3 border text-center ${isUndervalued ? 'bg-emerald-50 dark:bg-emerald-500/10 border-emerald-200 dark:border-emerald-500/30' : 'bg-rose-50 dark:bg-rose-500/10 border-rose-200 dark:border-rose-500/30'}`}>
                    <p className="text-xs text-slate-500 dark:text-gray-500 font-semibold mb-1">Margin of Safety</p>
                    <p className={`text-xl font-black ${isUndervalued ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-600 dark:text-rose-400'}`}>
                        {intrinsicValue > 0 ? `${(marginOfSafety * 100).toFixed(1)}%` : 'N/A'}
                    </p>
                </div>
            </div>

            {/* TTM FCF info */}
            <p className="text-xs text-slate-400 dark:text-gray-600 mb-4">
                Based on TTM FCF: <span className="font-bold text-slate-600 dark:text-gray-400">${formatCompact(dcfInputs.ttm_fcf)}</span> ·
                Cash: <span className="font-bold text-slate-600 dark:text-gray-400">${formatCompact(dcfInputs.cash)}</span> ·
                Debt: <span className="font-bold text-slate-600 dark:text-gray-400">${formatCompact(dcfInputs.total_debt)}</span>
            </p>

            {/* Sensitivity Table Toggle */}
            <button
                onClick={() => setShowSensitivity(!showSensitivity)}
                className="flex items-center gap-1.5 text-xs font-semibold text-violet-600 dark:text-violet-400 hover:text-violet-700 dark:hover:text-violet-300 transition-colors"
            >
                {showSensitivity ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                Sensitivity Analysis (WACC vs FCF Growth)
            </button>

            {showSensitivity && (
                <div className="mt-3 overflow-x-auto">
                    <table className="w-full text-xs border-collapse">
                        <thead>
                            <tr>
                                <th className="text-slate-400 dark:text-gray-600 p-1.5 text-left font-semibold border border-gray-100 dark:border-gray-800 bg-slate-50 dark:bg-[#1a1f2e]">
                                    G↓ / WACC→
                                </th>
                                {waccRange.map(w => (
                                    <th key={w} className={`p-1.5 text-center font-bold border border-gray-100 dark:border-gray-800 ${w === wacc ? 'bg-violet-100 dark:bg-violet-500/20 text-violet-700 dark:text-violet-300' : 'bg-slate-50 dark:bg-[#1a1f2e] text-slate-500 dark:text-gray-500'}`}>
                                        {w.toFixed(1)}%
                                    </th>
                                ))}
                            </tr>
                        </thead>
                        <tbody>
                            {sensitivityTable.map((row, ri) => (
                                <tr key={ri}>
                                    <td className={`p-1.5 text-center font-bold border border-gray-100 dark:border-gray-800 ${growthRange[ri] === growthRate ? 'bg-violet-100 dark:bg-violet-500/20 text-violet-700 dark:text-violet-300' : 'bg-slate-50 dark:bg-[#1a1f2e] text-slate-500 dark:text-gray-500'}`}>
                                        {growthRange[ri].toFixed(1)}%
                                    </td>
                                    {row.map((val, ci) => {
                                        const mos = val > 0 && currentPrice > 0 ? (val - currentPrice) / val : 0;
                                        const isActive = waccRange[ci] === wacc && growthRange[ri] === growthRate;
                                        return (
                                            <td
                                                key={ci}
                                                className={`p-1.5 text-center font-mono border border-gray-100 dark:border-gray-800 text-xs ${
                                                    isActive ? 'ring-2 ring-inset ring-violet-500 font-bold' : ''
                                                } ${
                                                    val > currentPrice ? 'bg-emerald-50 dark:bg-emerald-500/10 text-emerald-700 dark:text-emerald-400' :
                                                    val <= 0 ? 'bg-slate-100 dark:bg-gray-900 text-slate-400 dark:text-gray-600' :
                                                    'bg-rose-50 dark:bg-rose-500/10 text-rose-700 dark:text-rose-400'
                                                }`}
                                            >
                                                {val > 0 ? `$${val.toFixed(0)}` : '-'}
                                                {val > 0 && <span className="block text-[9px] opacity-60">{(mos * 100).toFixed(0)}%</span>}
                                            </td>
                                        );
                                    })}
                                </tr>
                            ))}
                        </tbody>
                    </table>
                    <p className="text-[10px] text-slate-400 dark:text-gray-600 mt-1">Green = undervalued vs current price. % shows margin of safety.</p>
                </div>
            )}
        </div>
    );
};

export default DCFCalculator;
