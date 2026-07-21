"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Activity, ArrowDownRight, ArrowUpRight, Clock3, ExternalLink, Loader2, Radar, RefreshCw, Sparkles } from "lucide-react";
import { fetchMarketAnomalies } from "@/lib/api";
import { useAppStore } from "@/store/useAppStore";

export default function AnomaliesPage() {
    const { data, lastFetchTime, setAnomaliesData } = useAppStore();
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");
    const controllerRef = useRef<AbortController | null>(null);

    useEffect(() => () => controllerRef.current?.abort(), []);

    const runScan = async () => {
        controllerRef.current?.abort();
        const controller = new AbortController();
        controllerRef.current = controller;
        setLoading(true);
        setError("");
        try {
            const result = await fetchMarketAnomalies(controller.signal);
            if (!controller.signal.aborted) setAnomaliesData(result);
        } catch (caught) {
            if (caught instanceof DOMException && caught.name === "AbortError") return;
            setError(caught instanceof Error ? caught.message : "Unable to complete the anomaly scan.");
        } finally {
            if (!controller.signal.aborted) setLoading(false);
        }
    };

    return (
        <div className="app-page">
            <div className="mx-auto w-full max-w-5xl space-y-6 pb-12">
                <header className="flex flex-col justify-between gap-4 border-b pb-5 sm:flex-row sm:items-end">
                    <div><p className="eyebrow">On-demand market monitor</p><h1 className="page-title mt-1">Market Anomalies</h1><p className="page-description">Scan large daily moves, then review a model-generated catalyst summary and its linked sources.</p></div>
                    {lastFetchTime && <span className="status-pill"><Clock3 size={13} /> Scanned {new Date(lastFetchTime).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>}
                </header>

                <section className="surface-panel flex flex-col justify-between gap-5 p-5 sm:flex-row sm:items-center sm:p-6">
                    <div className="flex gap-4"><span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-emerald-50 text-emerald-600 dark:bg-emerald-950/40 dark:text-emerald-300"><Radar size={22} /></span><div><h2 className="font-black">Run a fresh scan</h2><p className="mt-1 max-w-2xl text-sm leading-6 text-slate-500">This is an explicit, potentially slow operation. It checks the tracked universe and generates attribution only for the largest moves.</p></div></div>
                    <button type="button" className="primary-button shrink-0" disabled={loading} onClick={runScan}>{loading ? <Loader2 className="animate-spin" size={16} /> : data.length ? <RefreshCw size={16} /> : <Activity size={16} />}{loading ? "Scanning…" : data.length ? "Refresh scan" : "Run scan"}</button>
                </section>

                {error && <div className="error-panel" role="alert">{error}</div>}

                {!loading && !data.length && !error && (
                    <section className="surface-panel flex min-h-[360px] flex-col items-center justify-center p-8 text-center"><Activity className="text-slate-300 dark:text-slate-600" size={38} /><h2 className="mt-4 text-lg font-black">No scan results in this session</h2><p className="mt-2 max-w-md text-sm leading-6 text-slate-500">Run the scanner when you need a current anomaly review. The page no longer launches external data and AI work just by being opened.</p></section>
                )}

                {loading && !data.length && (
                    <section className="surface-panel flex min-h-[360px] flex-col items-center justify-center p-8 text-center"><Loader2 className="animate-spin text-emerald-500" size={38} /><h2 className="mt-4 text-lg font-black">Scanning market moves</h2><p className="mt-2 max-w-md text-sm leading-6 text-slate-500">Comparing current quotes and preparing source-backed attribution for the most significant moves.</p></section>
                )}

                {data.length > 0 && <section className="space-y-4" aria-busy={loading}>
                    {data.map((item) => {
                        const positive = item.price_change >= 0;
                        return (
                            <article key={`${item.ticker}-${item.date}`} className="surface-panel overflow-hidden">
                                <header className="surface-subtle flex flex-col justify-between gap-3 border-b p-4 sm:flex-row sm:items-center sm:px-5">
                                    <div className="min-w-0"><div className="flex items-center gap-3"><Link href={`/?ticker=${encodeURIComponent(item.ticker)}`} className="font-mono text-sm font-black text-emerald-600 hover:underline dark:text-emerald-400">{item.ticker.replace(".US", "")}</Link><span className="truncate text-sm font-semibold text-slate-600 dark:text-slate-300">{item.company_name}</span></div><p className="mt-1 text-xs text-slate-500">Price date {item.date}</p></div>
                                    <span className={`inline-flex w-fit items-center gap-1 rounded-lg px-3 py-1.5 font-mono text-base font-black ${positive ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300" : "bg-rose-50 text-rose-600 dark:bg-rose-950/30 dark:text-rose-300"}`}>{positive ? <ArrowUpRight size={17} /> : <ArrowDownRight size={17} />}{positive ? "+" : ""}{item.price_change.toFixed(2)}%</span>
                                </header>
                                <div className="p-5 sm:p-6"><div className="flex gap-3"><Sparkles className="mt-0.5 shrink-0 text-indigo-500" size={18} /><div className="min-w-0 flex-1"><p className="whitespace-pre-line text-sm leading-7 text-slate-700 dark:text-slate-300">{item.ai_analysis}</p>{item.top_news_links?.length > 0 && <div className="mt-5 flex flex-wrap gap-2 border-t pt-4">{item.top_news_links.map((link, index) => <a key={link} href={link} target="_blank" rel="noopener noreferrer" className="secondary-button min-h-8 px-3 py-1 text-xs">Source {index + 1}<ExternalLink size={12} /></a>)}</div>}</div></div></div>
                            </article>
                        );
                    })}
                </section>}
            </div>
        </div>
    );
}
