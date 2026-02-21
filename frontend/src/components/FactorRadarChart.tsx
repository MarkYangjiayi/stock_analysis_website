"use client";

import React, { useMemo } from 'react';
import ReactECharts from 'echarts-for-react';

interface FactorScores {
    value: number;
    quality: number;
    growth: number;
    health: number;
    momentum: number;
}

interface FactorRadarChartProps {
    scores: FactorScores | undefined;
}

const FactorRadarChart: React.FC<FactorRadarChartProps> = ({ scores }) => {

    const option = useMemo(() => {
        if (!scores) return {};

        return {
            backgroundColor: 'transparent',
            tooltip: {
                trigger: 'item',
                backgroundColor: 'rgba(25, 29, 38, 0.9)',
                borderColor: '#374151',
                textStyle: {
                    color: '#e5e7eb'
                }
            },
            radar: {
                // shape: 'circle',
                indicator: [
                    { name: 'Value', max: 100 },
                    { name: 'Quality', max: 100 },
                    { name: 'Growth', max: 100 },
                    { name: 'Health', max: 100 },
                    { name: 'Momentum', max: 100 }
                ],
                splitNumber: 4,
                axisName: {
                    color: '#9ca3af',
                    fontWeight: 600,
                    fontSize: 12,
                    padding: [0, 5]
                },
                splitLine: {
                    lineStyle: {
                        color: ['#1f2937', '#1f2937', '#374151', '#374151'].reverse()
                    }
                },
                splitArea: {
                    show: false
                },
                axisLine: {
                    lineStyle: {
                        color: '#374151'
                    }
                }
            },
            series: [
                {
                    name: 'Factor Scores',
                    type: 'radar',
                    data: [
                        {
                            value: [
                                scores.value,
                                scores.quality,
                                scores.growth,
                                scores.health,
                                scores.momentum
                            ],
                            name: 'Current Score',
                            symbol: 'circle',
                            symbolSize: 6,
                            itemStyle: {
                                color: '#10b981', // emerald-500 equivalent
                                borderColor: '#10b981',
                                borderWidth: 2,
                            },
                            areaStyle: {
                                color: 'rgba(16, 185, 129, 0.3)' // Semi-transparent emerald
                            },
                            lineStyle: {
                                color: '#10b981',
                                width: 2
                            }
                        }
                    ]
                }
            ]
        };
    }, [scores]);

    if (!scores) return null;

    return (
        <div className="w-full h-[300px]">
            <ReactECharts
                option={option}
                style={{ height: '100%', width: '100%' }}
                opts={{ renderer: 'svg' }}
            />
        </div>
    );
};

export default FactorRadarChart;
