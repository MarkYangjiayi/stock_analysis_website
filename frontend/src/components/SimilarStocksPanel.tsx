"use client";

import { useEffect, useState } from "react";
import { ChevronDown, ChevronUp, Star } from "lucide-react";
import { fetchSimilarStocks, type SimilarStocksResponse } from "@/lib/api";

const symbol = (ticker: string) => ticker.replace(/\.US$/, "");
const percent = (value: number) => `${value >= 0 ? "+" : ""}${(value * 100).toFixed(1)}%`;

export function ComparisonChart({ base, match, dates, ticker, peer, expanded = false }: {
    base: number[]; match: number[]; dates: string[]; ticker: string; peer: string; expanded?: boolean;
}) {
    const [hover, setHover] = useState<number | null>(null);
    const width = 640, height = expanded ? 210 : 90;
    const pad = expanded ? 45 : 5;
    const min = Math.min(0, ...base, ...match), max = Math.max(0, ...base, ...match);
    const span = Math.max(max - min, 0.01);
    const x = (i: number) => pad + i / Math.max(base.length - 1, 1) * (width - pad * 2);
    const y = (value: number) => height - 12 - (value - min) / span * (height - 24);
    const line = (values: number[]) => values.map((value, i) => `${x(i)},${y(value)}`).join(" ");
    const index = hover ?? base.length - 1;
    return <div>
        {expanded && <div className="mb-3 flex flex-wrap gap-x-5 gap-y-1 text-xs tabular-nums" aria-live="polite">
            <span className="text-[var(--text-muted)]">{dates[index]}</span>
            <span><span className="mr-1 inline-block h-0.5 w-3 bg-slate-400" />{symbol(ticker)} {percent(base[index])}</span>
            <span><span className="mr-1 inline-block h-0.5 w-3 bg-emerald-500" />{symbol(peer)} {percent(match[index])}</span>
        </div>}
        <svg viewBox={`0 0 ${width} ${height}`} className={expanded ? "h-auto w-full" : "h-12 w-full"}
            preserveAspectRatio={expanded ? "xMidYMid meet" : "none"} role="img"
            aria-label={`${symbol(ticker)} and ${symbol(peer)} cumulative returns over 60 trading days`}
            onPointerMove={expanded ? (event) => {
                const rect = event.currentTarget.getBoundingClientRect();
                const position = (event.clientX - rect.left) / rect.width * width;
                setHover(Math.max(0, Math.min(base.length - 1, Math.round((position - pad) / (width - 2 * pad) * (base.length - 1)))));
            } : undefined} onPointerLeave={() => setHover(null)}>
            {(expanded ? [min, (max + min) / 2, max] : []).map((value, i) => <g key={i}>
                <line x1={pad} x2={width - pad} y1={y(value)} y2={y(value)} stroke="var(--border)" />
                <text x={pad - 5} y={y(value) + 4} textAnchor="end" fontSize="10" fill="var(--text-muted)">{(value * 100).toFixed(0)}%</text>
            </g>)}
            <line x1={pad} x2={width - pad} y1={y(0)} y2={y(0)} stroke="var(--text-muted)" strokeDasharray="3 4" opacity=".3" />
            <polyline points={line(base)} fill="none" stroke="#94a3b8" strokeWidth="2" vectorEffect="non-scaling-stroke" />
            <polyline points={line(match)} fill="none" stroke="#10b981" strokeWidth="2" vectorEffect="non-scaling-stroke" />
            {expanded && hover !== null && <line x1={x(hover)} x2={x(hover)} y1="8" y2={height - 8} stroke="var(--text-muted)" strokeDasharray="3 3" />}
        </svg>
        {expanded && <>
            <div className="mt-1 flex justify-between text-xs text-[var(--text-muted)]"><span>{dates[0]}</span><span>{dates.at(-1)}</span></div>
            <label className="mt-3 block text-xs text-[var(--text-muted)]">Inspect date
                <input type="range" className="mt-1 block w-full accent-emerald-500" min={0} max={base.length - 1} value={index} onChange={event => setHover(Number(event.target.value))} aria-label="Comparison date" />
            </label>
        </>}
    </div>;
}

interface Props {
    ticker: string;
    watchlist: string[];
    unlocked: boolean;
    onSelect: (ticker: string) => void;
    onAdd: (ticker: string) => void;
    onRemove: (ticker: string) => void;
}

