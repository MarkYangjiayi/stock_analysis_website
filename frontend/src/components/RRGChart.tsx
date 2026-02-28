'use client';

import React, { useMemo } from 'react';
import ReactECharts from 'echarts-for-react';
import * as echarts from 'echarts/core';

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
    // 时光机：当前所在的全局日期索引
    currentDayIndex?: number;
}

// 预设的亮丽颜色数组
const BRAND_COLORS = [
    '#00f2fe', '#fec163', '#ff0844', '#f12711', '#00c6ff',
    '#a18cd1', '#ff9a9e', '#f83600', '#f9d423', '#00b4db',
    '#b224ef', '#00a8f3', '#ff512f', '#dd2476', '#a1c4fd'
];

export default function RRGChart({ data, tailLength = 10, currentDayIndex }: RRGChartProps) {

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

        // 1. 遍历计算极大极小值 (必须使用全量原始数据，确保坐标轴绝对锁死不随 tailLength 抖动)
        Object.entries(data.data).forEach(([ticker, seriesData]) => {
            if (!seriesData || seriesData.length === 0) return;

            legendData.push(ticker);

            // 计算边界：用所有的历史数据，找到物理最大边界，保证拖动 timeline 时坐标系绝对静止
            seriesData.forEach(point => {
                if (point.rs_ratio < minRatio) minRatio = point.rs_ratio;
                if (point.rs_ratio > maxRatio) maxRatio = point.rs_ratio;
                if (point.rs_momentum < minMomentum) minMomentum = point.rs_momentum;
                if (point.rs_momentum > maxMomentum) maxMomentum = point.rs_momentum;
            });

            // 时光机截断：确定当前的时间节点窗口
            const effectiveEndIndex = currentDayIndex !== undefined ? currentDayIndex + 1 : seriesData.length;

            // 找出当前的最后一个节点（Head Point，头）
            const lastDataNode = seriesData[effectiveEndIndex - 1];
            if (!lastDataNode) return;
            const lastPoint = [lastDataNode.rs_ratio, lastDataNode.rs_momentum, lastDataNode.date, ticker];

            // 截取过去尾巴长度的轨迹段 (Line Data)
            const startIndex = Math.max(0, effectiveEndIndex - tailLength);
            const slicedTrajectory = seriesData.slice(startIndex, effectiveEndIndex);

            const themeColor = BRAND_COLORS[colorIndex % BRAND_COLORS.length];
            colorIndex++;

            // 转换为 ECharts 数据格式
            const lineData = slicedTrajectory.map(pt => [pt.rs_ratio, pt.rs_momentum, pt.date, ticker]);

            // A. 创建拖尾线 (使用 Custom 系列实现真实的路径渐变)
            dynamicSeries.push({
                name: ticker,
                type: 'custom',
                animation: false,
                data: lineData,
                renderItem: function (params: any, api: any) {
                    const idx = params.dataIndex;
                    // 第一个点没有前一个点，无法连线，直接跳过
                    if (idx === 0) return;

                    // 获取上一个点和当前点的屏幕绝对坐标
                    const pt1 = api.coord([api.value(0, idx - 1), api.value(1, idx - 1)]);
                    const pt2 = api.coord([api.value(0, idx), api.value(1, idx)]);

                    // 基于时间序列计算透明度：最旧的线段接近 0.1，最新的线段接近 1.0
                    const totalPoints = lineData.length;
                    const opacity = 0.1 + 0.9 * (idx / (totalPoints - 1));

                    return {
                        type: 'line',
                        shape: {
                            x1: pt1[0], y1: pt1[1],
                            x2: pt2[0], y2: pt2[1]
                        },
                        style: {
                            stroke: themeColor,
                            lineWidth: 3,
                            opacity: opacity,
                            lineCap: 'round',
                            lineJoin: 'round',
                            shadowColor: themeColor,
                            shadowBlur: 2
                        }
                    };
                }
            });

            // B. 创建头部的点(Scatter Series)
            dynamicSeries.push({
                name: ticker,
                type: 'scatter',
                animation: false,
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

        // 向下兼容最小跨度 1.0，使用 Math.ceil() 向上取整
        const finalDeviation = Math.max(Math.ceil(maxDeviation), 1.0);

        const axisMin = 100 - finalDeviation;
        const axisMax = 100 + finalDeviation;

        // 绘制基础配置
        return {
            // 动画更新配置：关闭更新动画，提升 Slider 拖拉时的纯粹重绘体验，防蠕动
            animationDurationUpdate: 0,
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
                bottom: '15%',
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
    }, [data, tailLength, currentDayIndex]);

    return (
        <div className="w-full h-[600px] bg-slate-900 rounded-lg p-4 shadow-lg border border-slate-800 relative">
            <ReactECharts
                option={option}
                style={{ height: '100%', width: '100%' }}
                theme="dark"
                // 关键修复：设置为 false 允许 ECharts 保留用户手动点击过的 Legend 状态，而不是在重新渲染尾巴时被覆盖
                notMerge={false}
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
