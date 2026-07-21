"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { CalendarClock, Loader2, Pause, Play, RotateCcw, TriangleAlert } from "lucide-react";
import RRGChart, { RRGResponse } from "@/components/RRGChart";
import { apiRequest } from "@/lib/api";

const ENDPOINT = "/api/v1/rrg?tickers=XLK.US,XLF.US,XLV.US,XLY.US,XLP.US,XLE.US,XLI.US,XLB.US,XLU.US,XLRE.US,XLC.US&benchmark=SPY.US&history_days=252";

export default function RRGPage() {
    const [data, setData] = useState<RRGResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const [tailLength, setTailLength] = useState(14);
    const [currentDayIndex, setCurrentDayIndex] = useState(0);
    const [playing, setPlaying] = useState(false);

    const dateList = useMemo(() => {
        if (!data?.data) return [];
        const firstSeries = Object.values(data.data)[0];
        return firstSeries?.map((point) => point.date) || [];
    }, [data]);

    const loadData = useCallback(async (signal?: AbortSignal) => {
        setLoading(true);
        setError("");
        try {
            const response = await apiRequest<RRGResponse>(ENDPOINT, { signal }, 90_000);
            setData(response);
            const firstSeries = Object.values(response.data)[0] || [];
            setCurrentDayIndex(Math.max(firstSeries.length - 1, 0));
        } catch (caught) {
            if (caught instanceof DOMException && caught.name === "AbortError") return;
            setError(caught instanceof Error ? caught.message : "Unable to load sector rotation data.");
        } finally {
            if (!signal?.aborted) setLoading(false);
        }
    }, []);

    useEffect(() => {
        const controller = new AbortController();
        void loadData(controller.signal);
        return () => controller.abort();
    }, [loadData]);

    useEffect(() => {
        if (!playing) return;
        const timer = window.setInterval(() => {
            setCurrentDayIndex((current) => {
                if (current >= dateList.length - 1) {
                    setPlaying(false);
                    return current;
                }
                return current + 1;
            });
        }, 180);
        return () => window.clearInterval(timer);
    }, [dateList.length, playing]);

    const dataDate = data?.data_as_of_date || dateList.at(-1) || null;
    const ageDays = dataDate ? Math.floor((Date.now() - new Date(`${dataDate}T00:00:00`).getTime()) / 86_400_000) : null;
    const stale = ageDays != null && ageDays > 7;

    const togglePlayback = () => {
        if (!playing && currentDayIndex >= dateList.length - 1) setCurrentDayIndex(Math.max(0, dateList.length - 60));
        setPlaying((value) => !value);
    };

    return (
        <div className="app-page">
            <div className="page-container">
                <header className="flex flex-col justify-between gap-4 border-b pb-5 lg:flex-row lg:items-end">
                    <div><p className="eyebrow">Relative strength trajectory</p><h1 className="page-title mt-1">US Sector Rotation</h1><p className="page-description">Track sector ETF rotation relative to SPY across relative strength and momentum quadrants.</p></div>
                    <div className="flex flex-wrap gap-2">
                        {dataDate && <span className={stale ? "inline-flex items-center gap-1.5 rounded-full border border-amber-300 bg-amber-50 px-2.5 py-1 text-xs font-bold text-amber-800 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-200" : "status-pill"}><CalendarClock size={13} /> Data through {dataDate}</span>}
                        {data?.benchmark && <span className="rounded-full border px-2.5 py-1 text-xs font-semibold text-slate-500">Benchmark {data.benchmark}</span>}
                    </div>
                </header>

                {stale && <div className="rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-200" role="status"><TriangleAlert className="mr-2 inline" size={16} />The underlying sector ETF series is {ageDays} days old. Interpret the rotation path as historical until the price history is refreshed.</div>}
                {error && <div className="error-panel flex items-center justify-between gap-4" role="alert"><span>{error}</span><button type="button" className="secondary-button shrink-0" onClick={() => void loadData()}><RotateCcw size={15} /> Retry</button></div>}

                <section className="surface-panel p-4 sm:p-5" aria-label="Rotation playback controls">
                    <div className="grid gap-5 lg:grid-cols-[1fr_280px]">
                        <div>
                            <div className="mb-3 flex flex-col justify-between gap-2 sm:flex-row sm:items-center">
                                <div className="flex items-center gap-2"><button type="button" className="primary-button min-h-9 px-3 py-1.5" onClick={togglePlayback} disabled={!dateList.length}>{playing ? <Pause size={15} /> : <Play size={15} />}{playing ? "Pause" : "Replay"}</button><span className="text-xs text-slate-500">Timeline</span></div>
                                <strong className="font-mono text-sm text-emerald-700 dark:text-emerald-300">{dateList[currentDayIndex] || "—"}</strong>
                            </div>
                            <label className="sr-only" htmlFor="rrg-timeline">Rotation timeline</label>
                            <input id="rrg-timeline" type="range" className="w-full accent-emerald-600" min={0} max={Math.max(dateList.length - 1, 0)} value={currentDayIndex} disabled={!dateList.length} onChange={(event) => setCurrentDayIndex(Number(event.target.value))} />
                        </div>
                        <div>
                            <div className="mb-3 flex justify-between text-xs text-slate-500"><label htmlFor="rrg-tail">Trail length</label><strong>{tailLength} sessions</strong></div>
                            <input id="rrg-tail" type="range" className="w-full accent-emerald-600" min={3} max={30} value={tailLength} onChange={(event) => setTailLength(Number(event.target.value))} />
                        </div>
                    </div>
                </section>

                <section className="surface-panel overflow-hidden">
                    {loading ? <div className="flex h-[520px] flex-col items-center justify-center"><Loader2 className="animate-spin text-emerald-500" size={34} /><p className="mt-4 text-sm text-slate-500">Calculating sector trajectories…</p></div> : !error && <RRGChart data={data} tailLength={tailLength} currentDayIndex={currentDayIndex} />}
                </section>

                <footer className="grid gap-2 rounded-xl border px-4 py-3 text-xs text-slate-500 dark:text-slate-400 sm:grid-cols-2"><span><strong className="text-slate-700 dark:text-slate-200">Source:</strong> Adjusted sector ETF prices</span><span><strong className="text-slate-700 dark:text-slate-200">Method:</strong> Double-EMA relative strength trajectory</span></footer>
            </div>
        </div>
    );
}
