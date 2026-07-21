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
            setLoading(true);
            setError("");
            try {
                const data = await fetchStockNews(ticker, controller.signal);
                if (isMounted) {
                    setNews(data);
                    setError("");
                }
            } catch (err) {
                if (err instanceof DOMException && err.name === "AbortError") return;
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
        <div className="surface-panel flex min-h-0 flex-1 flex-col overflow-hidden">
            <div className="surface-subtle flex shrink-0 items-center justify-between border-b p-4 sm:px-5">
                <div>
                    <p className="eyebrow">External context</p>
                    <h2 className="mt-1 font-black text-slate-900 dark:text-white">Recent news & catalysts</h2>
                </div>
                <div className="rounded-full border px-2.5 py-1 text-xs font-semibold text-slate-500">
                    Past 72h
                </div>
            </div>

            <div className="custom-scrollbar min-h-0 flex-1 overflow-y-auto p-4">
                {loading ? (
                    <div className="flex h-full flex-col items-center justify-center space-y-4 text-slate-500">
                        <div className="h-8 w-8 animate-spin rounded-full border-2 border-slate-200 border-t-emerald-500 dark:border-slate-700 dark:border-t-emerald-400" />
                        <p className="text-sm font-medium">Loading recent coverage…</p>
                    </div>
                ) : error ? (
                    <div className="flex h-full flex-col items-center justify-center space-y-2 text-rose-500" role="alert">
                        <ZapOff size={32} />
                        <p className="text-sm">{error}</p>
                    </div>
                ) : news.length === 0 ? (
                    <div className="flex h-full flex-col items-center justify-center space-y-2 text-slate-500">
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
                                    className="group block rounded-xl border bg-white p-4 transition-colors hover:border-emerald-300 hover:bg-slate-50 dark:bg-slate-950/30 dark:hover:border-emerald-800 dark:hover:bg-slate-900"
                                >
                                    <div className="mb-2 flex items-start justify-between gap-3">
                                        <h3 className="line-clamp-2 w-full text-sm font-bold leading-5 text-slate-800 transition-colors group-hover:text-emerald-700 dark:text-slate-200 dark:group-hover:text-emerald-300">
                                            {item.title}
                                        </h3>
                                        <ExternalLink size={14} className="mt-0.5 shrink-0 text-slate-400 transition-colors group-hover:text-emerald-600" aria-hidden="true" />
                                    </div>

                                    <p className="mb-3 line-clamp-2 text-xs leading-5 text-slate-500">
                                        {item.summary}
                                    </p>

                                    <div className="flex items-center justify-between gap-3 text-[11px] font-medium text-slate-500">
                                        <span className="truncate rounded border bg-slate-50 px-2 py-0.5 dark:bg-slate-900">
                                            {item.publisher}
                                        </span>
                                        {parsedDate && (
                                            <div className="flex shrink-0 items-center gap-1.5">
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
