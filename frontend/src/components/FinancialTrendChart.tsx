"use client";

import React, { useMemo } from 'react';
import ReactECharts from 'echarts-for-react';
import { HistoricalFinancialPoint } from '@/lib/api';
import { BarChart3 } from 'lucide-react';
import { useTheme } from 'next-themes';

interface FinancialTrendChartProps {
    data?: HistoricalFinancialPoint[];
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

const FinancialTrendChart: React.FC<FinancialTrendChartProps> = ({ data }) => {
    const { resolvedTheme } = useTheme();

    const options = useMemo(() => {
        if (!data || data.length === 0) return {};

        const dates = data.map(item => item.date);
        const revenues = data.map(item => item.revenue);
        const netIncomes = data.map(item => item.net_income);
        const grossMargins = data.map(item => (item.gross_margin * 100).toFixed(2));

        const isDark = resolvedTheme === 'dark';
        const textColor = isDark ? '#9ca3af' : '#475569';
        const gridColor = isDark ? '#374151' : '#e2e8f0';
        const tooltipBg = isDark ? 'rgba(21, 25, 34, 0.95)' : 'rgba(255, 255, 255, 0.95)';
        const tooltipBorder = isDark ? '#374151' : '#e2e8f0';
        const tooltipText = isDark ? '#e5e7eb' : '#0f172a';

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
                        let valueStr = param.seriesName === 'Gross Margin'
                            ? `${param.value}%`
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
                data: ['Revenue', 'Net Income', 'Gross Margin'],
                textStyle: { color: textColor, fontWeight: 'bold' },
                top: 0
            },
            grid: {
                left: '3%',
                right: '4%',
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
            yAxis: [
                {
                    type: 'value',
                    name: 'Amount ($)',
                    nameTextStyle: { color: textColor, padding: [0, 0, 0, 30] },
                    axisLabel: {
                        color: textColor,
                        fontWeight: '500',
                        formatter: (value: number) => {
                            if (value >= 1e9) return (value / 1e9) + 'B';
                            if (value >= 1e6) return (value / 1e6) + 'M';
                            return value;
                        }
                    },
                    splitLine: { lineStyle: { color: [gridColor], type: 'dashed' } },
                },
                {
                    type: 'value',
                    name: 'Gross Margin (%)',
                    nameTextStyle: { color: textColor, padding: [0, 30, 0, 0] },
                    min: 0,
                    axisLabel: {
                        color: textColor,
                        fontWeight: '500',
                        formatter: '{value} %'
                    },
                    splitLine: { show: false },
                }
            ],
            series: [
                {
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
                },
                {
                    name: 'Net Income',
                    type: 'bar',
                    yAxisIndex: 0,
                    itemStyle: {
                        color: '#1e3a8a',
                        borderRadius: [4, 4, 0, 0]
                    },
                    data: netIncomes,
                    barMaxWidth: 32,
                },
                {
                    name: 'Gross Margin',
                    type: 'line',
                    yAxisIndex: 1,
                    itemStyle: { color: '#ef4444' }, // Red/orange
                    lineStyle: { width: 3, shadowColor: 'rgba(239, 68, 68, 0.5)', shadowBlur: 10 },
                    symbol: 'circle',
                    symbolSize: 8,
                    data: grossMargins
                }
            ]
        };
    }, [data, resolvedTheme]);

    if (!data || data.length === 0) {
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
                <div className="text-xs font-mono text-slate-500 dark:text-gray-500 bg-white dark:bg-[#191D26] px-2 py-1 rounded border border-gray-200 dark:border-gray-800">
                    Dual Y-Axis (Revenue vs Margins)
                </div>
            </div>
            <div className="p-4 sm:p-6 bg-slate-50 dark:bg-[#161b22] h-[450px]">
                <ReactECharts
                    option={options}
                    style={{ height: '100%', width: '100%' }}
                    notMerge={false}
                    lazyUpdate={true}
                />
            </div>
        </div>
    );
};

export default FinancialTrendChart;
