"use client";

import React, { useEffect, useRef, useState } from 'react';
import { createChart, ColorType, IChartApi, ISeriesApi, CrosshairMode, LineStyle, PriceScaleMode } from 'lightweight-charts';
import { HistoricalDataPoint } from '@/lib/api';
import { useTheme } from 'next-themes';
import { chartMonoFont, chartTheme } from '@/lib/chartTheme';

interface StockChartProps {
    data: HistoricalDataPoint[];
    interval?: string;
    onIntervalChange?: (interval: string) => void;
    isLoading?: boolean;
    height?: number;
    embedded?: boolean;
}

// Canvas renderers cannot resolve CSS variables, so name the mono stack literally.

type ChartColors = ReturnType<typeof chartTheme>;

const chartChromeOptions = (colors: ChartColors) => ({
    layout: {
        background: { type: ColorType.Solid, color: colors.background },
        textColor: colors.textMuted,
        fontFamily: chartMonoFont(),
    },
    grid: {
        vertLines: { color: colors.grid, style: LineStyle.Dashed },
        horzLines: { color: colors.grid, style: LineStyle.Dashed },
    },
    crosshair: {
        vertLine: { color: colors.textMuted, labelBackgroundColor: colors.tooltipBackground },
        horzLine: { color: colors.textMuted, labelBackgroundColor: colors.tooltipBackground },
    },
    timeScale: {
        borderColor: colors.border,
    },
    rightPriceScale: {
        borderColor: colors.border,
    },
});

// 8-digit hex alpha (~52%) keeps volume bars behind the candles.
const volumeColor = (colors: ChartColors, up: boolean) => `${up ? colors.positive : colors.negative}85`;

type ValidCandlePoint = HistoricalDataPoint & {
    open: number;
    high: number;
    low: number;
    close: number;
};

