"use client";

import React, { useEffect, useState } from "react";
import { fetchMarketAnomalies, AnomalyReport } from "@/lib/api";
import { Sparkles, TrendingUp, TrendingDown, ExternalLink, Activity, ScanSearch } from "lucide-react";
import Link from "next/link";

export default function AnomaliesPage() {
    const [anomalies, setAnomalies] = useState<AnomalyReport[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");

    useEffect(() => {
        let isMounted = true;
        const controller = new AbortController();

        const loadData = async () => {
            try {
                const data = await fetchMarketAnomalies();
                if (isMounted) {
                    setAnomalies(data);
                    setError("");
                }
            } catch (err) {
                console.error("Failed to fetch anomalies:", err);
                if (isMounted) setError("Failed to synchronize anomaly scan.");
            } finally {
                if (isMounted) setLoading(false);
            }
        };

        loadData();

        return () => {
            isMounted = false;
            controller.abort();
        };
    }, []);

    return (
        <div className="h-full w-full overflow-y-auto bg-[#0E1117] text-gray-100 p-6 md:p-8 font-sans selection:bg-emerald-500/30">
            <div className="max-w-5xl mx-auto space-y-8 animate-in fade-in slide-in-from-bottom-8 duration-700 ease-out fill-mode-both">
                {/* Header */}
                <div className="flex items-center justify-between border-b border-gray-800 pb-6 relative overflow-hidden">
                    <div className="absolute -left-10 -top-10 w-40 h-40 bg-purple-500/10 blur-[50px] rounded-full pointer-events-none" />
                    <div className="flex items-center gap-4 relative z-10">
                        <div className="p-3 bg-[#151922] rounded-xl border border-gray-800 shadow-inner">
                            <Activity className="text-purple-400" size={28} />
                        </div>
                        <div>
                            <h1 className="text-3xl font-extrabold tracking-tight text-white flex items-center gap-3">
                                Market Anomalies
                                <span className="bg-gradient-to-r from-purple-400 to-blue-400 bg-clip-text text-transparent italic tracking-normal ml-1">
                                    & AI Attribution
                                </span>
                            </h1>
                            <p className="text-gray-400 text-sm mt-1">Real-time daily variance scanning powered by predictive language models.</p>
                        </div>
                    </div>
                </div>

                {/* Content */}
                {loading ? (
                    <div className="flex flex-col items-center justify-center h-[50vh] text-center space-y-6 animate-in fade-in duration-500">
                        <div className="relative w-32 h-32 flex items-center justify-center">
                            <div className="absolute inset-0 border-[3px] border-purple-500/20 rounded-full"></div>
                            <div className="absolute inset-0 border-[3px] border-transparent border-t-purple-400 rounded-full animate-spin [animation-duration:1.5s]"></div>
                            <div className="absolute inset-2 border-[3px] border-transparent border-b-blue-400 rounded-full animate-spin [animation-duration:2s] [animation-direction:reverse]"></div>
                            <ScanSearch size={40} className="text-purple-400/80 animate-pulse" />
                        </div>
                        <div className="space-y-2">
                            <h3 className="text-xl font-bold text-gray-200">Quantum Radar Active</h3>
                            <p className="text-gray-500 text-sm max-w-sm">AI is scanning the global network for news catalysts to attribute multi-sigma price deviations...</p>
                        </div>
                    </div>
                ) : error ? (
                    <div className="bg-red-500/10 border border-red-500/20 rounded-2xl p-6 text-center">
                        <p className="text-red-400 font-medium">{error}</p>
                    </div>
                ) : anomalies.length === 0 ? (
                    <div className="flex flex-col items-center justify-center h-[40vh] text-gray-500 space-y-4">
                        <Activity size={48} className="opacity-30" />
                        <p className="text-lg">No significant market anomalies detected today.</p>
                    </div>
                ) : (
                    <div className="space-y-6 pb-12">
                        {anomalies.map((item, idx) => {
                            const isPositive = item.price_change >= 0;
                            const Icon = isPositive ? TrendingUp : TrendingDown;

                            return (
                                <div
                                    key={idx}
                                    className="group bg-[#151922] border border-gray-800 rounded-2xl overflow-hidden shadow-xl hover:border-gray-700 transition-all duration-300"
                                >
                                    {/* Card Header */}
                                    <div className="p-5 border-b border-gray-800/60 flex flex-col sm:flex-row sm:items-center justify-between gap-4 bg-[#181C25]">
                                        <div className="flex items-center gap-4">
                                            <div className="bg-[#0E1117] px-3 py-1.5 rounded-lg border border-gray-800 shadow-inner flex items-center gap-2">
                                                <Link
                                                    href={`/?ticker=${item.ticker}`}
                                                    className="font-bold text-gray-200 hover:text-purple-400 transition-colors tracking-wide"
                                                >
                                                    {item.ticker.replace('.US', '')}
                                                </Link>
                                            </div>
                                            <span className="text-gray-400 font-medium text-sm sm:text-base line-clamp-1">{item.company_name}</span>
                                        </div>

                                        <div className={`flex items-center gap-2 px-4 py-1.5 rounded-full font-bold shadow-lg ${isPositive
                                                ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 shadow-[0_0_15px_rgba(16,185,129,0.15)]'
                                                : 'bg-red-500/10 text-red-400 border border-red-500/20 shadow-[0_0_15px_rgba(239,68,68,0.15)]'
                                            }`}>
                                            <Icon size={18} strokeWidth={2.5} />
                                            <span className="text-lg tracking-tight">
                                                {isPositive ? '+' : ''}{item.price_change.toFixed(2)}%
                                            </span>
                                        </div>
                                    </div>

                                    {/* AI Analysis Area */}
                                    <div className="p-5 sm:p-6 bg-gradient-to-b from-[#11141B] to-[#151922] relative">
                                        <div className="flex gap-4 items-start">
                                            <div className="shrink-0 mt-1 p-2 bg-purple-500/10 rounded-lg border border-purple-500/20 shadow-[0_0_20px_rgba(168,85,247,0.1)]">
                                                <Sparkles className="text-purple-400" size={20} />
                                            </div>
                                            <div className="flex-1 space-y-4">
                                                <div className="prose prose-invert max-w-none text-gray-300 leading-relaxed text-[15px]">
                                                    {item.ai_analysis.split('\n').map((paragraph, pIdx) => (
                                                        <p key={pIdx} className="mb-2 last:mb-0">{paragraph}</p>
                                                    ))}
                                                </div>

                                                {/* News Sources Footer */}
                                                {item.top_news_links && item.top_news_links.length > 0 && (
                                                    <div className="pt-4 mt-2 border-t border-gray-800/60 flex flex-wrap items-center gap-3">
                                                        <span className="text-xs font-mono text-gray-500 uppercase tracking-wider">Catalyst Sources:</span>
                                                        {item.top_news_links.map((link, lIdx) => (
                                                            <a
                                                                key={lIdx}
                                                                href={link}
                                                                target="_blank"
                                                                rel="noopener noreferrer"
                                                                className="flex items-center gap-1.5 text-xs text-purple-400/80 hover:text-purple-300 bg-purple-500/5 hover:bg-purple-500/10 px-2 py-1 rounded border border-purple-500/10 transition-colors"
                                                            >
                                                                News {lIdx + 1}
                                                                <ExternalLink size={10} />
                                                            </a>
                                                        ))}
                                                    </div>
                                                )}
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                )}
            </div>
        </div>
    );
}
