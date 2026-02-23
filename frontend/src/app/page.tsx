"use client";

import { useState, useEffect } from 'react';
import { Search, Activity, AlertCircle, Plus, Check } from 'lucide-react';
import { fetchStockData, StockDataResponse } from '@/lib/api';
import StockChart from '@/components/StockChart';
import ValuationDashboard from '@/components/ValuationDashboard';
import AIReport from '@/components/AIReport';
import FinancialTrendChart from '@/components/FinancialTrendChart';
import WatchlistSidebar from '@/components/WatchlistSidebar';

export default function Home() {
  const [ticker, setTicker] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [stockData, setStockData] = useState<StockDataResponse | null>(null);

  const [watchlist, setWatchlist] = useState<string[]>([]);
  const DEFAULT_WATCHLIST = ['AAPL.US', 'AMAT.US', 'ASTS.US', 'UNH.US'];

  useEffect(() => {
    const stored = localStorage.getItem('my_watchlist');
    if (stored) {
      try {
        const parsed = JSON.parse(stored);
        if (Array.isArray(parsed) && parsed.length > 0) {
          setWatchlist(parsed);
          return;
        }
      } catch (e) { }
    }
    setWatchlist(DEFAULT_WATCHLIST);
    localStorage.setItem('my_watchlist', JSON.stringify(DEFAULT_WATCHLIST));
  }, []);

  const saveWatchlist = (newList: string[]) => {
    setWatchlist(newList);
    localStorage.setItem('my_watchlist', JSON.stringify(newList));
  };

  const handleAddWatchlist = (tickerToAdd: string) => {
    if (tickerToAdd && !watchlist.includes(tickerToAdd)) {
      saveWatchlist([tickerToAdd, ...watchlist]);
    }
  };

  const handleRemoveWatchlist = (tickerToRemove: string) => {
    saveWatchlist(watchlist.filter(t => t !== tickerToRemove));
  };

  const executeSearch = async (searchTicker: string) => {
    if (!searchTicker.trim()) return;

    setLoading(true);
    setError('');
    setStockData(null);
    setTicker(searchTicker);

    try {
      // Send GET /api/stocks/{ticker}
      const data = await fetchStockData(searchTicker.toUpperCase());
      setStockData(data);
    } catch (err: any) {
      if (err.response?.status === 404) {
        setError(`Data for ${searchTicker} not found. Ensure it has been synchronized first.`);
      } else {
        setError(err.message || 'Failed to fetch stock data');
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (typeof window !== 'undefined') {
      const params = new URLSearchParams(window.location.search);
      const qTicker = params.get('ticker');
      if (qTicker) {
        executeSearch(qTicker);
        // Clean up the URL state
        window.history.replaceState({}, '', '/');
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleSearch = async (e: React.FormEvent) => {
    e.preventDefault();
    await executeSearch(ticker);
  };

  return (
    <div className="flex h-screen overflow-hidden bg-[#0E1117] text-gray-100 font-sans selection:bg-emerald-500/30">
      <div className="hidden md:block h-full z-50">
        <WatchlistSidebar
          currentTicker={ticker}
          onSelectTicker={executeSearch}
          watchlist={watchlist}
          onAdd={handleAddWatchlist}
          onRemove={handleRemoveWatchlist}
        />
      </div>
      <main className="flex-1 overflow-y-auto p-6 md:p-8 relative">
        <div className="max-w-6xl mx-auto space-y-12 pb-12">
          {/* Header Section */}
          <div className="text-center space-y-4 pt-8 animate-in fade-in slide-in-from-top-4 duration-700">
            <div className="flex items-center justify-center space-x-3 text-emerald-400">
              <Activity strokeWidth={2.5} size={48} />
              <h1 className="text-5xl font-extrabold tracking-tight text-white drop-shadow-md">
                <span className="text-transparent bg-clip-text bg-gradient-to-r from-emerald-400 to-teal-200">Quantify</span> Platform
              </h1>
            </div>
            <p className="text-gray-400 text-lg font-medium max-w-xl mx-auto">
              Professional Grade Stock Analysis with Lightweight Charts & Real-time MACD/RSI Computations.
            </p>
          </div>

          {/* Search Bar Section */}
          <form onSubmit={handleSearch} className="max-w-3xl mx-auto flex flex-col sm:flex-row gap-4 relative z-10 animate-in fade-in zoom-in-95 duration-500 delay-150 fill-mode-both">
            <div className="relative flex-1 group">
              <div className="absolute -inset-0.5 bg-gradient-to-r from-emerald-400 to-teal-500 rounded-xl blur opacity-20 group-hover:opacity-40 transition duration-500"></div>
              <div className="relative flex items-center bg-[#151922] rounded-xl border border-gray-800 transition-all focus-within:border-emerald-500/50">
                <Search className="absolute left-5 text-gray-400 group-focus-within:text-emerald-400 transition-colors" size={22} />
                <input
                  type="text"
                  value={ticker}
                  onChange={(e) => setTicker(e.target.value)}
                  placeholder="Enter stock ticker (e.g., AAPL.US, TSLA.US)"
                  className="w-full bg-transparent text-white placeholder-gray-500 pl-14 pr-4 py-4 rounded-xl focus:outline-none text-lg h-full"
                />
              </div>
            </div>
            <button
              type="submit"
              disabled={loading || !ticker.trim()}
              className="flex items-center justify-center gap-2 bg-emerald-500 hover:bg-emerald-400 disabled:bg-emerald-500/20 disabled:text-emerald-500/40 text-white min-w-[140px] px-8 py-4 rounded-xl font-bold transition-all shadow-[0_0_15px_rgba(16,185,129,0.5)] active:scale-[0.98]"
            >
              {loading ? (
                <div className="animate-spin rounded-full h-5 w-5 border-2 border-white/30 border-t-white" />
              ) : (
                'Analyze'
              )}
            </button>
          </form>

          {/* Screener Entry Section */}
          <div className="text-center animate-in fade-in zoom-in-95 duration-500 delay-200 fill-mode-both">
            <a href="/screener" className="inline-flex items-center gap-2 px-6 py-3 rounded-xl border border-emerald-500/30 text-emerald-400 font-medium hover:bg-emerald-500/10 transition-colors shadow-[0_0_10px_rgba(16,185,129,0.2)]">
              <Activity size={18} />
              Open Global Market Screener
            </a>
          </div>

          {/* State Banners */}
          {error && (
            <div className="max-w-3xl mx-auto bg-red-500/10 border border-red-500/20 rounded-xl p-5 flex items-start gap-3 animate-in fade-in slide-in-from-top-4">
              <AlertCircle className="text-red-400 shrink-0 mt-0.5" size={22} />
              <p className="text-red-200 font-medium">{error}</p>
            </div>
          )}

          {/* Loading State Overlay */}
          {loading && (
            <div className="flex flex-col items-center justify-center py-24 space-y-8 animate-in fade-in zoom-in duration-500">
              <div className="relative flex items-center justify-center">
                <div className="w-20 h-20 border-4 border-emerald-500/20 rounded-full animate-ping absolute"></div>
                <div className="w-20 h-20 border-4 border-emerald-500/20 border-t-emerald-500 rounded-full animate-[spin_1.5s_linear_infinite] relative z-10 shadow-[0_0_15px_rgba(16,185,129,0.5)]"></div>
                <Search className="absolute text-emerald-500/50 animate-pulse z-0" size={24} />
              </div>
              <div className="text-center space-y-3">
                <h3 className="text-2xl font-black tracking-widest text-transparent bg-clip-text bg-gradient-to-r from-emerald-400 to-teal-200">
                  INITIALIZING
                </h3>
                <p className="text-gray-400 text-sm max-w-sm mx-auto leading-relaxed bg-[#191d26] p-4 rounded-xl border border-gray-800 shadow-inner">
                  首次查询该股票，正在从上游数据源<span className="text-emerald-400 font-bold">同步十年财务与行情数据</span>，请稍候...
                </p>
              </div>
            </div>
          )}

          {/* Data Visualization Dashboard */}
          {stockData && !loading && (
            <div className="space-y-8 animate-in fade-in slide-in-from-bottom-8 duration-700 ease-out fill-mode-both delay-100">
              {/* Top Profile Card */}
              <div className="bg-[#191D26] border border-gray-800 rounded-2xl p-6 md:p-8 shadow-2xl relative overflow-hidden">
                <div className="absolute top-0 right-0 p-32 bg-emerald-500/5 blur-[120px] rounded-full pointer-events-none" />

                <div className="flex flex-col md:flex-row justify-between items-start md:items-end gap-6 relative z-10">
                  <div className="space-y-4">
                    <div className="flex flex-wrap items-center gap-4">
                      <h2 className="text-4xl font-extrabold text-white tracking-tight">{stockData.profile.name}</h2>
                      {!watchlist.includes(stockData.profile.ticker) ? (
                        <button
                          onClick={() => handleAddWatchlist(stockData.profile.ticker)}
                          className="px-3 py-1.5 bg-emerald-500/20 hover:bg-emerald-500/30 text-emerald-400 text-sm font-bold rounded-lg border border-emerald-500/30 transition-colors flex items-center gap-1.5 shadow-sm"
                        >
                          <Plus size={16} strokeWidth={3} /> 加入自选
                        </button>
                      ) : (
                        <span className="px-3 py-1.5 bg-gray-800/80 text-gray-400 text-sm font-bold rounded-lg border border-gray-700 flex items-center gap-1.5">
                          <Check size={16} strokeWidth={3} className="text-emerald-500" /> 已关注
                        </span>
                      )}
                    </div>
                    <div className="flex flex-wrap gap-2 text-sm font-medium text-gray-300">
                      <span className="bg-[#2B2B43] px-3 py-1.5 rounded-lg border border-gray-700 shadow-sm text-emerald-400 tracking-wider">
                        {stockData.profile.ticker}
                      </span>
                      <span className="bg-[#2B2B43] px-3 py-1.5 rounded-lg border border-gray-700 shadow-sm">
                        {stockData.profile.exchange}
                      </span>
                      <span className="bg-[#2B2B43] px-3 py-1.5 rounded-lg border border-gray-700 shadow-sm">
                        {stockData.profile.sector}
                      </span>
                    </div>
                  </div>

                  <div className="text-left md:text-right w-full md:w-auto">
                    <div className="bg-[#151922] border border-gray-800 p-4 rounded-xl shadow-inner inline-block min-w-full md:min-w-[200px]">
                      <p className="text-sm font-medium text-gray-500 uppercase tracking-wider mb-1">Latest Close</p>
                      <p className="text-4xl font-black text-white">
                        <span className="text-xl text-gray-400 font-medium mr-1">{stockData.profile.currency}</span>
                        {stockData.historical_data[stockData.historical_data.length - 1]?.close?.toFixed(2)}
                      </p>

                      {/* Simple delta visualization derived from t vs t-1 */}
                      {(() => {
                        const latest = stockData.historical_data[stockData.historical_data.length - 1];
                        const prev = stockData.historical_data[stockData.historical_data.length - 2];
                        if (!latest || !prev) return null;

                        const diff = latest.close - prev.close;
                        const isUp = diff >= 0;

                        return (
                          <p className={`text-sm mt-1 font-bold ${isUp ? 'text-emerald-400' : 'text-red-400'}`}>
                            {isUp ? '▲' : '▼'} {Math.abs(diff).toFixed(2)} vs Prev Day
                          </p>
                        );
                      })()}
                    </div>
                  </div>
                </div>

                <div className="mt-6 pt-6 border-t border-gray-800/80">
                  <p className="text-gray-400 text-sm leading-relaxed line-clamp-3 hover:line-clamp-none transition-all duration-300">
                    {stockData.profile.description}
                  </p>
                </div>
              </div>

              {/* Valuation Dashboard Panel & AI Report Card grid */}
              {stockData.valuation_metrics && (
                <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 animate-in fade-in slide-in-from-bottom-4 duration-700 ease-out fill-mode-both delay-300 items-start">
                  <div className="lg:col-span-2 flex w-full">
                    <ValuationDashboard metrics={stockData.valuation_metrics} />
                  </div>
                  <div className="lg:col-span-1 flex w-full">
                    <div className="w-full flex h-[620px]">
                      <AIReport ticker={stockData.profile.ticker} />
                    </div>
                  </div>
                </div>
              )}

              {/* Interactive Chart Container */}
              <div className="bg-[#191D26] border border-gray-800 rounded-2xl shadow-2xl overflow-hidden flex flex-col">
                <div className="p-4 border-b border-gray-800 bg-[#141820] flex items-center justify-between">
                  <h3 className="font-semibold text-gray-200">Price & Volume History</h3>
                  <div className="text-xs font-mono text-gray-500 bg-[#191D26] px-2 py-1 rounded border border-gray-800">
                    Interactive K-Line View
                  </div>
                </div>
                <div className="p-0 sm:p-4 bg-[#1E222D]">
                  <StockChart data={stockData.historical_data} />
                </div>
              </div>

              {/* Financial Trends Chart */}
              <FinancialTrendChart data={stockData.historical_financials} />
            </div>
          )}

        </div>
      </main>
    </div>
  );
}
