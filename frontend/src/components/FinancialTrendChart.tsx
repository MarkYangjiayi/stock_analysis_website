"use client";

import React, { useMemo } from 'react';
import ReactECharts from 'echarts-for-react';
import { HistoricalFinancialPoint } from '@/lib/api';
import { BarChart3 } from 'lucide-react';

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
    const options = useMemo(() => {
        if (!data || data.length === 0) return {};

        const dates = data.map(item => item.date);
        const revenues = data.map(item => item.revenue);
        const netIncomes = data.map(item => item.net_income);
        const grossMargins = data.map(item => (item.gross_margin * 100).toFixed(2));

        return {
            tooltip: {
                trigger: 'axis',
                backgroundColor: 'rgba(21, 25, 34, 0.95)',
                borderColor: '#374151',
                textStyle: { color: '#e5e7eb' },
                axisPointer: { type: 'cross', crossStyle: { color: '#6b7280' } },
                formatter: (params: any) => {
                    let tooltipHtml = `<div class="font-bold mb-1 border-b border-gray-700 pb-1">${params[0].axisValue}</div>`;
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
                textStyle: { color: '#9ca3af', fontWeight: 'bold' },
                top: 0
            },
            grid: {
                left: '3%',
                right: '4%',
                bottom: '3%',
                containLabel: true
            },
            xAxis: [
                {
                    type: 'category',
                    data: dates,
                    axisPointer: { type: 'shadow' },
                    axisLine: { lineStyle: { color: '#4b5563' } },
                    axisLabel: { color: '#9ca3af', fontWeight: '500' }
                }
            ],
            yAxis: [
                {
                    type: 'value',
                    name: 'Amount ($)',
                    nameTextStyle: { color: '#9ca3af', padding: [0, 0, 0, 30] },
                    axisLabel: {
                        color: '#9ca3af',
                        fontWeight: '500',
                        formatter: (value: number) => {
                            if (value >= 1e9) return (value / 1e9) + 'B';
                            if (value >= 1e6) return (value / 1e6) + 'M';
                            return value;
                        }
                    },
                    splitLine: { lineStyle: { color: ['#1f2937'], type: 'dashed' } },
                },
                {
                    type: 'value',
                    name: 'Gross Margin (%)',
                    nameTextStyle: { color: '#9ca3af', padding: [0, 30, 0, 0] },
                    min: 0,
                    axisLabel: {
                        color: '#9ca3af',
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
    }, [data]);

    if (!data || data.length === 0) {
        return null;
    }

    return (
        <div className="bg-[#191D26] border border-gray-800 rounded-2xl shadow-2xl overflow-hidden flex flex-col w-full h-full mt-8 animate-in fade-in slide-in-from-bottom-6 duration-700 ease-out fill-mode-both delay-[400ms]">
            <div className="p-4 border-b border-gray-800 bg-[#141820] flex items-center justify-between">
                <div className="flex items-center gap-3">
                    <div className="p-2 bg-sky-500/10 rounded-xl transition">
                        <BarChart3 className="text-sky-400" size={20} />
                    </div>
                    <h3 className="font-semibold text-gray-200">Historical Financial Trends</h3>
                </div>
                <div className="text-xs font-mono text-gray-500 bg-[#191D26] px-2 py-1 rounded border border-gray-800">
                    Dual Y-Axis (Revenue vs Margins)
                </div>
            </div>
            <div className="p-4 sm:p-6 bg-[#161b22] h-[450px]">
                <ReactECharts option={options} style={{ height: '100%', width: '100%' }} />
            </div>
        </div>
    );
};

export default FinancialTrendChart;
