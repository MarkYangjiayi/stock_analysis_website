"use client";

import React, { useEffect, useRef } from 'react';
import { createChart, ColorType, IChartApi, ISeriesApi } from 'lightweight-charts';
import { HistoricalDataPoint } from '@/lib/api';

interface StockChartProps {
    data: HistoricalDataPoint[];
}

const StockChart: React.FC<StockChartProps> = ({ data }) => {
    const chartContainerRef = useRef<HTMLDivElement>(null);
    const chartRef = useRef<IChartApi | null>(null);

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
            scaleMargins: {
                top: 0.8, // Push to bottom 20%
                bottom: 0,
            },
        });

        const volumeData = data.map((d) => ({
            time: d.date,
            value: d.volume,
            color: d.close >= d.open ? '#26a69a66' : '#ef535066', // 40% opacity
        }));
        volumeSeries.setData(volumeData as any);

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
        />
    );
};

export default StockChart;
