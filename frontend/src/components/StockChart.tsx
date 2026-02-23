"use client";

import React, { useEffect, useRef, useState } from 'react';
import { createChart, ColorType, IChartApi, ISeriesApi, CrosshairMode, PriceScaleMode } from 'lightweight-charts';
import { HistoricalDataPoint } from '@/lib/api';

interface StockChartProps {
    data: HistoricalDataPoint[];
    interval?: string;
    onIntervalChange?: (interval: string) => void;
    isLoading?: boolean;
}

const StockChart: React.FC<StockChartProps> = ({ data, interval = '1d', onIntervalChange, isLoading }) => {
    const chartContainerRef = useRef<HTMLDivElement>(null);
    const chartRef = useRef<IChartApi | null>(null);

    // Maintain refs to series so they can be updated dynamically
    const candlestickSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
    const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
    const ma20SeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
    const ma50SeriesRef = useRef<ISeriesApi<"Line"> | null>(null);

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

    // Initial Chart Creation
    useEffect(() => {
        if (!chartContainerRef.current) return;

        const chart = createChart(chartContainerRef.current, {
            layout: {
                background: { type: ColorType.Solid, color: '#1E222D' },
                textColor: '#D9D9D9',
            },
            grid: {
                vertLines: { color: '#2B2B43' },
                horzLines: { color: '#2B2B43' },
            },
            width: chartContainerRef.current.clientWidth,
            height: 480,
            crosshair: {
                mode: CrosshairMode.Normal,
            },
            timeScale: {
                timeVisible: true,
                borderColor: '#2B2B43',
            },
            rightPriceScale: {
                borderColor: '#2B2B43',
                mode: PriceScaleMode.Logarithmic,
            },
        });

        chartRef.current = chart;

        candlestickSeriesRef.current = chart.addCandlestickSeries({
            upColor: '#26a69a',
            downColor: '#ef5350',
            borderVisible: false,
            wickUpColor: '#26a69a',
            wickDownColor: '#ef5350',
        });

        volumeSeriesRef.current = chart.addHistogramSeries({
            color: '#26a69a',
            priceFormat: { type: 'volume' },
            priceScaleId: '',
        });

        chart.priceScale('').applyOptions({
            scaleMargins: {
                top: 0.8,
                bottom: 0,
            },
        });

        ma20SeriesRef.current = chart.addLineSeries({
            color: '#2962FF',
            lineWidth: 2,
            crosshairMarkerVisible: false,
        });

        ma50SeriesRef.current = chart.addLineSeries({
            color: '#FF9800',
            lineWidth: 2,
            crosshairMarkerVisible: false,
        });

        const handleResize = () => {
            if (chartContainerRef.current && chartRef.current) {
                chartRef.current.applyOptions({ width: chartContainerRef.current.clientWidth });
            }
        };

        window.addEventListener('resize', handleResize);

        return () => {
            window.removeEventListener('resize', handleResize);
            chart.remove();
            chartRef.current = null;
        };
    }, []);

    // Data Update Effect (triggers when `data` changes instead of tearing down instance)
    useEffect(() => {
        if (!data || data.length === 0 || !chartRef.current) return;

        // Filter out any points that might have null/undefined values for essential properties
        const validCandleData = data.filter(d =>
            d.open != null && d.high != null && d.low != null && d.close != null
        );

        const candleData = validCandleData.map((d) => ({
            time: d.date,
            open: d.open,
            high: d.high,
            low: d.low,
            close: d.close,
        }));

        const volumeData = validCandleData.map((d) => ({
            time: d.date,
            value: d.volume != null ? d.volume : 0,
            color: d.close >= d.open ? 'rgba(38, 166, 154, 0.5)' : 'rgba(239, 83, 80, 0.5)',
        }));

        const ma20Data = data.filter(d => d.MA20 != null).map(d => ({
            time: d.date,
            value: d.MA20 as number,
        }));

        const ma50Data = data.filter(d => d.MA50 != null).map(d => ({
            time: d.date,
            value: d.MA50 as number,
        }));

        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        candlestickSeriesRef.current?.setData(candleData as any);
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        volumeSeriesRef.current?.setData(volumeData as any);
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        ma20SeriesRef.current?.setData(ma20Data as any);
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        ma50SeriesRef.current?.setData(ma50Data as any);

        // Make sure newly loaded long-timeline data fits on screen gracefully
        chartRef.current.timeScale().fitContent();

        // Subscribe inside so it has access to latest closures
        const chart = chartRef.current;
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const crosshairHandler = (param: any) => {
            if (
                param.point === undefined ||
                !param.time ||
                param.point.x < 0 ||
                param.point.x > chartContainerRef.current!.clientWidth ||
                param.point.y < 0 ||
                param.point.y > chartContainerRef.current!.clientHeight
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
                    open: rawData.open,
                    high: rawData.high,
                    low: rawData.low,
                    close: rawData.close,
                    volume: rawData.volume,
                    ma20: rawData.MA20,
                    ma50: rawData.MA50,
                    x: param.point.x,
                    y: param.point.y,
                    containerWidth: chartContainerRef.current?.clientWidth || Number.POSITIVE_INFINITY,
                });
            }
        };

        chart.subscribeCrosshairMove(crosshairHandler);

        return () => {
            chart.unsubscribeCrosshairMove(crosshairHandler);
        }
    }, [data]);

    return (
        <div className="w-full relative shadow-sm rounded-lg overflow-hidden border border-gray-800">
            {/* Interval Switcher UI */}
            {onIntervalChange && (
                <div className="absolute top-4 left-4 z-20 flex bg-[#151922] border border-gray-700/80 rounded-lg shadow-2xl p-1 gap-1 backdrop-blur-md">
                    {['1d', '1wk', '1mo'].map(intv => (
                        <button
                            key={intv}
                            onClick={() => onIntervalChange(intv)}
                            disabled={isLoading}
                            className={`px-3 py-1 text-xs font-bold rounded-md transition-colors ${interval === intv
                                ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/50 shadow-[0_0_10px_rgba(16,185,129,0.2)]'
                                : 'text-gray-400 hover:text-gray-200 hover:bg-gray-800 border border-transparent'
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

            <div ref={chartContainerRef} className="w-full h-full" />

            {/* Tooltip Overlay */}
            {tooltipData && tooltipData.visible && (
                <div
                    className="absolute z-30 pointer-events-none bg-[#151922]/90 backdrop-blur-md border border-gray-700/50 rounded-lg p-3 text-sm shadow-xl"
                    style={{
                        left: Math.min(tooltipData.x + 15, tooltipData.containerWidth - 180),
                        top: Math.max(10, tooltipData.y - 120),
                    }}
                >
                    <div className="font-bold border-b border-gray-700 pb-1 mb-2 text-gray-200">
                        {tooltipData.date}
                    </div>
                    <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                        <span className="text-gray-400">O:</span>
                        <span className="text-right text-gray-200">{tooltipData.open.toFixed(2)}</span>
                        <span className="text-gray-400">H:</span>
                        <span className="text-right text-gray-200">{tooltipData.high.toFixed(2)}</span>
                        <span className="text-gray-400">L:</span>
                        <span className="text-right text-gray-200">{tooltipData.low.toFixed(2)}</span>
                        <span className="text-gray-400">C:</span>
                        <span className="text-right text-gray-200">{tooltipData.close.toFixed(2)}</span>
                        <span className="text-gray-400 mt-1">Vol:</span>
                        <span className="text-right text-gray-200 mt-1">{(tooltipData.volume / 1000000).toFixed(2)}M</span>
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