export default function SimilarStocksPanel({ ticker, watchlist, unlocked, onSelect, onAdd, onRemove }: Props) {
    const [data, setData] = useState<SimilarStocksResponse | null>(null);
    const [error, setError] = useState(false);
    const [retry, setRetry] = useState(0);
    const [selected, setSelected] = useState<string | null>(null);
    useEffect(() => {
        const controller = new AbortController();
        fetchSimilarStocks(ticker, controller.signal).then(result => {
            if (!controller.signal.aborted) { setData(result); setError(false); }
        }).catch(() => { if (!controller.signal.aborted) setError(true); });
        return () => controller.abort();
    }, [ticker, retry]);
    const current = data?.ticker === ticker ? data : null;
    const peer = current?.matches.find(item => item.ticker === selected);
    const emptyMessage = current?.status === "unsupported_market" ? "Similar stocks are currently available for US stocks."
        : current?.status === "unsupported_security" ? "Similarity is available for covered common stocks."
        : current?.status === "insufficient_history" ? "Not enough recent adjusted price history for a 60-day comparison."
        : "No strong matches over the past 60 trading days.";
    return <section className="research-panel p-4 sm:p-5" aria-labelledby="similar-stocks-title">
        <header className="mb-4 flex flex-wrap items-start justify-between gap-3">
            <div><h2 id="similar-stocks-title" className="text-lg font-semibold tracking-tight">Similar price moves</h2>
                <p className="mt-1 text-xs text-[var(--text-muted)]">Past 60 trading days{current?.as_of ? ` · As of ${current.as_of}` : ""}</p></div>
            <details className="max-w-md text-xs text-[var(--text-muted)]"><summary className="cursor-pointer">How stocks are matched</summary>
                <p className="mt-2 leading-relaxed">Ranked by daily adjusted-return correlation among covered US common stocks with complete history. Similar moves do not imply similar businesses or future returns. The window stays fixed when you change the main chart.</p>
            </details>
        </header>
        {error ? <div role="alert" className="text-sm">Unable to load similar stocks. <button className="research-link" onClick={() => { setError(false); setRetry(value => value + 1); }}>Retry</button></div>
            : !current ? <div role="status" className="animate-pulse py-8 text-sm text-[var(--text-muted)]">Finding similar price moves…</div>
            : current.matches.length === 0 ? <p className="py-5 text-sm text-[var(--text-muted)]">{emptyMessage}</p>
            : <>
                <div className="mb-2 flex flex-wrap justify-between gap-2 text-xs text-[var(--text-muted)]">
                    <span><span className="mr-1 inline-block h-0.5 w-3 bg-slate-400" />{symbol(ticker)}<span className="ml-4 mr-1 inline-block h-0.5 w-3 bg-emerald-500" />Similar stock</span>
                    <span>60-day return · {symbol(ticker)} {percent(current.target_returns.at(-1)!)}</span>
                </div>
                <ul className="divide-y divide-[var(--border)]">
                    {current.matches.map(item => {
                        const saved = watchlist.includes(item.ticker);
                        return <li key={item.ticker} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-2 py-3 sm:grid-cols-[minmax(0,1fr)_minmax(120px,180px)_80px_auto]">
                            <div className="min-w-0"><button className="max-w-full truncate text-left text-sm font-semibold hover:underline" onClick={() => onSelect(item.ticker)}>{symbol(item.ticker)} <span className="font-normal text-[var(--text-muted)]">{item.name}</span></button><p className="truncate text-xs text-[var(--text-muted)]">{item.industry || "Industry unavailable"}</p></div>
                            <div className="col-start-1 row-start-2 sm:col-auto sm:row-auto"><ComparisonChart base={current.target_returns} match={item.returns} dates={current.dates} ticker={ticker} peer={item.ticker} /></div>
                            <span className={`col-start-2 row-start-2 text-right text-sm tabular-nums sm:col-auto sm:row-auto ${item.returns.at(-1)! >= 0 ? "text-emerald-600 dark:text-emerald-400" : "text-rose-600 dark:text-rose-400"}`}>{percent(item.returns.at(-1)!)}</span>
                            <div className="col-start-2 row-start-1 flex items-center gap-2 sm:col-auto sm:row-auto"><button className="secondary-button px-2 py-1 text-xs" aria-expanded={selected === item.ticker} aria-controls="similar-comparison" onClick={() => setSelected(selected === item.ticker ? null : item.ticker)}>{selected === item.ticker ? <>Close <ChevronUp size={13} /></> : <>Compare <ChevronDown size={13} /></>}</button>
                                <button className="rounded-md p-2 hover:bg-[var(--surface-muted)]" aria-label={unlocked ? `${saved ? "Remove" : "Add"} ${symbol(item.ticker)} ${saved ? "from" : "to"} watchlist` : "Unlock to edit watchlist"} aria-pressed={saved} onClick={() => saved ? onRemove(item.ticker) : onAdd(item.ticker)}><Star size={17} className={saved ? "fill-amber-400 text-amber-500" : "text-[var(--text-muted)]"} /></button></div>
                        </li>;
                    })}
                </ul>
                {peer && <div id="similar-comparison" className="mt-3 rounded-lg border bg-[var(--surface-muted)] p-3 sm:p-4">
                    <div className="mb-3 flex items-center justify-between gap-2"><h3 className="text-sm font-medium">{symbol(ticker)} vs {symbol(peer.ticker)}</h3><span className="text-xs text-[var(--text-muted)]">Cumulative return · starts at 0%</span></div>
                    <ComparisonChart key={peer.ticker} base={current.target_returns} match={peer.returns} dates={current.dates} ticker={ticker} peer={peer.ticker} expanded />
                </div>}
            </>}
    </section>;
}
