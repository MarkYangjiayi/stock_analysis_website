"use client";

import React, { useEffect, useState } from "react";
import { fetchStockNews, NewsItem } from "@/lib/api";
import { Clock, ExternalLink, Newspaper, ZapOff } from "lucide-react";
import { formatDistanceToNow } from "date-fns";

interface NewsFeedProps {
    ticker: string;
}

export default function NewsFeed({ ticker }: NewsFeedProps) {
    const [news, setNews] = useState<NewsItem[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");

    useEffect(() => {
        if (!ticker) return;

        let isMounted = true;
        const controller = new AbortController();

        const loadNews = async () => {
            try {
                const data = await fetchStockNews(ticker);
                if (isMounted) {
                    setNews(data);
                    setError("");
                }
            } catch (err) {
                console.error("Failed to fetch news:", err);
                if (isMounted) setError("Failed to load global news feed.");
            } finally {
                if (isMounted) setLoading(false);
            }
        };

        loadNews();

        return () => {
            isMounted = false;
            controller.abort();
        };
    }, [ticker]);

    return (
        <div className="bg-[#191D26] border border-gray-800 rounded-2xl shadow-2xl flex flex-col h-full overflow-hidden">
            {/* Header */}
            <div className="p-4 border-b border-gray-800 bg-[#141820] flex items-center justify-between shrink-0 relative overflow-hidden">
                <div className="absolute inset-0 bg-gradient-to-r from-blue-500/10 to-transparent pointer-events-none" />
                <div className="flex items-center gap-3 relative z-10">
                    <div className="p-2 bg-blue-500/20 rounded-lg border border-blue-500/30">
                        <Newspaper className="text-blue-400" size={20} />
                    </div>
                    <h3 className="font-extrabold text-white tracking-wide">Signal Intel & News</h3>
                </div>
                <div className="text-xs font-mono text-gray-500 bg-[#1D212A] px-2 py-1 rounded border border-gray-700 relative z-10">
                    Past 72H
                </div>
            </div>

            {/* Content Area */}
            <div className="flex-1 overflow-y-auto p-4 custom-scrollbar">
                {loading ? (
                    <div className="flex flex-col items-center justify-center h-full space-y-4 text-gray-500">
                        <div className="w-8 h-8 border-2 border-blue-500/20 border-t-blue-500 rounded-full animate-spin"></div>
                        <p className="text-sm font-medium animate-pulse">Scanning global feeds...</p>
                    </div>
                ) : error ? (
                    <div className="flex flex-col items-center justify-center h-full text-red-400/80 space-y-2">
                        <ZapOff size={32} />
                        <p className="text-sm">{error}</p>
                    </div>
                ) : news.length === 0 ? (
                    <div className="flex flex-col items-center justify-center h-full text-gray-500 space-y-2">
                        <Newspaper size={32} className="opacity-50" />
                        <p className="text-sm">No recent news catalysts found.</p>
                    </div>
                ) : (
                    <div className="space-y-4">
                        {news.map((item, idx) => {
                            // Parse date
                            let parsedDate = "";
                            try {
                                if (item.pub_date) {
                                    const dateObj = new Date(item.pub_date);
                                    parsedDate = formatDistanceToNow(dateObj, { addSuffix: true });
                                }
                            } catch { }

                            return (
                                <a
                                    key={idx}
                                    href={item.link}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="block group bg-[#151922] hover:bg-[#1D222D] p-4 rounded-xl border border-gray-800/80 hover:border-blue-500/40 transition-all shadow-sm"
                                >
                                    <div className="flex justify-between items-start gap-3 mb-2">
                                        <h4 className="text-sm font-semibold text-gray-200 group-hover:text-blue-400 transition-colors leading-tight line-clamp-2 w-full">
                                            {item.title}
                                        </h4>
                                        <ExternalLink size={14} className="text-gray-600 group-hover:text-blue-400 shrink-0 mt-0.5 opacity-0 group-hover:opacity-100 transition-opacity" />
                                    </div>

                                    <p className="text-xs text-gray-500 line-clamp-2 mb-3 leading-relaxed">
                                        {item.summary}
                                    </p>

                                    <div className="flex items-center justify-between text-[11px] font-medium text-gray-600">
                                        <span className="bg-gray-800/50 px-2 py-0.5 rounded text-gray-400 border border-gray-700/50">
                                            {item.publisher}
                                        </span>
                                        {parsedDate && (
                                            <div className="flex items-center gap-1.5 opacity-80 group-hover:opacity-100 transition-opacity">
                                                <Clock size={12} />
                                                <span>{parsedDate}</span>
                                            </div>
                                        )}
                                    </div>
                                </a>
                            );
                        })}
                    </div>
                )}
            </div>
        </div>
    );
}
