"use client";

import React, { useState, useEffect } from 'react';
import RRGChart, { RRGResponse } from '@/components/RRGChart';
import { Loader2 } from 'lucide-react';

export default function RealRRGWidget() {
    const [rrgData, setRrgData] = useState<RRGResponse | null>(null);
    const [loading, setLoading] = useState<boolean>(true);
    const [error, setError] = useState<string | null>(null);

    // 初始化拖尾长度，UI 拖拉拽可控，默认设为 14
    const [tailLength, setTailLength] = useState<number>(14);

    useEffect(() => {
        const fetchRRG = async () => {
            try {
                setLoading(true);
                setError(null);

                // 拼接真实的 FastAPI 端点进行请求
                // 改为 11 大行业板块 ETF 代码，并且一次性要求后端返回 tail_length=40 的大包数据给前端做缓存
                const endpoint = "http://127.0.0.1:8000/api/v1/rrg?tickers=XLK.US,XLF.US,XLV.US,XLY.US,XLP.US,XLE.US,XLI.US,XLB.US,XLU.US,XLRE.US,XLC.US&benchmark=SPY&tail_length=40";

                const response = await fetch(endpoint);

                if (!response.ok) {
                    throw new Error(`API returned status ${response.status}`);
                }

                const data: RRGResponse = await response.json();
                setRrgData(data);

            } catch (err: any) {
                console.error("Failed to fetch RRG data:", err);
                setError(err.message || "Unknown error occurred while fetching data.");
            } finally {
                setLoading(false);
            }
        };

        fetchRRG();
    }, []);

    return (
        <div className="min-h-screen bg-slate-950 p-8 text-white font-sans flex flex-col items-center">
            <div className="w-full max-w-7xl">
                <div className="flex items-center justify-between mb-8 border-b border-slate-800 pb-4">
                    <h1 className="text-3xl font-extrabold tracking-wide">
                        US Sector Rotation (RRG)
                    </h1>
                    {rrgData?.update_time && (
                        <span className="text-slate-400 text-sm">
                            Last Updated: {new Date(rrgData.update_time).toLocaleString()}
                        </span>
                    )}
                </div>

                {/* 增加基于 Range 控制的面板 UI */}
                <div className="bg-slate-900/50 p-6 rounded-xl border border-slate-800 backdrop-blur-md mb-6 flex flex-col gap-4 shadow-lg">
                    <div className="flex items-center justify-between">
                        <label htmlFor="tail-slider" className="text-slate-200 font-semibold tracking-wide">
                            拖尾长度 (Tail Length): <span className="text-blue-400 text-lg ml-2">{tailLength} 天</span>
                        </label>
                        <span className="text-xs text-slate-500 bg-slate-800 px-2 py-1 rounded-md">
                            缓存长度: {rrgData ? 40 : '--'} 天
                        </span>
                    </div>
                    {/* 滑动条 */}
                    <input
                        id="tail-slider"
                        type="range"
                        min={3}
                        max={30}
                        step={1}
                        value={tailLength}
                        onChange={(e) => setTailLength(Number(e.target.value))}
                        className="w-full h-2 bg-slate-800 rounded-lg appearance-none cursor-pointer accent-blue-500 hover:accent-blue-400 transition-all border border-slate-700"
                    />
                </div>

                {/* 错误态处理 */}
                {error && (
                    <div className="w-full bg-red-950/40 border border-red-800/50 rounded-lg p-6 mb-6">
                        <h3 className="text-xl font-bold text-red-500 mb-2">Failed to load chart data</h3>
                        <p className="text-red-300 font-mono text-sm">{error}</p>
                    </div>
                )}

                {/* 载入态处理: 暗黑主题带旋转 SVG */}
                {loading && (
                    <div className="w-full h-[600px] bg-slate-900/50 rounded-xl shadow-lg border border-slate-800 flex flex-col items-center justify-center backdrop-blur-sm">
                        <Loader2 className="h-12 w-12 text-blue-500 animate-spin mb-4" />
                        <span className="text-slate-400 font-medium tracking-widest uppercase text-sm animate-pulse">
                            Extracting & Calculating Sector RRG Metrics...
                        </span>
                    </div>
                )}

                {/* 数据载入完毕后渲染组件：透传控制 length 的状态给它剪裁 */}
                {!loading && !error && (
                    <div className="bg-slate-900 rounded-xl shadow-2xl border border-slate-800 backdrop-blur-md">
                        <RRGChart data={rrgData} tailLength={tailLength} />
                    </div>
                )}

                {!loading && !error && (
                    <div className="mt-8 p-6 bg-slate-900/50 rounded-xl border border-slate-800 text-slate-400 text-sm leading-relaxed">
                        <p className="mb-2"><strong className="text-slate-200">Data Source:</strong> Fast API Quant Backend • Sector ETFs loaded from EODHD.</p>
                        <p><strong className="text-slate-200">Benchmark:</strong> SPY • Track 14-day SMA & StdDev trajectory offsets.</p>
                    </div>
                )}
            </div>
        </div>
    );
}
