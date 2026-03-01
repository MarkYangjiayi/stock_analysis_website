"use client";

import React, { useState, useEffect, useMemo } from 'react';
import { Plus, Trash2, TrendingUp, ArrowUpDown, CloudOff } from 'lucide-react';
import { fetchBatchFactors, BatchFactorScore } from '@/lib/api';

interface WatchlistSidebarProps {
    currentTicker: string;
    onSelectTicker: (ticker: string) => void;
    watchlist: string[];
    onAdd: (ticker: string) => void;
    onRemove: (ticker: string) => void;
}

const WatchlistSidebar: React.FC<WatchlistSidebarProps> = ({ currentTicker, onSelectTicker, watchlist, onAdd, onRemove }) => {
    const [newTicker, setNewTicker] = useState('');
    const [mounted, setMounted] = useState(false);

    type SortDimension = 'Default' | 'value' | 'quality' | 'growth' | 'health' | 'momentum';
    const [sortBy, setSortBy] = useState<SortDimension>('Default');
    const [factorScores, setFactorScores] = useState<Record<string, BatchFactorScore>>({});
    const [loadingScores, setLoadingScores] = useState(false);

    useEffect(() => {
        setMounted(true);
    }, []);

    // Effect to fetch scores whenever watchlist array changes
    useEffect(() => {
        if (!watchlist || watchlist.length === 0) {
            setFactorScores({});
            return;
        }

        const loadScores = async () => {
            setLoadingScores(true);
            try {
                const results = await fetchBatchFactors(watchlist);
                const scoresMap: Record<string, BatchFactorScore> = {};
                results.forEach(res => {
                    scoresMap[res.ticker] = res;
                });
                setFactorScores(scoresMap);
            } catch (err) {
                console.error("Failed to batch fetch factor scores", err);
            } finally {
                setLoadingScores(false);
            }
        };

        loadScores();
    }, [watchlist]);

    const handleAdd = (e: React.FormEvent) => {
        e.preventDefault();
        const ticker = newTicker.trim().toUpperCase();
        if (ticker) {
            onAdd(ticker);
            setNewTicker('');
        }
    };

    const handleRemove = (e: React.MouseEvent, tickerToRemove: string) => {
        e.stopPropagation();
        onRemove(tickerToRemove);
    };

    // Compute sorted watchlist
    const sortedWatchlist = useMemo(() => {
        if (sortBy === 'Default') return [...watchlist];

        return [...watchlist].sort((a, b) => {
            const scoreA = factorScores[a]?.factor_scores[sortBy as keyof typeof factorScores[0]['factor_scores']] || 0;
            const scoreB = factorScores[b]?.factor_scores[sortBy as keyof typeof factorScores[0]['factor_scores']] || 0;
            return scoreB - scoreA; // Descending
        });
    }, [watchlist, factorScores, sortBy]);

    // Helper to get color code for badge
    const getScoreColor = (score: number) => {
        if (score === 0) return 'bg-gray-800 text-gray-500 border-gray-700'; // Missing data
        if (score >= 80) return 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30';
        if (score >= 60) return 'bg-teal-500/20 text-teal-400 border-teal-500/30';
        if (score >= 40) return 'bg-yellow-500/20 text-yellow-500 border-yellow-500/30';
        return 'bg-red-500/20 text-red-400 border-red-500/30';
    };

    // Before mounting on client, render a placeholder with same dimensions to avoid hydration mismatch
    if (!mounted) {
        return (
            <div className="w-72 bg-white dark:bg-[#151922] border-r border-gray-200 dark:border-gray-800 flex flex-col h-full shrink-0 transition-colors duration-300"></div>
        );
    }

    return (
        <div className="w-72 bg-white dark:bg-[#151922] border-r border-gray-200 dark:border-gray-800 flex flex-col h-full shrink-0 transition-colors duration-300">
            <div className="p-5 border-b border-gray-200 dark:border-gray-800 relative space-y-4 transition-colors duration-300">
                <div className="flex items-center gap-3 text-slate-800 dark:text-gray-200 font-extrabold tracking-wide">
                    <div className="p-2 bg-emerald-100 dark:bg-emerald-500/10 rounded-lg">
                        <TrendingUp className="text-emerald-600 dark:text-emerald-400" size={18} />
                    </div>
                    <h2>Watchlist</h2>
                </div>

                <form onSubmit={handleAdd} className="flex gap-2">
                    <input
                        type="text"
                        value={newTicker}
                        onChange={(e) => setNewTicker(e.target.value)}
                        placeholder="Add ticker..."
                        className="w-full bg-slate-50 dark:bg-[#1e222d] text-slate-900 dark:text-white placeholder-slate-400 dark:placeholder-gray-500 text-sm px-4 py-2.5 rounded-xl border border-gray-200 dark:border-gray-800 focus:outline-none focus:border-emerald-500/50 focus:ring-1 focus:ring-emerald-500/50 transition-all shadow-sm dark:shadow-inner"
                    />
                    <button
                        type="submit"
                        disabled={!newTicker.trim()}
                        className="bg-emerald-500 hover:bg-emerald-400 disabled:opacity-30 disabled:hover:bg-emerald-500 text-white p-2.5 rounded-xl transition-all flex items-center justify-center shadow-lg active:scale-95 shrink-0"
                    >
                        <Plus size={18} strokeWidth={3} />
                    </button>
                </form>

                {/* Sort Dropdown */}
                <div className="flex items-center gap-2 pt-2">
                    <ArrowUpDown size={14} className="text-slate-400 dark:text-gray-500 shrink-0" />
                    <select
                        value={sortBy}
                        onChange={(e) => setSortBy(e.target.value as SortDimension)}
                        className="w-full bg-slate-50 dark:bg-[#1e222d] text-slate-600 dark:text-gray-300 text-xs font-semibold px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-800 focus:outline-none focus:border-emerald-500/50 appearance-none cursor-pointer hover:bg-slate-100 dark:hover:bg-[#252a36] transition-colors"
                        style={{ backgroundImage: `url("data:image/svg+xml,%3csvg xmlns='http://www.w3.org/2000/svg' fill='none' viewBox='0 0 20 20'%3e%3cpath stroke='%236b7280' stroke-linecap='round' stroke-linejoin='round' stroke-width='1.5' d='M6 8l4 4 4-4'/%3e%3c/svg%3e")`, backgroundPosition: `right 0.5rem center`, backgroundRepeat: `no-repeat`, backgroundSize: `1.5em 1.5em`, paddingRight: `2rem` }}
                    >
                        <option value="Default">Added Order</option>
                        <option value="value">Value Score</option>
                        <option value="quality">Quality Score</option>
                        <option value="growth">Growth Score</option>
                        <option value="health">Health Score</option>
                        <option value="momentum">Momentum Score</option>
                    </select>
                </div>
            </div>

            <div className="flex-1 overflow-y-auto p-3 space-y-1.5 !scrollbar-hide relative">
                {/* Optional faded overlay when loading scores */}
                {loadingScores && (
                    <div className="absolute top-0 right-0 p-2">
                        <div className="w-3 h-3 bg-emerald-500/50 rounded-full animate-ping"></div>
                    </div>
                )}

                {sortedWatchlist.map((ticker) => {
                    const isSelected = ticker === currentTicker;
                    const factorObj = factorScores[ticker];

                    let activeScore = null;
                    let isMissingData = false;

                    if (factorObj) {
                        const sums = Object.values(factorObj.factor_scores).reduce((a, b) => a + b, 0);
                        if (sums === 0) {
                            isMissingData = true;
                        } else if (sortBy !== 'Default') {
                            activeScore = factorObj.factor_scores[sortBy as keyof typeof factorObj.factor_scores];
                        }
                    } else if (!loadingScores) {
                        isMissingData = true;
                    }

                    return (
                        <div
                            key={ticker}
                            onClick={() => onSelectTicker(ticker)}
                            className={`group flex items-center justify-between px-4 py-3.5 rounded-xl cursor-pointer transition-all duration-200 border-l-4 ${isSelected
                                ? 'bg-emerald-50 dark:bg-emerald-500/10 border-emerald-500 text-emerald-600 dark:text-emerald-400 font-bold shadow-sm'
                                : 'border-transparent text-slate-600 dark:text-gray-300 hover:bg-slate-50 dark:hover:bg-[#1e222d] hover:text-slate-900 dark:hover:text-gray-100'
                                }`}
                        >
                            <span className="tracking-wider text-sm whitespace-nowrap overflow-hidden text-ellipsis mr-2">{ticker}</span>

                            <div className="flex items-center gap-2">
                                {/* Score Badge */}
                                {isMissingData ? (
                                    <div className="flex items-center justify-center gap-1 text-[10px] font-mono px-2 py-1 rounded border bg-slate-100 text-slate-400 border-slate-200 dark:bg-gray-800 dark:text-gray-500 dark:border-gray-700 whitespace-nowrap" title="Sync required to view score">
                                        <CloudOff size={10} /> <span>No Data</span>
                                    </div>
                                ) : (
                                    sortBy !== 'Default' && activeScore !== null && (
                                        <div className={`text-xs font-mono font-bold px-2 py-0.5 rounded border ${getScoreColor(activeScore)}`}>
                                            {activeScore}
                                        </div>
                                    )
                                )}

                                {/* Delete Button (appears on hover) */}
                                <button
                                    onClick={(e) => handleRemove(e, ticker)}
                                    className={`text-gray-600 hover:text-red-400 hover:bg-red-400/10 transition-all p-1.5 rounded-lg shrink-0 ${isSelected ? 'opacity-100' : 'hidden group-hover:block'
                                        }`}
                                    title="Remove from watchlist"
                                >
                                    <Trash2 size={15} />
                                </button>
                            </div>
                        </div>
                    );
                })}
                {watchlist.length === 0 && (
                    <div className="text-center py-10 flex flex-col items-center justify-center space-y-2 opacity-60">
                        <div className="w-12 h-12 rounded-full bg-slate-100 dark:bg-gray-800/50 flex items-center justify-center mb-2">
                            <TrendingUp className="text-slate-400 dark:text-gray-600" size={20} />
                        </div>
                        <p className="text-slate-500 dark:text-gray-500 text-sm font-medium">Empty Watchlist</p>
                    </div>
                )}
            </div>
        </div>
    );
};

export default WatchlistSidebar;