const StockChart: React.FC<StockChartProps> = ({ data, interval = '1d', onIntervalChange, isLoading, height = 480, embedded = false }) => {
    const chartContainerRef = useRef<HTMLDivElement>(null);
    const chartRef = useRef<IChartApi | null>(null);
    const { resolvedTheme } = useTheme();

    // Maintain refs to series so they can be updated dynamically
    const candlestickSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
    const volumeSeriesRef = useRef<ISeriesApi<"Histogram"> | null>(null);
    const ma20SeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
    const ma50SeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
    // Volume bars are colored per point, so keep the raw direction to recolor them on theme change.
    const volumeDataRef = useRef<{ time: string; value: number; up: boolean }[]>([]);
    const resolvedThemeRef = useRef(resolvedTheme);

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
        if (!chartContainerRef.current) return;

        const colors = chartTheme(resolvedTheme === 'dark');

        const chrome = chartChromeOptions(colors);
        const chart = createChart(chartContainerRef.current, {
            layout: chrome.layout,
            grid: chrome.grid,
            width: chartContainerRef.current.clientWidth,
            height,
            crosshair: {
                mode: CrosshairMode.Normal,
                ...chrome.crosshair,
            },
            handleScroll: {
                mouseWheel: false,
                vertTouchDrag: false,
            },
            handleScale: {
                mouseWheel: false,
            },
            timeScale: {
                timeVisible: true,
                ...chrome.timeScale,
            },
            rightPriceScale: {
                ...chrome.rightPriceScale,
                mode: PriceScaleMode.Logarithmic,
            },
        });

        chartRef.current = chart;

        candlestickSeriesRef.current = chart.addCandlestickSeries({
            upColor: colors.positive,
            downColor: colors.negative,
            borderVisible: false,
            wickUpColor: colors.positive,
            wickDownColor: colors.negative,
        });

        volumeSeriesRef.current = chart.addHistogramSeries({
            color: colors.positive,
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
            color: colors.series[0],
            lineWidth: 2,
            crosshairMarkerVisible: false,
        });

        ma50SeriesRef.current = chart.addLineSeries({
            color: colors.series[3],
            lineWidth: 2,
            crosshairMarkerVisible: false,
        });

        const handleResize = () => {
            if (chartContainerRef.current && chartRef.current) {
                chartRef.current.applyOptions({ width: chartContainerRef.current.clientWidth });
            }
        };

        const observer = new ResizeObserver(handleResize);
        observer.observe(chartContainerRef.current);

        return () => {
            observer.disconnect();
            chart.remove();
            chartRef.current = null;
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []); // Only run once on mount

    useEffect(() => {
        chartRef.current?.applyOptions({ height });
    }, [height]);

    // 2. Dynamic Theme Update (Applies options without recreating chart)
    useEffect(() => {
        resolvedThemeRef.current = resolvedTheme;
        if (!chartRef.current) return;

        const colors = chartTheme(resolvedTheme === 'dark');

        chartRef.current.applyOptions(chartChromeOptions(colors));
        candlestickSeriesRef.current?.applyOptions({
            upColor: colors.positive,
            downColor: colors.negative,
            wickUpColor: colors.positive,
            wickDownColor: colors.negative,
        });
        ma20SeriesRef.current?.applyOptions({ color: colors.series[0] });
        ma50SeriesRef.current?.applyOptions({ color: colors.series[3] });
        if (volumeDataRef.current.length > 0) {
            volumeSeriesRef.current?.setData(volumeDataRef.current.map((point) => ({
                time: point.time,
                value: point.value,
                color: volumeColor(colors, point.up),
            })));
        }
    }, [resolvedTheme]);

    // 3. Data Update Effect (triggers when `data` changes instead of tearing down instance)
    useEffect(() => {
        if (!data || data.length === 0 || !chartRef.current) return;

        // Filter out any points that might have null/undefined values for essential properties
        const validCandleData = data.filter((d): d is ValidCandlePoint =>
            d.open != null && d.high != null && d.low != null && d.close != null
        );

        const candleData = validCandleData.map((d) => ({
            time: d.date,
            open: d.open,
            high: d.high,
            low: d.low,
            close: d.close,
        }));

        const colors = chartTheme(resolvedThemeRef.current === 'dark');
        volumeDataRef.current = validCandleData.map((d) => ({
            time: d.date,
            value: d.volume != null ? d.volume : 0,
            up: d.close >= d.open,
        }));
        const volumeData = volumeDataRef.current.map((point) => ({
            time: point.time,
            value: point.value,
            color: volumeColor(colors, point.up),
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
            const rawData = validCandleData.find(d => d.date === activeDate);

            if (rawData) {
                setTooltipData({
                    visible: true,
                    date: rawData.date,
                    open: rawData.open,
                    high: rawData.high,
                    low: rawData.low,
                    close: rawData.close,
                    volume: rawData.volume ?? 0,
                    ma20: rawData.MA20 ?? undefined,
                    ma50: rawData.MA50 ?? undefined,
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
        <div
            className={`relative w-full overflow-hidden bg-surface ${embedded ? "" : "rounded-lg border"}`}
            onWheelCapture={(event) => event.stopPropagation()}
        >
            {/* Interval Switcher UI */}
            {onIntervalChange && (
                <div className="segmented-control absolute left-3 top-3 z-20 gap-0.5 backdrop-blur-md">
                    {['1d', '1wk', '1mo'].map(intv => (
                        <button
                            key={intv}
                            type="button"
                            onClick={() => onIntervalChange(intv)}
                            disabled={isLoading}
                            aria-pressed={interval === intv}
                            className="segmented-control-item px-3 font-mono"
                        >
                            {intv === '1d' ? 'D' : intv === '1wk' ? 'W' : 'M'}
                        </button>
                    ))}
                    {isLoading && (
                        <div className="flex items-center justify-center px-2">
                            <div className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-accent/30 border-t-accent" />
                        </div>
                    )}
                </div>
            )}

            <div ref={chartContainerRef} className="h-full w-full" role="img" aria-label="Interactive candlestick chart with price, volume, 20-day moving average, and 50-day moving average" />

            {/* Tooltip Overlay */}
            {tooltipData && tooltipData.visible && (
                <div
                    className="pointer-events-none absolute z-30 rounded-md border bg-surface-raised/95 p-3 text-sm shadow-xl backdrop-blur-md"
                    style={{
                        left: Math.min(tooltipData.x + 15, tooltipData.containerWidth - 180),
                        top: Math.max(10, tooltipData.y - 120),
                    }}
                >
                    <div className="mb-2 border-b pb-1 font-mono font-semibold text-fg">
                        {tooltipData.date}
                    </div>
                    <div className="grid grid-cols-2 gap-x-4 gap-y-1 font-mono text-xs tabular-nums">
                        <span className="text-fg-muted">O:</span>
                        <span className="text-right text-fg">{tooltipData.open.toFixed(2)}</span>
                        <span className="text-fg-muted">H:</span>
                        <span className="text-right text-fg">{tooltipData.high.toFixed(2)}</span>
                        <span className="text-fg-muted">L:</span>
                        <span className="text-right text-fg">{tooltipData.low.toFixed(2)}</span>
                        <span className="text-fg-muted">C:</span>
                        <span className="text-right text-fg">{tooltipData.close.toFixed(2)}</span>
                        <span className="mt-1 text-fg-muted">Vol:</span>
                        <span className="mt-1 text-right text-fg">{(tooltipData.volume / 1000000).toFixed(2)}M</span>
                        {tooltipData.ma20 && (
                            <>
                                <span className="mt-1 font-medium text-[var(--chart-series-1)]">MA20:</span>
                                <span className="mt-1 text-right text-[var(--chart-series-1)]">{tooltipData.ma20.toFixed(2)}</span>
                            </>
                        )}
                        {tooltipData.ma50 && (
                            <>
                                <span className="font-medium text-[var(--chart-series-4)]">MA50:</span>
                                <span className="text-right text-[var(--chart-series-4)]">{tooltipData.ma50.toFixed(2)}</span>
                            </>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
};

export default StockChart;
