"use client";

import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Bot, RefreshCw, Sparkles, TriangleAlert } from "lucide-react";
import { API_BASE_URL } from "@/lib/api";

interface AIReportProps {
    ticker: string;
    evidenceKey: string;
    adminKey?: string | null;
    onUnauthorized?: () => void;
    embedded?: boolean;
    disabledReason?: string;
}

export default function AIReport({ ticker, evidenceKey, adminKey, onUnauthorized, embedded = false, disabledReason }: AIReportProps) {
    const [report, setReport] = useState("");
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");
    const abortRef = useRef<AbortController | null>(null);

    useEffect(() => {
        abortRef.current?.abort();
        setReport("");
        setError("");
        setLoading(false);
        return () => abortRef.current?.abort();
    }, [ticker, evidenceKey, adminKey, disabledReason]);

    const loadReport = async () => {
        if (disabledReason) return;
        abortRef.current?.abort();
        const controller = new AbortController();
        abortRef.current = controller;
        setReport("");
        setError("");
        setLoading(true);
        try {
            const response = await fetch(`${API_BASE_URL}/api/stocks/${encodeURIComponent(ticker)}/report`, {
                signal: controller.signal,
                headers: adminKey ? { "X-API-Key": adminKey } : undefined,
            });
            if (!response.ok) {
                if (response.status === 401) onUnauthorized?.();
                const payload = await response.json().catch(() => ({})) as { detail?: string };
                throw new Error(payload.detail || `Report request failed with status ${response.status}`);
            }
            if (!response.body) throw new Error("The report stream was empty.");
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let complete = "";
            while (true) {
                const { value, done } = await reader.read();
                if (done) break;
                if (value) {
                    const chunk = decoder.decode(value, { stream: true });
                    complete += chunk;
                    setReport((current) => current + chunk);
                }
            }
            const tail = decoder.decode();
            if (tail) {
                complete += tail;
                setReport((current) => current + tail);
            }
            if (complete.trimStart().startsWith("Error:")) {
                setReport("");
                setError(complete.trim().replace(/^Error:\s*/, ""));
            }
        } catch (caught) {
            if (caught instanceof DOMException && caught.name === "AbortError") return;
            setError(caught instanceof Error ? caught.message : "Unable to generate the research brief.");
        } finally {
            if (!controller.signal.aborted) setLoading(false);
        }
    };

    return (
        <section className={`${embedded ? "flex" : "surface-panel flex"} min-h-[360px] flex-col p-5 sm:p-6`} aria-labelledby="ai-brief-title">
            <header className="flex items-start justify-between gap-4 border-b pb-4">
                <div className="flex items-center gap-3">
                    <span className="rounded-xl bg-indigo-50 p-2.5 text-indigo-500 dark:bg-indigo-950/40"><Bot size={21} /></span>
                    <div>
                        <p className="eyebrow">Optional · evidence constrained</p>
                        <h2 id="ai-brief-title" className="mt-0.5 font-black">Evidence brief</h2>
                    </div>
                </div>
                {report && <button type="button" onClick={loadReport} disabled={loading} className="secondary-button min-h-9 px-3 py-1.5" aria-label="Regenerate research brief"><RefreshCw className={loading ? "animate-spin" : ""} size={15} /> Refresh</button>}
            </header>

            <div className="custom-scrollbar min-h-0 flex-1 overflow-y-auto pt-4">
                {disabledReason ? (
                    <div className="flex min-h-[240px] flex-col items-center justify-center rounded-xl border border-amber-200 bg-amber-50/70 p-5 text-center text-amber-900 dark:border-amber-900 dark:bg-amber-950/20 dark:text-amber-200" role="status">
                        <TriangleAlert size={28} />
                        <h3 className="mt-4 font-bold">Brief paused</h3>
                        <p className="mt-2 max-w-md text-sm leading-6">{disabledReason}</p>
                    </div>
                ) : loading && !report ? (
                    <div className="flex h-full min-h-[240px] flex-col items-center justify-center text-center">
                        <RefreshCw className="animate-spin text-indigo-500" size={28} />
                        <p className="mt-4 text-sm font-semibold">Generating a synthesis from the current snapshot…</p>
                        <p className="mt-1 text-xs text-slate-500">This can take a little while.</p>
                    </div>
                ) : error ? (
                    <div className="error-panel flex min-h-[220px] flex-col items-center justify-center text-center" role="alert">
                        <TriangleAlert size={28} /><p className="mt-3 max-w-md">{error}</p>
                        <button type="button" onClick={loadReport} className="secondary-button mt-4">Try again</button>
                    </div>
                ) : report ? (
                    <div className="prose prose-sm max-w-none dark:prose-invert prose-headings:font-black prose-a:text-indigo-500">
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>{report}</ReactMarkdown>
                    </div>
                ) : (
                    <div className="flex min-h-[240px] flex-col items-center justify-center text-center">
                        <Sparkles className="text-indigo-400" size={30} />
                        <h3 className="mt-4 font-bold">Generate on demand</h3>
                        <p className="mt-2 max-w-md text-sm leading-6 text-slate-500">Create a cited narrative from the cockpit evidence IDs only. The deterministic panels remain available and authoritative even if generation fails.</p>
                        <button type="button" onClick={loadReport} className="primary-button mt-5 bg-indigo-600 hover:bg-indigo-700"><Sparkles size={16} /> Generate brief</button>
                    </div>
                )}
            </div>
        </section>
    );
}
