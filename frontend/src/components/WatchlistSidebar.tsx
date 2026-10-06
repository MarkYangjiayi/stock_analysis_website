"use client";

import { useState } from "react";
import { LockKeyhole, Plus, Star, Trash2 } from "lucide-react";

interface WatchlistSidebarProps {
    currentTicker: string;
    onSelectTicker: (ticker: string) => void;
    watchlist: string[];
    onAdd: (ticker: string) => void;
    onRemove: (ticker: string) => void;
    compact?: boolean;
    readOnly?: boolean;
    onUnlock?: () => void;
}

export default function WatchlistSidebar({ currentTicker, onSelectTicker, watchlist, onAdd, onRemove, compact = false, readOnly = false, onUnlock }: WatchlistSidebarProps) {
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
            <section className="border-b pb-2 xl:hidden" aria-label="Watchlist">
                <div className="flex items-center gap-2 overflow-x-auto">
                    <span className="eyebrow flex shrink-0 items-center gap-1.5 px-1"><Star size={14} /> Watchlist</span>
                    {watchlist.map((ticker) => (
                        <div key={ticker} className={`flex shrink-0 items-center rounded-full border ${ticker === currentTicker ? "border-accent bg-accent-soft text-accent-strong" : "bg-surface text-fg-muted hover:border-line-strong hover:text-fg"}`}>
                            <button type="button" onClick={() => onSelectTicker(ticker)} className="min-h-11 py-1.5 pl-3 pr-1 font-mono text-xs font-semibold">
                                {ticker.replace(".US", "")}
                            </button>
                            {!readOnly && <button type="button" onClick={() => onRemove(ticker)} className="mr-1 flex h-7 w-7 items-center justify-center rounded-full text-fg-muted transition-colors hover:bg-danger/10 hover:text-danger focus-visible:bg-danger/10 focus-visible:text-danger" aria-label={`Remove ${ticker} from watchlist`}>
                                <Trash2 size={12} />
                            </button>}
                        </div>
                    ))}
                    {readOnly && <button type="button" className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full border text-fg-muted hover:bg-surface-muted" onClick={onUnlock} aria-label="Unlock to edit watchlist"><LockKeyhole size={15} /></button>}
                </div>
                {!readOnly && <form onSubmit={handleAdd} className="mt-2 flex gap-2">
                    <label className="sr-only" htmlFor="mobile-watchlist-ticker">Add ticker to watchlist</label>
                    <input id="mobile-watchlist-ticker" className="control-field py-2" value={newTicker} onChange={(event) => setNewTicker(event.target.value)} placeholder="Add ticker" />
                    <button type="submit" className="primary-button min-h-9 px-3" disabled={!newTicker.trim()} aria-label="Add ticker"><Plus size={16} /></button>
                </form>}
            </section>
        );
    }

    return (
        <aside className="flex h-full w-52 shrink-0 flex-col border-r bg-surface" aria-label="Watchlist">
            <div className="border-b p-3">
                <div className="flex items-center justify-between gap-2"><span className="eyebrow flex items-center gap-1.5"><Star className="text-accent" size={13} /> Watchlist</span>{readOnly && <button type="button" onClick={onUnlock} className="rounded-md p-1 text-fg-muted hover:bg-surface-muted hover:text-fg" aria-label="Unlock personal workspace"><LockKeyhole size={15} /></button>}</div>
                <p className="mt-1 text-xs text-fg-muted">{readOnly ? "Preview from this browser · locked" : "Synced to personal workspace"}</p>
                {readOnly ? <button type="button" className="secondary-button mt-3 w-full min-h-9 px-3 text-xs" onClick={onUnlock}><LockKeyhole size={14} /> Unlock to edit</button> : <form onSubmit={handleAdd} className="mt-3 flex gap-2">
                    <label className="sr-only" htmlFor="watchlist-ticker">Add ticker to watchlist</label>
                    <input id="watchlist-ticker" className="control-field min-w-0 py-1.5 font-mono" value={newTicker} onChange={(event) => setNewTicker(event.target.value)} placeholder="Add ticker" />
                    <button type="submit" className="primary-button min-h-9 px-3" disabled={!newTicker.trim()} aria-label="Add ticker"><Plus size={16} /></button>
                </form>}
            </div>
            <div className="custom-scrollbar flex-1 overflow-y-auto">
                {watchlist.map((ticker) => {
                    const selected = ticker === currentTicker;
                    return (
                        <div key={ticker} className={`group flex items-center gap-1 border-b ${selected ? "bg-accent-soft shadow-[inset_2px_0_0_var(--brand)]" : "hover:bg-surface-muted"}`}>
                            <button type="button" onClick={() => onSelectTicker(ticker)} aria-current={selected ? "true" : undefined} className={`min-h-9 min-w-0 flex-1 truncate px-3 py-2 text-left font-mono text-xs ${selected ? "font-semibold text-fg" : "font-medium text-fg-muted group-hover:text-fg"}`}>
                                {ticker}
                            </button>
                            {!readOnly && <button type="button" onClick={() => onRemove(ticker)} className="mr-1.5 rounded-md p-1 text-fg-muted opacity-0 transition-opacity hover:bg-danger/10 hover:text-danger focus:opacity-100 group-hover:opacity-100" aria-label={`Remove ${ticker} from watchlist`}><Trash2 size={14} /></button>}
                        </div>
                    );
                })}
                {watchlist.length === 0 && <p className="px-3 py-12 text-center text-xs leading-5 text-fg-muted">Your watchlist is empty.<br />Add a ticker above.</p>}
            </div>
        </aside>
    );
}
