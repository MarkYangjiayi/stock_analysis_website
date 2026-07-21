"use client";

import { useState } from "react";
import { Plus, Star, Trash2 } from "lucide-react";

interface WatchlistSidebarProps {
    currentTicker: string;
    onSelectTicker: (ticker: string) => void;
    watchlist: string[];
    onAdd: (ticker: string) => void;
    onRemove: (ticker: string) => void;
    compact?: boolean;
}

export default function WatchlistSidebar({ currentTicker, onSelectTicker, watchlist, onAdd, onRemove, compact = false }: WatchlistSidebarProps) {
    const [newTicker, setNewTicker] = useState("");

    const handleAdd = (event: React.FormEvent) => {
        event.preventDefault();
        const ticker = newTicker.trim().toUpperCase();
        if (!ticker) return;
        onAdd(ticker);
        setNewTicker("");
    };

    if (compact) {
        return (
            <section className="surface-panel p-3 md:hidden" aria-label="Watchlist">
                <div className="flex items-center gap-2 overflow-x-auto pb-2">
                    <span className="flex shrink-0 items-center gap-1.5 px-1 text-xs font-black uppercase tracking-wide text-slate-500"><Star size={14} /> Watchlist</span>
                    {watchlist.map((ticker) => (
                        <button key={ticker} type="button" onClick={() => onSelectTicker(ticker)} className={`shrink-0 rounded-full border px-3 py-1.5 font-mono text-xs font-bold ${ticker === currentTicker ? "border-emerald-400 bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300" : "bg-white text-slate-600 dark:bg-slate-900 dark:text-slate-300"}`}>
                            {ticker.replace(".US", "")}
                        </button>
                    ))}
                </div>
                <form onSubmit={handleAdd} className="mt-1 flex gap-2">
                    <label className="sr-only" htmlFor="mobile-watchlist-ticker">Add ticker to watchlist</label>
                    <input id="mobile-watchlist-ticker" className="control-field py-2" value={newTicker} onChange={(event) => setNewTicker(event.target.value)} placeholder="Add ticker" />
                    <button type="submit" className="primary-button min-h-9 px-3" disabled={!newTicker.trim()} aria-label="Add ticker"><Plus size={16} /></button>
                </form>
            </section>
        );
    }

    return (
        <aside className="flex h-full w-64 shrink-0 flex-col border-r bg-white dark:bg-[#10171d]" aria-label="Watchlist">
            <div className="border-b p-4">
                <div className="flex items-center gap-2 text-sm font-black"><Star className="text-emerald-500" size={17} /> Watchlist</div>
                <p className="mt-1 text-xs text-slate-500">Stored on this device</p>
                <form onSubmit={handleAdd} className="mt-4 flex gap-2">
                    <label className="sr-only" htmlFor="watchlist-ticker">Add ticker to watchlist</label>
                    <input id="watchlist-ticker" className="control-field min-w-0 py-2" value={newTicker} onChange={(event) => setNewTicker(event.target.value)} placeholder="Add ticker" />
                    <button type="submit" className="primary-button min-h-9 px-3" disabled={!newTicker.trim()} aria-label="Add ticker"><Plus size={16} /></button>
                </form>
            </div>
            <div className="custom-scrollbar flex-1 space-y-1 overflow-y-auto p-2">
                {watchlist.map((ticker) => {
                    const selected = ticker === currentTicker;
                    return (
                        <div key={ticker} className={`group flex items-center gap-1 rounded-xl border ${selected ? "border-emerald-300 bg-emerald-50 dark:border-emerald-800 dark:bg-emerald-950/30" : "border-transparent hover:bg-slate-50 dark:hover:bg-slate-800/60"}`}>
                            <button type="button" onClick={() => onSelectTicker(ticker)} className={`min-w-0 flex-1 truncate px-3 py-3 text-left font-mono text-xs font-bold ${selected ? "text-emerald-700 dark:text-emerald-300" : "text-slate-600 dark:text-slate-300"}`}>
                                {ticker}
                            </button>
                            <button type="button" onClick={() => onRemove(ticker)} className="mr-2 rounded-lg p-1.5 text-slate-400 opacity-0 transition-opacity hover:bg-rose-50 hover:text-rose-500 focus:opacity-100 group-hover:opacity-100 dark:hover:bg-rose-950/30" aria-label={`Remove ${ticker} from watchlist`}><Trash2 size={14} /></button>
                        </div>
                    );
                })}
                {watchlist.length === 0 && <p className="px-3 py-12 text-center text-xs leading-5 text-slate-500">Your watchlist is empty.<br />Add a ticker above.</p>}
            </div>
        </aside>
    );
}
