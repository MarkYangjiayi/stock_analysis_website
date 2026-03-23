"use client";

import React, { useMemo, useState } from 'react';
import ReactECharts from 'echarts-for-react';
import { HistoricalFinancialPoint, ValuationMetrics } from '@/lib/api';
import { BarChart3 } from 'lucide-react';
import { useTheme } from 'next-themes';

interface FinancialTrendChartProps {
    data?: HistoricalFinancialPoint[];
    ttmData?: ValuationMetrics['ttm'];
    currentPrice?: number;
    timePeriod: 'annual' | 'ttm' | 'quarterly';
    onTimePeriodChange: (period: 'annual' | 'ttm' | 'quarterly') => void;
}

const formatCompact = (num: number) => {
    if (num >= 1e9) {
        return (num / 1e9).toFixed(2) + ' B';
    }
    if (num >= 1e6) {
        return (num / 1e6).toFixed(2) + ' M';
    }
    return num.toLocaleString(undefined, { maximumFractionDigits: 2 });
};

type OverlayMode = 'all' | 'revenue_price' | 'net_income_price' | 'margin_price';

const FinancialTrendChart: React.FC<FinancialTrendChartProps> = ({ data, ttmData, currentPrice, timePeriod, onTimePeriodChange }) => {
    const { resolvedTheme } = useTheme();
    const [overlayMode, setOverlayMode] = useState<OverlayMode>('all');

    const activeData = data;

    const options = useMemo(() => {
        if (!activeData || activeData.length === 0) return {};

        const dates = activeData.map(item => item.date);
        const revenues = activeData.map(item => item.revenue);
        const netIncomes = activeData.map(item => item.net_income);
        const grossMargins = activeData.map(item => (item.gross_margin * 100).toFixed(2));
        const prices: (number | null | undefined)[] = activeData.map(item => item.price);
        const epsValues = activeData.map(item => item.eps ?? null);

        // Inject TTM Data if active mode is 'ttm'
        if (timePeriod === 'ttm' && ttmData) {
            dates.push('TTM (Current)');
            revenues.push(ttmData.revenue);
            netIncomes.push(ttmData.net_income);
            const ttmGrossMargin = ttmData.revenue > 0 ? (ttmData.gross_profit / ttmData.revenue) : 0;
            grossMargins.push((ttmGrossMargin * 100).toFixed(2));
            prices.push(currentPrice ?? null);
            epsValues.push(null);
        }

        const isDark = resolvedTheme === 'dark';
        const textColor = isDark ? '#9ca3af' : '#475569';
        const gridColor = isDark ? '#374151' : '#e2e8f0';
        const tooltipBg = isDark ? 'rgba(21, 25, 34, 0.95)' : 'rgba(255, 255, 255, 0.95)';
        const tooltipBorder = isDark ? '#374151' : '#e2e8f0';
        const tooltipText = isDark ? '#e5e7eb' : '#0f172a';

        // Base Y-Axis Configurations
        const yAxisAmount = {
            type: 'value',
            name: 'Amount ($)',
            nameTextStyle: { color: textColor, padding: [0, 0, 0, 30] },
            axisLabel: {
                color: textColor,
                fontWeight: '500',
                formatter: (value: number) => {
                    if (value >= 1e9) return (value / 1e9).toFixed(2) + ' B';
                    if (value >= 1e6) return (value / 1e6).toFixed(2) + ' M';
                    return value;
                }
            },
            splitLine: { lineStyle: { color: [gridColor], type: 'dashed' } },
        };

        const yAxisMargin = {
            type: 'value',
            name: 'Gross Margin (%)',
            nameTextStyle: { color: textColor, padding: [0, 30, 0, 0] },
            position: 'right',
            min: 0,
            axisLabel: {
                color: textColor,
                fontWeight: '500',
                formatter: '{value} %'
            },
            splitLine: { show: false },
        };

        const yAxisPrice = {
            type: 'value',
            name: 'Price ($)',
            nameTextStyle: { color: textColor, padding: [0, 30, 0, 0] },
            position: 'right',
            offset: overlayMode === 'all' ? 80 : 0, // No offset if it's the only right axis
            axisLine: { show: true, lineStyle: { color: gridColor } },
            axisLabel: {
                color: textColor,
                fontWeight: '500',
                formatter: (value: number) => `$${value.toFixed(2)}`
            },
            splitLine: { show: false },
        };

        // Base Series Configurations
        const seriesRevenue = {
            name: 'Revenue',
            type: 'bar',
            yAxisIndex: 0,
            itemStyle: {
                color: '#60a5fa',
                borderRadius: [4, 4, 0, 0]
            },
            data: revenues,
            barMaxWidth: 32,
            barGap: '20%'
        };

        const seriesNetIncome = {
            name: 'Net Income',
            type: 'bar',
            yAxisIndex: 0, // Shares standard Amount axis
            itemStyle: {
                color: '#1e3a8a',
                borderRadius: [4, 4, 0, 0]
            },
            data: netIncomes,
            barMaxWidth: 32,
        };

        const seriesMargin = {
            name: 'Gross Margin',
            type: 'line',
            yAxisIndex: 1, // Uses right side Margin axis
            itemStyle: { color: '#ef4444' }, // Red/orange
            lineStyle: { width: 3, shadowColor: 'rgba(239, 68, 68, 0.5)', shadowBlur: 10 },
            symbol: 'circle',
            symbolSize: 8,
            data: grossMargins
        };

        const seriesPrice = {
            name: 'Stock Price',
            type: 'line',
            yAxisIndex: overlayMode === 'all' ? 2 : 1,
            itemStyle: { color: '#10b981' },
            lineStyle: { width: 3, type: 'dotted', shadowColor: 'rgba(16, 185, 129, 0.5)', shadowBlur: 8 },
            symbol: 'diamond',
            symbolSize: 10,
            data: prices,
            connectNulls: true
        };

        const hasEps = epsValues.some(v => v != null);
        const yAxisEps = {
            type: 'value',
            name: 'EPS ($)',
            nameTextStyle: { color: textColor, padding: [0, 30, 0, 0] },
            position: 'right',
            offset: overlayMode === 'all' ? 160 : 80,
            axisLine: { show: true, lineStyle: { color: gridColor } },
            axisLabel: { color: textColor, fontWeight: '500', formatter: (v: number) => `$${v.toFixed(2)}` },
            splitLine: { show: false },
        };
        const seriesEps = hasEps ? {
            name: 'EPS',
            type: 'line',
            yAxisIndex: overlayMode === 'all' ? 3 : 2,
            itemStyle: { color: '#f59e0b' },
            lineStyle: { width: 2, type: 'dashed' },
            symbol: 'circle', symbolSize: 6,
            data: epsValues,
            connectNulls: true
        } : null;

        let activeLegend: string[] = [];
        let yAxisConfig: any[] = [];
        let seriesConfig: any[] = [];

        switch (overlayMode) {
            case 'revenue_price':
                activeLegend = ['Revenue', 'Stock Price'];
                yAxisConfig = [yAxisAmount, yAxisPrice];
                seriesConfig = [seriesRevenue, seriesPrice];
                break;
            case 'net_income_price':
                activeLegend = ['Net Income', 'Stock Price'];
                yAxisConfig = [yAxisAmount, yAxisPrice];
                seriesConfig = [seriesNetIncome, seriesPrice];
                break;
            case 'margin_price':
                activeLegend = ['Gross Margin', 'Stock Price'];
                const modifiedYAxisMargin = { ...yAxisMargin, position: 'left' };
                const modifiedSeriesMargin = { ...seriesMargin, yAxisIndex: 0 };
                yAxisConfig = [modifiedYAxisMargin, yAxisPrice];
                seriesConfig = [modifiedSeriesMargin, seriesPrice];
                break;
            case 'all':
            default:
                activeLegend = ['Revenue', 'Net Income', 'Gross Margin', 'Stock Price'];
                yAxisConfig = [yAxisAmount, yAxisMargin, yAxisPrice];
                seriesConfig = [seriesRevenue, seriesNetIncome, seriesMargin, seriesPrice];
                if (seriesEps) {
                    activeLegend.push('EPS');
                    yAxisConfig.push(yAxisEps);
                    seriesConfig.push(seriesEps);
                }
                break;
        }

        return {
            tooltip: {
                trigger: 'axis',
                backgroundColor: tooltipBg,
                borderColor: tooltipBorder,
                textStyle: { color: tooltipText },
                axisPointer: { type: 'cross', crossStyle: { color: '#6b7280' } },
                formatter: (params: any) => {
                    let tooltipHtml = `<div class="font-bold mb-1 border-b border-gray-200 dark:border-gray-700 pb-1">${params[0].axisValue}</div>`;
                    params.forEach((param: any) => {
                        // Skip undefined prices
                        if (param.value === undefined || param.value === null) return;

                        let valueStr = param.seriesName === 'Gross Margin'
                            ? `${param.value}%`
                            : param.seriesName === 'Stock Price'
                                ? `$${Number(param.value).toFixed(2)}`
                                : `$${formatCompact(param.value)}`;
                        let marker = param.marker;
                        tooltipHtml += `<div class="flex justify-between gap-6 text-sm mt-2">
                            <span class="flex items-center">${marker} ${param.seriesName}</span>
                            <span class="font-bold font-mono pl-4">${valueStr}</span>
                        </div>`;
                    });
                    return tooltipHtml;
                }
            },
            legend: {
                data: activeLegend,
                textStyle: { color: textColor, fontWeight: 'bold' },
                top: 0
            },
            grid: {
                left: '3%',
                right: '4%', // Could adjust right padding if only 1 right axis is present, but ECharts containLabel handles it well
                bottom: '12%',
                containLabel: true
            },
            dataZoom: [
                {
                    type: 'inside',
                    startValue: Math.max(0, dates.length - 20),
                    endValue: dates.length - 1
                },
                {
                    type: 'slider',
                    bottom: 0,
                    startValue: Math.max(0, dates.length - 20),
                    endValue: dates.length - 1,
                    textStyle: { color: textColor },
                    borderColor: gridColor,
                    fillerColor: 'rgba(16, 185, 129, 0.2)', // emerald-500
                    handleStyle: {
                        color: '#10b981',
                        borderColor: '#059669'
                    },
                    dataBackground: {
                        lineStyle: { color: gridColor },
                        areaStyle: { color: isDark ? '#374151' : '#cbd5e1' }
                    },
                    selectedDataBackground: {
                        lineStyle: { color: '#10b981' },
                        areaStyle: { color: '#059669' }
                    }
                }
            ],
            xAxis: [
                {
                    type: 'category',
                    data: dates,
                    axisPointer: { type: 'shadow' },
                    axisLine: { lineStyle: { color: gridColor } },
                    axisLabel: { color: textColor, fontWeight: '500' }
                }
            ],
            yAxis: yAxisConfig,
            series: seriesConfig
        };
    }, [activeData, ttmData, currentPrice, timePeriod, resolvedTheme, overlayMode]);

    if (!activeData || activeData.length === 0) {
        return null;
    }

    return (
        <div className="bg-white dark:bg-[#191D26] border border-gray-200 dark:border-gray-800 rounded-2xl shadow-2xl overflow-hidden flex flex-col w-full h-full mt-8 animate-in fade-in slide-in-from-bottom-6 duration-700 ease-out fill-mode-both delay-[400ms]">
            <div className="p-4 border-b border-gray-200 dark:border-gray-800 bg-slate-50 dark:bg-[#141820] flex items-center justify-between">
                <div className="flex items-center gap-3">
                    <div className="p-2 bg-sky-500/10 rounded-xl transition">
                        <BarChart3 className="text-sky-400" size={20} />
                    </div>
                    <h3 className="font-semibold text-slate-800 dark:text-gray-200">Historical Financial Trends</h3>
                </div>
                <div className="flex items-center gap-3">
                    {/* Period Toggle */}
                    <div className="flex items-center bg-gray-100 dark:bg-[#1a1f2b] rounded-lg p-1 border border-gray-200 dark:border-gray-800">
                        {(['annual', 'ttm', 'quarterly'] as const).map(period => (
                            <button
                                key={period}
                                onClick={() => onTimePeriodChange(period)}
                                className={`px-3 py-1 text-xs font-semibold rounded-md transition-all ${timePeriod === period
                                        ? 'bg-white dark:bg-[#2B2B43] text-emerald-600 dark:text-emerald-400 shadow-sm'
                                        : 'text-slate-500 dark:text-gray-400 hover:text-slate-700 dark:hover:text-gray-200'
                                    }`}
                            >
                                {period === 'annual' ? 'Annual' : period === 'ttm' ? 'Annual + TTM' : 'Quarterly'}
                            </button>
                        ))}
                    </div>
                    <select
                        value={overlayMode}
                        onChange={(e) => setOverlayMode(e.target.value as OverlayMode)}
                        className="bg-white dark:bg-[#191D26] text-slate-700 dark:text-gray-300 text-xs sm:text-sm font-semibold border border-gray-200 dark:border-gray-700 rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-emerald-500/50 transition-all cursor-pointer shadow-sm hover:border-gray-300 dark:hover:border-gray-600 appearance-none pr-8 relative"
                        style={{
                            backgroundImage: `url("data:image/svg+xml;charset=UTF-8,%3csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3e%3cpolyline points='6 9 12 15 18 9'%3e%3c/polyline%3e%3c/svg%3e")`,
                            backgroundRepeat: 'no-repeat',
                            backgroundPosition: 'right 0.5rem center',
                            backgroundSize: '1em 1em'
                        }}
                    >
                        <option value="all">Default (All Financials)</option>
                        <option value="revenue_price">Revenue vs Price</option>
                        <option value="net_income_price">Net Income vs Price</option>
                        <option value="margin_price">Gross Margin vs Price</option>
                    </select>
                </div>
            </div>
            <div className="p-4 sm:p-6 bg-slate-50 dark:bg-[#161b22] h-[450px]">
                <ReactECharts
                    option={options}
                    style={{ height: '100%', width: '100%' }}
                    notMerge={true}
                    lazyUpdate={true}
                />
            </div>
        </div>
    );
};

export default FinancialTrendChart;
