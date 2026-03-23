"use client";

import React, { useEffect, useRef, useState } from 'react';
import { createChart, ColorType, IChartApi, ISeriesApi, CrosshairMode, PriceScaleMode } from 'lightweight-charts';
import { HistoricalDataPoint } from '@/lib/api';
import { useTheme } from 'next-themes';

interface StockChartProps {
    data: HistoricalDataPoint[];
    interval?: string;
    onIntervalChange?: (interval: string) => void;
    isLoading?: boolean;
}

const StockChart: React.FC<StockChartProps> = ({ data, interval = '1d', onIntervalChange, isLoading }) => {
    const chartContainerRef = useRef<HTMLDivElement>(null);
    const rsiContainerRef = useRef<HTMLDivElement>(null);
    const macdContainerRef = useRef<HTMLDivElement>(null);

    const chartRef = useRef<IChartApi | null>(null);
    const rsiChartRef = useRef<IChartApi | null>(null);
    const macdChartRef = useRef<IChartApi | null>(null);

    const { resolvedTheme } = useTheme();

    // Main chart series
    const candlestickSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
    const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
    const ma20SeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
    const ma50SeriesRef = useRef<ISeriesApi<"Line"> | null>(null);

    // RSI series
    const rsiSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);

    // MACD series
    const macdLineRef = useRef<ISeriesApi<"Line"> | null>(null);
    const macdSignalRef = useRef<ISeriesApi<"Line"> | null>(null);
    const macdHistRef = useRef<ISeriesApi<"Histogram"> | null>(null);

    const isSyncingRef = useRef(false);

    const [tooltipData, setTooltipData] = useState<{
        visible: boolean;
        date: string;
        open: number;
        high: number;
        low: number;
        close: number;
        volume: number;
        ma20?: number;
        ma50?: number;
        x: number;
        y: number;
        containerWidth: number;
    } | null>(null);

    // 1. Initial Chart Creation (Only runs once)
    useEffect(() => {
        if (!chartContainerRef.current || !rsiContainerRef.current || !macdContainerRef.current) return;

        const isDark = resolvedTheme === 'dark';
        const backgroundColor = isDark ? '#191D26' : '#ffffff';
        const textColor = isDark ? '#D9D9D9' : '#334155';
        const gridColor = isDark ? '#2B2B43' : '#e2e8f0';

        const commonOptions = {
            layout: { background: { type: ColorType.Solid, color: backgroundColor }, textColor },
            grid: { vertLines: { color: gridColor }, horzLines: { color: gridColor } },
            crosshair: { mode: CrosshairMode.Normal },
            timeScale: { timeVisible: true, borderColor: gridColor },
            rightPriceScale: { borderColor: gridColor },
            handleScroll: { mouseWheel: true, pressedMouseMove: true },
            handleScale: { mouseWheel: true, pinch: true },
        };

        // Main chart
        const chart = createChart(chartContainerRef.current, {
            ...commonOptions,
            width: chartContainerRef.current.clientWidth,
            height: 380,
            rightPriceScale: { borderColor: gridColor, mode: PriceScaleMode.Logarithmic },
        });
        chartRef.current = chart;

        candlestickSeriesRef.current = chart.addCandlestickSeries({
            upColor: '#26a69a', downColor: '#ef5350',
            borderVisible: false, wickUpColor: '#26a69a', wickDownColor: '#ef5350',
        });

        volumeSeriesRef.current = chart.addHistogramSeries({
            color: '#26a69a', priceFormat: { type: 'volume' }, priceScaleId: '',
        });
        chart.priceScale('').applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });

        ma20SeriesRef.current = chart.addLineSeries({ color: '#2962FF', lineWidth: 2, crosshairMarkerVisible: false });
        ma50SeriesRef.current = chart.addLineSeries({ color: '#FF9800', lineWidth: 2, crosshairMarkerVisible: false });

        // RSI chart
        const rsiChart = createChart(rsiContainerRef.current, {
            ...commonOptions,
            width: rsiContainerRef.current.clientWidth,
            height: 90,
            timeScale: { timeVisible: false, borderColor: gridColor },
        });
        rsiChartRef.current = rsiChart;

        rsiSeriesRef.current = rsiChart.addLineSeries({
            color: '#a78bfa', lineWidth: 2, crosshairMarkerVisible: false,
            priceFormat: { type: 'custom', minMove: 0.01, formatter: (p: number) => p.toFixed(1) },
        });
        // Reference lines at 70 and 30
        rsiSeriesRef.current.createPriceLine({ price: 70, color: '#ef4444', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: '70' });
        rsiSeriesRef.current.createPriceLine({ price: 30, color: '#22c55e', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: '30' });

        // MACD chart
        const macdChart = createChart(macdContainerRef.current, {
            ...commonOptions,
            width: macdContainerRef.current.clientWidth,
            height: 100,
            timeScale: { timeVisible: false, borderColor: gridColor },
        });
        macdChartRef.current = macdChart;

        macdHistRef.current = macdChart.addHistogramSeries({
            priceFormat: { type: 'custom', minMove: 0.001, formatter: (p: number) => p.toFixed(3) },
        });
        macdLineRef.current = macdChart.addLineSeries({ color: '#2962FF', lineWidth: 1, crosshairMarkerVisible: false });
        macdSignalRef.current = macdChart.addLineSeries({ color: '#FF6D00', lineWidth: 1, crosshairMarkerVisible: false });

        // Time scale synchronization
        const syncRange = (sourceChart: IChartApi, targetCharts: IChartApi[]) => {
            sourceChart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
                if (isSyncingRef.current || !range) return;
                isSyncingRef.current = true;
                targetCharts.forEach(tc => tc.timeScale().setVisibleLogicalRange(range));
                isSyncingRef.current = false;
            });
        };
        syncRange(chart, [rsiChart, macdChart]);
        syncRange(rsiChart, [chart, macdChart]);
        syncRange(macdChart, [chart, rsiChart]);

        const handleResize = () => {
            if (chartContainerRef.current && chartRef.current) {
                chartRef.current.applyOptions({ width: chartContainerRef.current.clientWidth });
            }
            if (rsiContainerRef.current && rsiChartRef.current) {
                rsiChartRef.current.applyOptions({ width: rsiContainerRef.current.clientWidth });
            }
            if (macdContainerRef.current && macdChartRef.current) {
                macdChartRef.current.applyOptions({ width: macdContainerRef.current.clientWidth });
            }
        };

        window.addEventListener('resize', handleResize);

        return () => {
            window.removeEventListener('resize', handleResize);
            chart.remove();
            rsiChart.remove();
            macdChart.remove();
            chartRef.current = null;
            rsiChartRef.current = null;
            macdChartRef.current = null;
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []); // Only run once on mount

    // 2. Dynamic Theme Update
    useEffect(() => {
        const charts = [chartRef.current, rsiChartRef.current, macdChartRef.current];
        if (charts.some(c => !c)) return;

        const isDark = resolvedTheme === 'dark';
        const backgroundColor = isDark ? '#191D26' : '#ffffff';
        const textColor = isDark ? '#D9D9D9' : '#334155';
        const gridColor = isDark ? '#2B2B43' : '#e2e8f0';

        charts.forEach(c => c?.applyOptions({
            layout: { background: { type: ColorType.Solid, color: backgroundColor }, textColor },
            grid: { vertLines: { color: gridColor }, horzLines: { color: gridColor } },
            timeScale: { borderColor: gridColor },
            rightPriceScale: { borderColor: gridColor },
        }));
    }, [resolvedTheme]);

    // 3. Data Update Effect
    useEffect(() => {
        if (!data || data.length === 0 || !chartRef.current) return;

        const validCandleData = data.filter(d => d.open != null && d.high != null && d.low != null && d.close != null);

        const candleData = validCandleData.map((d) => ({ time: d.date, open: d.open, high: d.high, low: d.low, close: d.close }));
        const volumeData = validCandleData.map((d) => ({
            time: d.date, value: d.volume != null ? d.volume : 0,
            color: d.close >= d.open ? 'rgba(38, 166, 154, 0.5)' : 'rgba(239, 83, 80, 0.5)',
        }));
        const ma20Data = data.filter(d => d.MA20 != null).map(d => ({ time: d.date, value: d.MA20 as number }));
        const ma50Data = data.filter(d => d.MA50 != null).map(d => ({ time: d.date, value: d.MA50 as number }));

        // RSI data
        const rsiData = data.filter(d => d.RSI != null).map(d => ({ time: d.date, value: d.RSI as number }));

        // MACD data
        const macdData = data.filter(d => d.MACD != null).map(d => ({ time: d.date, value: d.MACD as number }));
        const macdSignalData = data.filter(d => d.MACD_Signal != null).map(d => ({ time: d.date, value: d.MACD_Signal as number }));
        const macdHistData = data.filter(d => d.MACD_Hist != null).map(d => ({
            time: d.date,
            value: d.MACD_Hist as number,
            color: (d.MACD_Hist as number) >= 0 ? 'rgba(38,166,154,0.7)' : 'rgba(239,83,80,0.7)',
        }));

        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        candlestickSeriesRef.current?.setData(candleData as any);
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        volumeSeriesRef.current?.setData(volumeData as any);
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        ma20SeriesRef.current?.setData(ma20Data as any);
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        ma50SeriesRef.current?.setData(ma50Data as any);
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        rsiSeriesRef.current?.setData(rsiData as any);
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        macdHistRef.current?.setData(macdHistData as any);
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        macdLineRef.current?.setData(macdData as any);
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        macdSignalRef.current?.setData(macdSignalData as any);

        chartRef.current.timeScale().fitContent();

        const chart = chartRef.current;
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const crosshairHandler = (param: any) => {
            if (
                param.point === undefined || !param.time ||
                param.point.x < 0 || param.point.x > chartContainerRef.current!.clientWidth ||
                param.point.y < 0 || param.point.y > chartContainerRef.current!.clientHeight
            ) {
                setTooltipData(prev => prev ? { ...prev, visible: false } : null);
                return;
            }

            const activeDate = param.time as string;
            const rawData = data.find(d => d.date === activeDate);

            if (rawData) {
                setTooltipData({
                    visible: true,
                    date: rawData.date,
                    open: rawData.open, high: rawData.high, low: rawData.low, close: rawData.close,
                    volume: rawData.volume,
                    ma20: rawData.MA20, ma50: rawData.MA50,
                    x: param.point.x, y: param.point.y,
                    containerWidth: chartContainerRef.current?.clientWidth || Number.POSITIVE_INFINITY,
                });
            }
        };

        chart.subscribeCrosshairMove(crosshairHandler);
        return () => { chart.unsubscribeCrosshairMove(crosshairHandler); };
    }, [data]);

    return (
        <div className="w-full relative shadow-sm rounded-lg overflow-hidden border border-gray-200 dark:border-gray-800">
            {/* Interval Switcher UI */}
            {onIntervalChange && (
                <div className="absolute top-4 left-4 z-20 flex bg-white dark:bg-[#151922] border border-gray-200 dark:border-gray-700/80 rounded-lg shadow-2xl p-1 gap-1 backdrop-blur-md">
                    {['1d', '1wk', '1mo'].map(intv => (
                        <button
                            key={intv}
                            onClick={() => onIntervalChange(intv)}
                            disabled={isLoading}
                            className={`px-3 py-1 text-xs font-bold rounded-md transition-colors ${interval === intv
                                ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/50 shadow-[0_0_10px_rgba(16,185,129,0.2)]'
                                : 'text-slate-500 dark:text-gray-400 hover:text-slate-900 dark:hover:text-gray-200 hover:bg-slate-100 dark:hover:bg-gray-800 border border-transparent'
                                }`}
                        >
                            {intv === '1d' ? 'D' : intv === '1wk' ? 'W' : 'M'}
                        </button>
                    ))}
                    {isLoading && (
                        <div className="flex items-center justify-center px-2">
                            <div className="animate-spin h-3.5 w-3.5 border-2 border-emerald-500/30 border-t-emerald-500 rounded-full"></div>
                        </div>
                    )}
                </div>
            )}

            <div ref={chartContainerRef} className="w-full" />

            {/* RSI Pane */}
            <div className="border-t border-gray-200 dark:border-gray-700/50 relative">
                <span className="absolute top-1 left-2 z-10 text-[10px] font-bold text-violet-400 dark:text-violet-400 opacity-70 pointer-events-none">RSI (14)</span>
                <div ref={rsiContainerRef} className="w-full" />
            </div>

            {/* MACD Pane */}
            <div className="border-t border-gray-200 dark:border-gray-700/50 relative">
                <span className="absolute top-1 left-2 z-10 text-[10px] font-bold text-blue-400 dark:text-blue-400 opacity-70 pointer-events-none">MACD (12,26,9)</span>
                <div ref={macdContainerRef} className="w-full" />
            </div>

            {/* Tooltip Overlay */}
            {tooltipData && tooltipData.visible && (
                <div
                    className="absolute z-30 pointer-events-none bg-white dark:bg-[#151922]/90 backdrop-blur-md border border-gray-200 dark:border-gray-700/50 rounded-lg p-3 text-sm shadow-xl"
                    style={{
                        left: Math.min(tooltipData.x + 15, tooltipData.containerWidth - 180),
                        top: Math.max(10, tooltipData.y - 120),
                    }}
                >
                    <div className="font-bold border-b border-gray-200 dark:border-gray-700 pb-1 mb-2 text-slate-800 dark:text-gray-200">
                        {tooltipData.date}
                    </div>
                    <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                        <span className="text-slate-500 dark:text-gray-400">O:</span>
                        <span className="text-right text-slate-800 dark:text-gray-200">{tooltipData.open.toFixed(2)}</span>
                        <span className="text-slate-500 dark:text-gray-400">H:</span>
                        <span className="text-right text-slate-800 dark:text-gray-200">{tooltipData.high.toFixed(2)}</span>
                        <span className="text-slate-500 dark:text-gray-400">L:</span>
                        <span className="text-right text-slate-800 dark:text-gray-200">{tooltipData.low.toFixed(2)}</span>
                        <span className="text-slate-500 dark:text-gray-400">C:</span>
                        <span className="text-right text-slate-800 dark:text-gray-200">{tooltipData.close.toFixed(2)}</span>
                        <span className="text-slate-500 dark:text-gray-400 mt-1">Vol:</span>
                        <span className="text-right text-slate-800 dark:text-gray-200 mt-1">{(tooltipData.volume / 1000000).toFixed(2)}M</span>
                        {tooltipData.ma20 && (
                            <>
                                <span className="text-[#2962FF] font-medium mt-1">MA20:</span>
                                <span className="text-right text-[#2962FF] mt-1">{tooltipData.ma20.toFixed(2)}</span>
                            </>
                        )}
                        {tooltipData.ma50 && (
                            <>
                                <span className="text-[#FF9800] font-medium">MA50:</span>
                                <span className="text-right text-[#FF9800]">{tooltipData.ma50.toFixed(2)}</span>
                            </>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
};

export default StockChart;
