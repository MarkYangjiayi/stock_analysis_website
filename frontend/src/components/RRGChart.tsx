'use client';

import React, { useMemo, useEffect, useState } from 'react';
import ReactECharts from 'echarts-for-react';
import { useTheme } from 'next-themes';
import { chartMonoFont, chartTheme } from '@/lib/chartTheme';

export interface RRGDataPoint {
    date: string;
    rs_ratio: number;
    rs_momentum: number;
}

export interface RRGResponse {
    benchmark: string;
    update_time: string;
    data_as_of_date?: string | null;
    data_complete?: boolean;
    missing_tickers?: string[];
    data: Record<string, RRGDataPoint[]>;
}

interface RRGChartProps {
    data: RRGResponse | null;
    // 控制尾部显示长度，默认为 10
    tailLength?: number;
    // 时光机：当前所在的全局日期索引
    currentDayIndex?: number;
}

interface CustomRenderParams {
    dataIndex: number;
}

interface CustomRenderApi {
    coord: (value: Array<number | string>) => number[];
    value: (dimension: number, dataIndex?: number) => number | string;
}

interface RRGTooltipParam {
    value?: Array<number | string>;
    color?: string;
    seriesName?: string;
}

export default function RRGChart({ data, tailLength = 10, currentDayIndex }: RRGChartProps) {
    const { resolvedTheme } = useTheme();
    const [mounted, setMounted] = useState(false);

    useEffect(() => {
        setMounted(true);
    }, []);

    const isDark = resolvedTheme === 'dark';

    const option = useMemo(() => {
        // 默认空图表占位
        if (!data || !data.data || Object.keys(data.data).length === 0) {
            return {};
        }

        const colors = chartTheme(isDark);
        const monoFont = chartMonoFont();
        // 分类色板：与涨跌绿/红区分，数量不足时循环使用
        const sectorColors = colors.categorical;

        let minRatio = 100, maxRatio = 100;
        let minMomentum = 100, maxMomentum = 100;

        const dynamicSeries: Array<Record<string, unknown>> = [];
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

            const themeColor = sectorColors[colorIndex % sectorColors.length];
            colorIndex++;

            // 转换为 ECharts 数据格式
            const lineData = slicedTrajectory.map(pt => [pt.rs_ratio, pt.rs_momentum, pt.date, ticker]);

            // A. 创建拖尾线 (使用 Custom 系列实现真实的路径渐变)
            dynamicSeries.push({
                name: ticker,
                type: 'custom',
                animation: false,
                data: lineData,
                renderItem: function (params: CustomRenderParams, api: CustomRenderApi) {
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
                            lineJoin: 'round'
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
                    borderColor: colors.background,
                    borderWidth: 1.5
                },
                label: {
                    show: true,
                    formatter: ticker,
                    position: lastDataNode.rs_ratio >= 100 ? 'left' : 'right',
                    distance: 7,
                    color: themeColor,
                    fontFamily: monoFont,
                    fontWeight: 600,
                    fontSize: 12,
                    textBorderColor: colors.background,
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
            backgroundColor: 'transparent',
            aria: { enabled: true, description: 'Relative rotation graph for US sector ETFs versus the benchmark' },
            title: {
                text: 'Relative Rotation Graph (RRG)',
                left: 'center',
                top: 10,
                textStyle: {
                    color: colors.text,
                    fontSize: 14,
                    fontWeight: 600
                }
            },
            legend: {
                type: 'scroll',
                bottom: 15, // 放置在底部
                data: legendData,
                textStyle: {
                    color: colors.textMuted,
                    fontFamily: monoFont,
                    fontSize: 12
                },
                inactiveColor: colors.border,
                pageIconColor: colors.brand,
                pageIconInactiveColor: colors.border,
                pageTextStyle: {
                    color: colors.textMuted,
                    fontFamily: monoFont
                }
            },
            tooltip: {
                trigger: 'item',
                backgroundColor: colors.tooltipBackground,
                borderColor: colors.border,
                textStyle: { color: colors.text },
                formatter: function (params: RRGTooltipParam) {
                    if (Array.isArray(params.value)) {
                        const ratio = Number(params.value[0]).toFixed(2);
                        const momentum = Number(params.value[1]).toFixed(2);
                        const dt = params.value[2];
                        const tck = params.value[3];

                        const dateColor = 'text-fg-muted';
                        const valColor = 'font-mono text-fg';

                        return `
              <div class="font-mono font-semibold flex items-center gap-2 mb-1">
                <span style="display:inline-block;width:10px;height:10px;border-radius:50%;background-color:${params.color};"></span>
                ${tck}
              </div>
              <div class="text-sm ${dateColor}">Date: <span class="font-mono">${dt}</span></div>
              <div class="text-sm ${dateColor}">RS-Ratio: <span class="${valColor}">${ratio}</span></div>
              <div class="text-sm ${dateColor}">RS-Momentum: <span class="${valColor}">${momentum}</span></div>
            `;
                    }
                    return params.seriesName ?? '';
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
                nameTextStyle: { color: colors.textMuted, fontFamily: monoFont },
                min: axisMin,
                max: axisMax,
                axisLine: { show: false },
                axisTick: { show: false },
                splitLine: {
                    show: true,
                    lineStyle: {
                        color: colors.grid,
                        type: 'dashed'
                    }
                },
                axisLabel: { color: colors.textMuted, fontFamily: monoFont }
            },
            yAxis: {
                type: 'value',
                name: 'RS-Momentum',
                nameLocation: 'middle',
                nameGap: 30,
                nameTextStyle: { color: colors.textMuted, fontFamily: monoFont },
                min: axisMin,
                max: axisMax,
                axisLine: { show: false },
                axisTick: { show: false },
                splitLine: {
                    show: true,
                    lineStyle: {
                        color: colors.grid,
                        type: 'dashed'
                    }
                },
                axisLabel: { color: colors.textMuted, fontFamily: monoFont }
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
                        data: [
                            // 第一象限：右上 Leading (绿)
                            [
                                { xAxis: 100, yAxis: 100, itemStyle: { color: `${colors.positive}14` } },
                                { xAxis: axisMax, yAxis: axisMax }
                            ],
                            // 第二象限：左上 Improving (蓝)
                            [
                                { xAxis: axisMin, yAxis: 100, itemStyle: { color: `${colors.info}14` } },
                                { xAxis: 100, yAxis: axisMax }
                            ],
                            // 第三象限：左下 Lagging (红)
                            [
                                { xAxis: axisMin, yAxis: axisMin, itemStyle: { color: `${colors.negative}14` } },
                                { xAxis: 100, yAxis: 100 }
                            ],
                            // 第四象限：右下 Weakening (警示紫)
                            [
                                { xAxis: 100, yAxis: axisMin, itemStyle: { color: `${colors.caution}14` } },
                                { xAxis: axisMax, yAxis: 100 }
                            ]
                        ]
                    },
                    markLine: {
                        silent: true,
                        symbol: 'none',
                        label: { show: false },
                        lineStyle: {
                            color: colors.border,
                            width: 1,
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
    }, [data, tailLength, currentDayIndex, isDark]);

    if (!mounted) {
        return <div className="h-[520px] w-full rounded-lg bg-surface p-2 sm:h-[600px] sm:p-4" />;
    }

    return (
        <div
            className="relative h-[520px] w-full rounded-lg bg-surface p-2 sm:h-[600px] sm:p-4"
            onWheelCapture={(event) => event.stopPropagation()}
        >
            <ReactECharts
                option={option}
                style={{ height: '100%', width: '100%' }}
                // 关键修复：设置为 false 允许 ECharts 保留用户手动点击过的 Legend 状态，而不是在重新渲染尾巴时被覆盖
                notMerge={false}
                lazyUpdate={true}
            />

            {/* 补充四个象限的文字标识浮层 (绝对定位，避免遮挡 ECharts legend，调整到底部网格上方) */}
            <div className="pointer-events-none absolute right-5 top-12 z-0 text-xs font-semibold uppercase tracking-widest text-up/40 sm:right-10 sm:text-lg">Leading</div>
            <div className="pointer-events-none absolute bottom-28 right-5 z-0 text-xs font-semibold uppercase tracking-widest text-caution/40 sm:bottom-24 sm:right-10 sm:text-lg">Weakening</div>
            <div className="pointer-events-none absolute bottom-28 left-5 z-0 text-xs font-semibold uppercase tracking-widest text-down/40 sm:bottom-24 sm:left-10 sm:text-lg">Lagging</div>
            <div className="pointer-events-none absolute left-5 top-12 z-0 text-xs font-semibold uppercase tracking-widest text-info/40 sm:left-10 sm:text-lg">Improving</div>
        </div>
    );
}
