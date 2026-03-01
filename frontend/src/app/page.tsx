"use client";

import { useState, useEffect, Suspense } from 'react';
import { useSearchParams } from 'next/navigation';
import { Search, Activity, AlertCircle, Plus, Check, BarChart2 } from 'lucide-react';
import { fetchStockData, StockDataResponse } from '@/lib/api';
import StockChart from '@/components/StockChart';
import ValuationDashboard from '@/components/ValuationDashboard';
import AIReport from '@/components/AIReport';
import NewsFeed from '@/components/NewsFeed';
import FinancialTrendChart from '@/components/FinancialTrendChart';
import WatchlistSidebar from '@/components/WatchlistSidebar';

function HomeContent() {
  const searchParams = useSearchParams();
  const [ticker, setTicker] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [stockData, setStockData] = useState<StockDataResponse | null>(null);

  const [chartInterval, setChartInterval] = useState('1d');
  const [isChartLoading, setIsChartLoading] = useState(false);

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

  const executeSearch = async (searchTicker: string, intervalToFetch: string = '1d') => {
    if (!searchTicker.trim()) return;

    // Full load only for a completely new ticker
    const isNewTicker = searchTicker !== ticker;
    if (isNewTicker) {
      setLoading(true);
      setError('');
      setStockData(null);
      setTicker(searchTicker);
      setChartInterval('1d');
      intervalToFetch = '1d';
    }

    try {
      // Send GET /api/stocks/{ticker}?interval=...
      const data = await fetchStockData(searchTicker.toUpperCase(), intervalToFetch);
      setStockData(data);
    } catch (err: any) {
      if (err.response?.status === 404) {
        setError(`Data for ${searchTicker} not found. Ensure it has been synchronized first.`);
      } else {
        setError(err.message || 'Failed to fetch stock data');
      }
    } finally {
      if (isNewTicker) {
        setLoading(false);
      }
    }
  };

  const handleIntervalChange = async (newInterval: string) => {
    if (newInterval === chartInterval || !ticker) return;
    setChartInterval(newInterval);
    setIsChartLoading(true);
    await executeSearch(ticker, newInterval);
    setIsChartLoading(false);
  };

  // URL-driven state initialization
  useEffect(() => {
    const qTicker = searchParams.get('ticker');
    if (qTicker) {
      executeSearch(qTicker);
    } else if (!ticker && watchlist.length > 0) {
      // If no URL ticker AND no current ticker, fallback to the first item in watchlist
      // executeSearch(watchlist[0]); // Optional auto-load. Left commented intentionally to show empty state.
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  return (
    <div className="flex h-full w-full overflow-hidden bg-slate-50 dark:bg-[#0E1117] text-slate-900 dark:text-gray-100 font-sans selection:bg-emerald-500/30 transition-colors duration-300">
      <div className="hidden md:block h-full z-50">
        <WatchlistSidebar
          currentTicker={ticker}
          onSelectTicker={executeSearch}
          watchlist={watchlist}
          onAdd={handleAddWatchlist}
          onRemove={handleRemoveWatchlist}
        />
      </div>
      <main className="flex-1 overflow-y-auto p-4 md:p-6 relative">
        <div className="max-w-7xl mx-auto space-y-8 pb-12">

          {/* Empty State */}
          {!ticker && !loading && !error && !stockData && (
            <div className="flex flex-col items-center justify-center h-[70vh] text-center space-y-6 animate-in fade-in zoom-in duration-700">
              <div className="relative">
                <div className="absolute inset-0 bg-emerald-500/20 blur-3xl rounded-full" />
                <div className="bg-white dark:bg-[#151922] p-6 rounded-3xl border border-gray-200 dark:border-gray-800 shadow-xl dark:shadow-2xl relative z-10 transition-colors duration-300">
                  <BarChart2 size={64} strokeWidth={1.5} className="text-emerald-500 dark:text-emerald-400/80 mb-4 mx-auto" />
                  <h2 className="text-3xl font-extrabold text-slate-900 dark:text-white tracking-tight">Financial Analysis Terminal</h2>
                  <p className="text-slate-500 dark:text-gray-400 mt-3 text-lg">Use the top search bar or select a ticker from your watchlist to begin.</p>
                </div>
              </div>
            </div>
          )}

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
                <h3 className="text-2xl font-black tracking-widest text-transparent bg-clip-text bg-gradient-to-r from-emerald-600 to-teal-400 dark:from-emerald-400 dark:to-teal-200">
                  INITIALIZING
                </h3>
                <p className="text-slate-600 dark:text-gray-400 text-sm max-w-sm mx-auto leading-relaxed bg-white dark:bg-[#191d26] p-4 rounded-xl border border-gray-200 dark:border-gray-800 shadow-md dark:shadow-inner transition-colors duration-300">
                  首次查询该股票，正在从上游数据源<span className="text-emerald-600 dark:text-emerald-400 font-bold">同步十年财务与行情数据</span>，请稍候...
                </p>
              </div>
            </div>
          )}

          {/* Data Visualization Dashboard */}
          {stockData && !loading && (
            <div className="space-y-8 animate-in fade-in slide-in-from-bottom-8 duration-700 ease-out fill-mode-both delay-100">
              {/* Top Profile Card */}
              <div className="bg-white dark:bg-[#191D26] border border-gray-200 dark:border-gray-800 rounded-2xl p-6 md:p-8 shadow-xl dark:shadow-2xl relative overflow-hidden transition-colors duration-300">
                <div className="absolute top-0 right-0 p-32 bg-emerald-500/10 blur-[120px] rounded-full pointer-events-none" />

                <div className="flex flex-col md:flex-row justify-between items-start md:items-end gap-6 relative z-10">
                  <div className="space-y-4">
                    <div className="flex flex-wrap items-center gap-4">
                      <h2 className="text-4xl font-extrabold text-slate-900 dark:text-white tracking-tight">{stockData.profile.name}</h2>
                      {!watchlist.includes(stockData.profile.ticker) ? (
                        <button
                          onClick={() => handleAddWatchlist(stockData.profile.ticker)}
                          className="px-3 py-1.5 bg-emerald-500/20 hover:bg-emerald-500/30 text-emerald-400 text-sm font-bold rounded-lg border border-emerald-500/30 transition-colors flex items-center gap-1.5 shadow-sm"
                        >
                          <Plus size={16} strokeWidth={3} /> 加入自选
                        </button>
                      ) : (
                        <span className="px-3 py-1.5 bg-gray-100 dark:bg-gray-800/80 text-gray-500 dark:text-gray-400 text-sm font-bold rounded-lg border border-gray-300 dark:border-gray-700 flex items-center gap-1.5 transition-colors">
                          <Check size={16} strokeWidth={3} className="text-emerald-500" /> 已关注
                        </span>
                      )}
                    </div>
                    <div className="flex flex-wrap gap-2 text-sm font-medium text-slate-700 dark:text-gray-300">
                      <span className="bg-gray-100 dark:bg-[#2B2B43] px-3 py-1.5 rounded-lg border border-gray-200 dark:border-gray-700 shadow-sm text-emerald-600 dark:text-emerald-400 tracking-wider transition-colors duration-300">
                        {stockData.profile.ticker}
                      </span>
                      <span className="bg-gray-100 dark:bg-[#2B2B43] px-3 py-1.5 rounded-lg border border-gray-200 dark:border-gray-700 shadow-sm transition-colors duration-300">
                        {stockData.profile.exchange}
                      </span>
                      <span className="bg-gray-100 dark:bg-[#2B2B43] px-3 py-1.5 rounded-lg border border-gray-200 dark:border-gray-700 shadow-sm transition-colors duration-300">
                        {stockData.profile.sector}
                      </span>
                    </div>
                  </div>

                  <div className="text-left md:text-right w-full md:w-auto">
                    <div className="bg-gray-50 dark:bg-[#151922] border border-gray-200 dark:border-gray-800 p-4 rounded-xl shadow-sm dark:shadow-inner inline-block min-w-full md:min-w-[200px] transition-colors duration-300">
                      <p className="text-sm font-medium text-slate-500 dark:text-gray-500 uppercase tracking-wider mb-1">Latest Close</p>
                      <p className="text-4xl font-black text-slate-900 dark:text-white">
                        <span className="text-xl text-slate-400 dark:text-gray-400 font-medium mr-1">{stockData.profile.currency}</span>
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

                <div className="mt-6 pt-6 border-t border-gray-200 dark:border-gray-800/80 transition-colors duration-300">
                  <p className="text-slate-600 dark:text-gray-400 text-sm leading-relaxed line-clamp-3 hover:line-clamp-none transition-all duration-300">
                    {stockData.profile.description}
                  </p>
                </div>
              </div>

              {/* Valuation Dashboard Panel & Intelligence Column */}
              {stockData.valuation_metrics && (
                <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 animate-in fade-in slide-in-from-bottom-4 duration-700 ease-out fill-mode-both delay-300 items-start">
                  <div className="lg:col-span-2 flex w-full">
                    <ValuationDashboard metrics={stockData.valuation_metrics} />
                  </div>
                  <div className="lg:col-span-1 flex flex-col w-full gap-6 h-full">
                    <div className="w-full flex h-[350px]">
                      <AIReport ticker={stockData.profile.ticker} />
                    </div>
                    <div className="w-full flex h-[450px]">
                      <NewsFeed ticker={stockData.profile.ticker} />
                    </div>
                  </div>
                </div>
              )}

              {/* Interactive Chart Container */}
              <div className="bg-white dark:bg-[#191D26] border border-gray-200 dark:border-gray-800 rounded-2xl shadow-xl dark:shadow-2xl overflow-hidden flex flex-col transition-colors duration-300">
                <div className="p-4 border-b border-gray-200 dark:border-gray-800 bg-slate-50 dark:bg-[#141820] flex items-center justify-between transition-colors duration-300">
                  <h3 className="font-semibold text-slate-800 dark:text-gray-200">Price & Volume History</h3>
                  <div className="text-xs font-mono text-slate-500 dark:text-gray-500 bg-white dark:bg-[#191D26] px-2 py-1 rounded border border-gray-200 dark:border-gray-800 transition-colors duration-300">
                    Interactive K-Line View
                  </div>
                </div>
                <div className="p-0 sm:p-4 bg-slate-100 dark:bg-[#1E222D] relative transition-colors duration-300">
                  <StockChart
                    data={stockData.historical_data}
                    interval={chartInterval}
                    onIntervalChange={handleIntervalChange}
                    isLoading={isChartLoading}
                  />
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

export default function Home() {
  return (
    <Suspense fallback={
      <div className="flex h-screen w-full items-center justify-center bg-slate-50 dark:bg-[#0E1117] transition-colors duration-300">
        <div className="w-12 h-12 border-4 border-emerald-500/20 border-t-emerald-500 rounded-full animate-spin"></div>
      </div>
    }>
      <HomeContent />
    </Suspense>
  );
}
