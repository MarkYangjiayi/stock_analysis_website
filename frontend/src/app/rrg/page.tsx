"use client";

import React, { useState, useEffect } from 'react';
import RRGChart, { RRGResponse } from '@/components/RRGChart';
import { Loader2 } from 'lucide-react';
import { API_BASE_URL } from '@/lib/api';

export default function RealRRGWidget() {
    const [rrgData, setRrgData] = useState<RRGResponse | null>(null);
    const [loading, setLoading] = useState<boolean>(true);
    const [error, setError] = useState<string | null>(null);

    // 初始化拖尾长度，UI 拖拉拽可控，默认设为 14
    const [tailLength, setTailLength] = useState<number>(14);

    // 全局日期列表与时光机当前索引
    const [dateList, setDateList] = useState<string[]>([]);
    const [currentDayIndex, setCurrentDayIndex] = useState<number>(0);
    const [isPlaying, setIsPlaying] = useState<boolean>(false);

    // 自动播放时光机逻辑
    useEffect(() => {
        let interval: ReturnType<typeof setInterval>;
        if (isPlaying) {
            interval = setInterval(() => {
                setCurrentDayIndex((prev) => {
                    // 如果已经播放到最后一天，则停止播放
                    if (prev >= dateList.length - 1) {
                        setIsPlaying(false);
                        return prev;
                    }
                    return prev + 1;
                });
            }, 150); // 150ms 的回放速度
        }
        return () => {
            if (interval) clearInterval(interval);
        };
    }, [isPlaying, dateList.length]);

    useEffect(() => {
        const fetchRRG = async () => {
            try {
                setLoading(true);
                setError(null);

                // 拼接真实的 FastAPI 端点进行请求
                // 改为 11 大行业板块 ETF 代码，请求一整年 (252个交易日) 的大规模数据
                const endpoint = `${API_BASE_URL}/api/v1/rrg?tickers=XLK.US,XLF.US,XLV.US,XLY.US,XLP.US,XLE.US,XLI.US,XLB.US,XLU.US,XLRE.US,XLC.US&benchmark=SPY&history_days=252`;

                const response = await fetch(endpoint);

                if (!response.ok) {
                    throw new Error(`API returned status ${response.status}`);
                }

                const data: RRGResponse = await response.json();
                setRrgData(data);

                // 提取全量日期列表，供时光机控制
                const firstSector = Object.keys(data.data)[0];
                if (firstSector && data.data[firstSector]) {
                    const dates = data.data[firstSector].map(p => p.date);
                    setDateList(dates);
                    // 默认停靠在最新的一天
                    setCurrentDayIndex(dates.length - 1);
                }

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
        <div className="h-full w-full overflow-y-auto bg-gray-50 dark:bg-[#0E1117] text-gray-900 dark:text-gray-100 p-6 md:p-8 font-sans selection:bg-emerald-500/30">
            <div className="max-w-7xl mx-auto space-y-8 animate-in fade-in slide-in-from-bottom-8 duration-700 ease-out fill-mode-both">
                <div className="flex items-center justify-between border-b border-gray-200 dark:border-gray-800 pb-4">
                    <h1 className="text-2xl font-bold tracking-wide text-gray-900 dark:text-white">
                        <span className="text-emerald-500 dark:text-emerald-400">US Sector Rotation</span> (RRG)
                    </h1>
                    {rrgData?.update_time && (
                        <div className="text-sm font-medium text-emerald-600 dark:text-emerald-400/80 bg-emerald-50 dark:bg-emerald-500/10 px-3 py-1 rounded-full border border-emerald-500/20 shadow-[0_0_10px_rgba(16,185,129,0.1)]">
                            Last Updated: {new Date(rrgData.update_time).toLocaleString()}
                        </div>
                    )}
                </div>

                {/* 增加基于 Range 控制的面板 UI */}
                <div className="bg-white dark:bg-[#191D26] p-6 rounded-2xl border border-gray-200 dark:border-gray-800 mb-6 flex flex-col gap-6 shadow-xl dark:shadow-2xl transition-colors">
                    {/* 第一排：主时间轴 Timeline Slider */}
                    <div className="flex flex-col gap-3 border-b border-gray-200 dark:border-gray-800 pb-5">
                        <div className="flex items-center justify-between">
                            <div className="flex items-center gap-4">
                                <label htmlFor="timeline-slider" className="text-gray-800 dark:text-gray-200 font-bold tracking-wide flex items-center gap-2">
                                    历史回放 (Timeline)
                                </label>
                                <button
                                    onClick={() => {
                                        // 如果当前已经是最后一天，点击播放时重置到 60 天前（或第0天）再播
                                        if (!isPlaying && currentDayIndex >= dateList.length - 1) {
                                            setCurrentDayIndex(Math.max(0, dateList.length - 60));
                                        }
                                        setIsPlaying(!isPlaying);
                                    }}
                                    disabled={dateList.length === 0}
                                    className={`px-4 py-1.5 rounded-md text-sm font-bold flex items-center gap-1 transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${isPlaying
                                        ? 'bg-amber-600 hover:bg-amber-500 text-white shadow-lg shadow-amber-900/20'
                                        : 'bg-emerald-600 hover:bg-emerald-500 text-white shadow-lg shadow-emerald-900/20'
                                        }`}
                                >
                                    {isPlaying ? (
                                        <>
                                            <svg className="w-4 h-4 fill-current" viewBox="0 0 24 24"><path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z" /></svg>
                                            暂停
                                        </>
                                    ) : (
                                        <>
                                            <svg className="w-4 h-4 fill-current" viewBox="0 0 24 24"><path d="M8 5v14l11-7z" /></svg>
                                            播放
                                        </>
                                    )}
                                </button>
                            </div>
                            <span className="text-emerald-600 dark:text-emerald-400 font-mono font-bold bg-emerald-50 dark:bg-[#141820] px-3 py-1.5 rounded-md border border-emerald-500/20 shadow-[0_0_10px_rgba(16,185,129,0.1)]">
                                当前日期: {dateList.length > 0 ? dateList[currentDayIndex] : '--'}
                            </span>
                        </div>
                        <input
                            id="timeline-slider"
                            type="range"
                            min={0}
                            max={dateList.length > 0 ? dateList.length - 1 : 0}
                            step={1}
                            value={currentDayIndex}
                            onChange={(e) => setCurrentDayIndex(Number(e.target.value))}
                            disabled={dateList.length === 0}
                            className="w-full h-3 bg-gray-200 dark:bg-[#151922] rounded-lg appearance-none cursor-pointer accent-emerald-600 hover:accent-emerald-500 dark:accent-emerald-500 dark:hover:accent-emerald-400 transition-all border border-gray-300 dark:border-gray-700 disabled:opacity-50"
                        />
                    </div>

                    {/* 第二排：细粒度调节 (Tail Length) */}
                    <div className="flex flex-col gap-2">
                        <div className="flex items-center justify-between">
                            <label htmlFor="tail-slider" className="text-gray-600 dark:text-gray-400 font-medium text-sm">
                                拖尾长度 (Tail Length): <span className="text-emerald-600 dark:text-emerald-400 ml-1 font-bold">{tailLength} 天</span>
                            </label>
                            <span className="text-xs text-gray-500 dark:text-gray-500 bg-gray-100 dark:bg-[#151922] px-2 py-1 rounded-md border border-gray-200 dark:border-gray-800">
                                缓存跨度: {dateList.length} 天
                            </span>
                        </div>
                        <input
                            id="tail-slider"
                            type="range"
                            min={3}
                            max={30}
                            step={1}
                            value={tailLength}
                            onChange={(e) => setTailLength(Number(e.target.value))}
                            className="w-full h-1.5 bg-gray-200 dark:bg-[#151922] rounded-lg appearance-none cursor-pointer accent-emerald-600 hover:accent-emerald-500 dark:accent-emerald-500 dark:hover:accent-emerald-400 transition-all border border-gray-300 dark:border-gray-700"
                        />
                    </div>
                </div>

                {/* 错误态处理 */}
                {error && (
                    <div className="w-full bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-500/30 rounded-2xl p-6 mb-6">
                        <h3 className="text-xl font-bold text-red-600 dark:text-red-500 mb-2">Failed to load chart data</h3>
                        <p className="text-red-500 dark:text-red-300 font-mono text-sm">{error}</p>
                    </div>
                )}

                {/* 载入态处理: 适配亮暗主题带旋转 SVG */}
                {loading && (
                    <div className="w-full h-[600px] bg-white dark:bg-[#191D26] rounded-2xl shadow-xl dark:shadow-2xl border border-gray-200 dark:border-gray-800 flex flex-col items-center justify-center transition-colors duration-300">
                        <Loader2 className="h-12 w-12 text-emerald-600 dark:text-emerald-500 animate-spin mb-4" />
                        <span className="text-slate-600 dark:text-gray-400 font-medium tracking-widest uppercase text-sm animate-pulse">
                            Extracting & Calculating Sector RRG Metrics...
                        </span>
                    </div>
                )}

                {/* 数据载入完毕后渲染组件：透传控制 length 的状态给它剪裁 */}
                {!loading && !error && (
                    <div className="bg-white dark:bg-[#191D26] rounded-2xl shadow-xl dark:shadow-2xl border border-gray-200 dark:border-gray-800 overflow-hidden relative transition-colors duration-300">
                        {/* We use strict borders explicitly since RRG uses its own dark theme inside ECharts */}
                        <RRGChart data={rrgData} tailLength={tailLength} currentDayIndex={currentDayIndex} />
                    </div>
                )}

                {!loading && !error && (
                    <div className="p-6 bg-white dark:bg-[#191D26] rounded-2xl border border-gray-200 dark:border-gray-800 text-slate-600 dark:text-gray-400 text-sm leading-relaxed shadow-lg dark:shadow-xl transition-colors duration-300">
                        <p className="mb-2"><strong className="text-slate-900 dark:text-gray-200">Data Source:</strong> Fast API Quant Backend • Sector ETFs loaded from EODHD.</p>
                        <p><strong className="text-slate-900 dark:text-gray-200">Benchmark:</strong> SPY • Track double EMA trajectory offsets.</p>
                    </div>
                )}
            </div>
        </div>
    );
}
