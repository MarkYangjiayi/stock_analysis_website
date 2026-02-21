"use client";

import React, { useEffect, useRef, useState } from 'react';
import { createChart, ColorType, IChartApi, ISeriesApi, CrosshairMode } from 'lightweight-charts';
import { HistoricalDataPoint } from '@/lib/api';

interface StockChartProps {
    data: HistoricalDataPoint[];
}

const StockChart: React.FC<StockChartProps> = ({ data }) => {
    const chartContainerRef = useRef<HTMLDivElement>(null);
    const chartRef = useRef<IChartApi | null>(null);
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
    } | null>(null);

    useEffect(() => {
        if (!chartContainerRef.current || data.length === 0) return;

        // 1. Setup the main chart
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
            },
        });

        chartRef.current = chart;

        // 2. Add Candlestick Series
        const candlestickSeries = chart.addCandlestickSeries({
            upColor: '#26a69a',
            downColor: '#ef5350',
            borderVisible: false,
            wickUpColor: '#26a69a',
            wickDownColor: '#ef5350',
        });

        // Formatting data for lightweight charts
        // Time must be a string YYYY-MM-DD
        const candleData = data.map((d) => ({
            time: d.date,
            open: d.open,
            high: d.high,
            low: d.low,
            close: d.close,
        }));
        candlestickSeries.setData(candleData as any);

        // 3. Add Volume Histogram overlay
        const volumeSeries = chart.addHistogramSeries({
            color: '#26a69a',
            priceFormat: { type: 'volume' },
            priceScaleId: '', // Blank targets overlay
        });

        // Lightweight Charts 4.0: Set price scale margins on the main chart priceScale options
        chart.priceScale('').applyOptions({
            scaleMargins: {
                top: 0.8, // Push to bottom 20%
                bottom: 0,
            },
        });

        const volumeData = data.map((d) => ({
            time: d.date,
            value: d.volume,
            color: d.close >= d.open ? 'rgba(38, 166, 154, 0.5)' : 'rgba(239, 83, 80, 0.5)',
        }));
        volumeSeries.setData(volumeData as any);

        // 4. Add Moving Averages (MA20 and MA50)
        const ma20Series = chart.addLineSeries({
            color: '#2962FF',
            lineWidth: 2,
            crosshairMarkerVisible: false,
        });

        const ma50Series = chart.addLineSeries({
            color: '#FF9800',
            lineWidth: 2,
            crosshairMarkerVisible: false,
        });

        const ma20Data = data.filter(d => d.MA20 != null).map(d => ({
            time: d.date,
            value: d.MA20 as number,
        }));
        const ma50Data = data.filter(d => d.MA50 != null).map(d => ({
            time: d.date,
            value: d.MA50 as number,
        }));

        ma20Series.setData(ma20Data as any);
        ma50Series.setData(ma50Data as any);

        // 5. Crosshair interactive tooltip handling
        chart.subscribeCrosshairMove((param) => {
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
            // 找到原始数组中的当前 hover 日期的数据
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
                });
            }
        });

        // Resize observer pattern
        const handleResize = () => {
            if (chartContainerRef.current && chartRef.current) {
                chartRef.current.applyOptions({ width: chartContainerRef.current.clientWidth });
            }
        };

        window.addEventListener('resize', handleResize);

        // Cleanup on unmount
        return () => {
            window.removeEventListener('resize', handleResize);
            chart.remove();
        };
    }, [data]);

    return (
        <div
            ref={chartContainerRef}
            className="w-full relative shadow-sm rounded-lg overflow-hidden border border-gray-800"
        >
            {/* Tooltip Overlay */}
            {tooltipData && tooltipData.visible && (
                <div
                    className="absolute z-20 pointer-events-none bg-[#151922]/90 backdrop-blur-sm border border-gray-700/50 rounded-lg p-3 text-sm shadow-xl"
                    style={{
                        left: Math.min(tooltipData.x + 15, chartContainerRef.current!.clientWidth - 180),
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
