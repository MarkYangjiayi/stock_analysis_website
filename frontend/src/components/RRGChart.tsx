'use client';

import React, { useMemo } from 'react';
import ReactECharts from 'echarts-for-react';

export interface RRGDataPoint {
    date: string;
    rs_ratio: number;
    rs_momentum: number;
}

export interface RRGResponse {
    benchmark: string;
    update_time: string;
    data: Record<string, RRGDataPoint[]>;
}

interface RRGChartProps {
    data: RRGResponse | null;
    // 控制尾部显示长度，默认为 10
    tailLength?: number;
}

// 预设的亮丽颜色数组
const BRAND_COLORS = [
    '#00f2fe', '#fec163', '#ff0844', '#f12711', '#00c6ff',
    '#a18cd1', '#ff9a9e', '#f83600', '#f9d423', '#00b4db',
    '#b224ef', '#00a8f3', '#ff512f', '#dd2476', '#a1c4fd'
];

export default function RRGChart({ data, tailLength = 10 }: RRGChartProps) {

    const option = useMemo(() => {
        // 默认空图表占位
        if (!data || !data.data || Object.keys(data.data).length === 0) {
            return {};
        }

        let minRatio = 100, maxRatio = 100;
        let minMomentum = 100, maxMomentum = 100;

        const dynamicSeries: any[] = [];
        const legendData: string[] = [];
        let colorIndex = 0;

        // 1. 遍历并截取所需尾部长度的数据，并以此计算坐标轴的最大 / 最小值
        Object.entries(data.data).forEach(([ticker, seriesData]) => {
            // 若无有效数据跳过
            if (!seriesData || seriesData.length === 0) return;

            legendData.push(ticker);

            // 根据 tailLength 控制尾巴长短
            const slicedTrajectory = seriesData.slice(-tailLength);

            slicedTrajectory.forEach(point => {
                if (point.rs_ratio < minRatio) minRatio = point.rs_ratio;
                if (point.rs_ratio > maxRatio) maxRatio = point.rs_ratio;
                if (point.rs_momentum < minMomentum) minMomentum = point.rs_momentum;
                if (point.rs_momentum > maxMomentum) maxMomentum = point.rs_momentum;
            });

            const themeColor = BRAND_COLORS[colorIndex % BRAND_COLORS.length];
            colorIndex++;

            // 转换为 ECharts 数据格式 `[rs_ratio, rs_momentum, date, ticker]`
            const lineData = slicedTrajectory.map(pt => [pt.rs_ratio, pt.rs_momentum, pt.date, ticker]);
            const lastPoint = lineData[lineData.length - 1];

            // A. 创建拖尾线(Line Series)
            dynamicSeries.push({
                name: ticker, // 必须同名，图例才能成组控制
                type: 'line',
                data: lineData,
                smooth: true,
                symbol: 'none',
                lineStyle: {
                    width: 3,
                    color: themeColor,
                    opacity: 0.5,
                    shadowColor: themeColor,
                    shadowBlur: 10
                }
            });

            // B. 创建头部的点(Scatter Series)
            dynamicSeries.push({
                name: ticker, // 必须同名，图例才能成组控制
                type: 'scatter',
                data: [lastPoint],
                symbolSize: 12,
                itemStyle: {
                    color: themeColor,
                    borderColor: '#ffffff',
                    borderWidth: 1.5,
                    shadowColor: themeColor,
                    shadowBlur: 15
                },
                label: {
                    show: true,
                    formatter: ticker,
                    position: 'right',
                    distance: 10,
                    color: themeColor,
                    fontWeight: 'bold',
                    fontSize: 14,
                    textBorderColor: '#000',
                    textBorderWidth: 2
                }
            });
        });

        // 2. 计算最大偏离值
        const maxDeviationRatio = Math.max(Math.abs(maxRatio - 100), Math.abs(100 - minRatio));
        const maxDeviationMomentum = Math.max(Math.abs(maxMomentum - 100), Math.abs(100 - minMomentum));

        // 取较大的那个作为统一的单侧跨度，留出 1.05 的 padding 防止点贴边
        const maxDeviation = Math.max(maxDeviationRatio, maxDeviationMomentum) * 1.05;

        // 向下兼容最小跨度 1.0，使用 Math.ceil() 向上取整，确保 ECharts 坐标轴刻度数字为干净整数
        const finalDeviation = Math.max(Math.ceil(maxDeviation), 1.0);

        const axisMin = 100 - finalDeviation;
        const axisMax = 100 + finalDeviation;

        // 绘制基础配置
        return {
            title: {
                text: 'Relative Rotation Graph (RRG)',
                left: 'center',
                top: 10,
                textStyle: {
                    color: '#ccc',
                    fontSize: 16
                }
            },
            legend: {
                type: 'scroll',
                bottom: 15, // 放置在底部
                data: legendData,
                textStyle: {
                    color: '#e2e8f0', // slate-200
                    fontSize: 12
                },
                pageIconColor: '#3b82f6',
                pageTextStyle: {
                    color: '#e2e8f0'
                }
            },
            tooltip: {
                trigger: 'item',
                backgroundColor: 'rgba(15, 23, 42, 0.95)',
                borderColor: '#334155',
                textStyle: { color: '#f8fafc' },
                formatter: function (params: any) {
                    if (Array.isArray(params.value)) {
                        const ratio = params.value[0].toFixed(2);
                        const momentum = params.value[1].toFixed(2);
                        const dt = params.value[2];
                        const tck = params.value[3];

                        return `
              <div class="font-bold flex items-center gap-2 mb-1">
                <span style="display:inline-block;width:10px;height:10px;border-radius:50%;background-color:${params.color};"></span>
                ${tck}
              </div>
              <div class="text-sm text-slate-300">Date: ${dt}</div>
              <div class="text-sm text-slate-300">RS-Ratio: <span class="text-white">${ratio}</span></div>
              <div class="text-sm text-slate-300">RS-Momentum: <span class="text-white">${momentum}</span></div>
            `;
                    }
                    return params.seriesName;
                }
            },
            grid: {
                left: '5%',
                right: '5%',
                bottom: '15%', // 给 legend 腾出空间
                top: '12%',
                containLabel: true
            },
            // 3. X/Y轴配置
            xAxis: {
                type: 'value',
                name: 'RS-Ratio',
                nameLocation: 'middle',
                nameGap: 30,
                nameTextStyle: { color: '#888' },
                min: axisMin,
                max: axisMax,
                axisLine: { show: false },
                axisTick: { show: false },
                splitLine: {
                    show: true,
                    lineStyle: {
                        color: '#333',
                        type: 'dashed'
                    }
                },
                axisLabel: { color: '#888' }
            },
            yAxis: {
                type: 'value',
                name: 'RS-Momentum',
                nameLocation: 'middle',
                nameGap: 30,
                nameTextStyle: { color: '#888' },
                min: axisMin,
                max: axisMax,
                axisLine: { show: false },
                axisTick: { show: false },
                splitLine: {
                    show: true,
                    lineStyle: {
                        color: '#333',
                        type: 'dashed'
                    }
                },
                axisLabel: { color: '#888' }
            },
            // 4 & 5. 背景象限 和 准星线
            series: [
                {
                    name: 'background-grid',
                    type: 'scatter',
                    silent: true, // 不参与高亮/交互
                    data: [],
                    markArea: {
                        silent: true,
                        itemStyle: {
                            opacity: 0.1
                        },
                        data: [
                            // 第一象限：右上 Leading (绿)
                            [
                                { xAxis: 100, yAxis: 100, itemStyle: { color: '#10b981' } },
                                { xAxis: axisMax, yAxis: axisMax }
                            ],
                            // 第二象限：左上 Improving (蓝)
                            [
                                { xAxis: axisMin, yAxis: 100, itemStyle: { color: '#3b82f6' } },
                                { xAxis: 100, yAxis: axisMax }
                            ],
                            // 第三象限：左下 Lagging (红)
                            [
                                { xAxis: axisMin, yAxis: axisMin, itemStyle: { color: '#ef4444' } },
                                { xAxis: 100, yAxis: 100 }
                            ],
                            // 第四象限：右下 Weakening (黄)
                            [
                                { xAxis: 100, yAxis: axisMin, itemStyle: { color: '#eab308' } },
                                { xAxis: axisMax, yAxis: 100 }
                            ]
                        ]
                    },
                    markLine: {
                        silent: true,
                        symbol: 'none',
                        label: { show: false },
                        lineStyle: {
                            color: '#555',
                            width: 1.5,
                            type: 'solid'
                        },
                        data: [
                            { xAxis: 100 },
                            { yAxis: 100 }
                        ]
                    }
                },
                // 追加上所有股票的流星线与散点头部
                ...dynamicSeries
            ]
        };
    }, [data, tailLength]);

    return (
        <div className="w-full h-[600px] bg-slate-900 rounded-lg p-4 shadow-lg border border-slate-800 relative">
            <ReactECharts
                option={option}
                style={{ height: '100%', width: '100%' }}
                theme="dark"
                notMerge={true}
                lazyUpdate={true}
            />

            {/* 补充四个象限的文字标识浮层 (绝对定位，避免遮挡 ECharts legend，调整到底部网格上方) */}
            <div className="absolute top-12 right-10 text-emerald-500/50 font-bold uppercase pointer-events-none tracking-widest text-lg z-0">Leading</div>
            <div className="absolute bottom-20 right-10 text-yellow-500/50 font-bold uppercase pointer-events-none tracking-widest text-lg z-0">Weakening</div>
            <div className="absolute bottom-20 left-10 text-red-500/50 font-bold uppercase pointer-events-none tracking-widest text-lg z-0">Lagging</div>
            <div className="absolute top-12 left-10 text-blue-500/50 font-bold uppercase pointer-events-none tracking-widest text-lg z-0">Improving</div>
        </div>
    );
}
