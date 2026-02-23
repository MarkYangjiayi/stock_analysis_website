"use client";

import React, { useState, useEffect, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Bot, RefreshCw, AlertTriangle } from 'lucide-react';
import { API_BASE_URL } from '@/lib/api';

interface AIReportProps {
    ticker: string;
}

const AIReport: React.FC<AIReportProps> = ({ ticker }) => {
    const [report, setReport] = useState<string>('');
    const [loading, setLoading] = useState<boolean>(true);
    const [error, setError] = useState<string | null>(null);
    const abortControllerRef = useRef<AbortController | null>(null);

    const loadReport = async () => {
        setReport('');
        setLoading(true);
        setError(null);

        if (abortControllerRef.current) {
            abortControllerRef.current.abort();
        }

        const controller = new AbortController();
        abortControllerRef.current = controller;

        try {
            const response = await fetch(`${API_BASE_URL}/api/stocks/${ticker}/report`, {
                signal: controller.signal
            });

            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                throw new Error(errorData.detail || `HTTP Error ${response.status}`);
            }

            if (!response.body) throw new Error("No response body");

            setLoading(false);

            const reader = response.body.getReader();
            const decoder = new TextDecoder('utf-8');
            let done = false;

            while (!done) {
                const { value, done: readerDone } = await reader.read();
                done = readerDone;
                if (value) {
                    const chunk = decoder.decode(value, { stream: true });
                    setReport(prev => prev + chunk);
                }
            }
        } catch (err: any) {
            if (err.name === 'AbortError') return;
            console.error("Failed to fetch AI report:", err);
            setError(err.message || 'Failed to generate investment brief.');
            setLoading(false);
        }
    };

    useEffect(() => {
        if (ticker) {
            loadReport();
        }
        return () => {
            if (abortControllerRef.current) {
                abortControllerRef.current.abort();
            }
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [ticker]);

    return (
        <div className="bg-[#151922] border border-gray-800 rounded-xl p-6 shadow-inner w-full mt-4 flex flex-col h-full">
            <div className="flex items-center justify-between mb-4 border-b border-gray-800/80 pb-4">
                <div className="flex items-center gap-3">
                    <div className="p-2.5 bg-gradient-to-br from-indigo-500/20 to-purple-500/20 rounded-xl">
                        <Bot className="text-indigo-400" size={24} />
                    </div>
                    <div>
                        <h3 className="text-lg font-bold text-gray-100 whitespace-nowrap">AI Quant Analyst</h3>
                        <p className="text-xs font-semibold text-indigo-400/80 tracking-widest uppercase mt-0.5">Gemini 2.5 Flash</p>
                    </div>
                </div>

                <button
                    onClick={loadReport}
                    disabled={loading}
                    className="p-2 hover:bg-gray-800 rounded-lg transition-colors disabled:opacity-50 text-gray-400 hover:text-white"
                    title="Regenerate Report"
                >
                    <RefreshCw size={18} className={loading ? "animate-spin" : ""} />
                </button>
            </div>

            <div className="flex-grow overflow-y-auto pr-2 custom-scrollbar">
                {loading ? (
                    <div className="h-full flex flex-col items-center justify-center space-y-4 py-12">
                        <div className="w-10 h-10 border-4 border-indigo-500/30 border-t-indigo-500 rounded-full animate-spin" />
                        <p className="text-sm font-medium text-gray-400 animate-pulse">🤖 AI 正在深度分析财报与因子数据...</p>
                    </div>
                ) : error ? (
                    <div className="bg-rose-500/10 border border-rose-500/20 rounded-lg p-4 flex flex-col items-center justify-center text-center h-full">
                        <AlertTriangle className="text-rose-400 mb-2" size={32} />
                        <p className="text-rose-400 font-medium">{error}</p>
                        <button
                            onClick={loadReport}
                            className="mt-4 px-4 py-2 bg-rose-500/20 hover:bg-rose-500/30 text-rose-300 rounded-lg text-sm transition-colors"
                        >
                            Retry Generated Analysis
                        </button>
                    </div>
                ) : report ? (
                    <div className="prose prose-invert prose-sm max-w-none pb-8 pr-2
                        prose-headings:text-gray-200 prose-headings:font-bold prose-headings:mt-6 prose-headings:mb-3
                        prose-p:text-gray-300 prose-p:leading-relaxed prose-p:mb-4
                        prose-li:text-gray-300
                        prose-strong:text-indigo-300
                        prose-hr:border-gray-800">
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>
                            {report}
                        </ReactMarkdown>
                    </div>
                ) : (
                    <div className="text-center py-8 text-gray-500">No report available.</div>
                )}
            </div>
        </div>
    );
};

export default AIReport;
