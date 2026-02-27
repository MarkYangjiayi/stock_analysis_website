"use client";

import React from 'react';
import RRGChart, { RRGResponse } from '@/components/RRGChart';

// 伪造的 RRG 后端返回数据
const mockRrgData: RRGResponse = {
    benchmark: "SPY",
    update_time: new Date().toISOString(),
    data: {
        // AAPL: 模拟在右上方 (Leading) 强势，且动量继续向上
        // RS-Ratio > 100 且 RS-Momentum > 100
        AAPL: [
            { date: "2026-02-21", rs_ratio: 101.0, rs_momentum: 100.5 },
            { date: "2026-02-22", rs_ratio: 101.2, rs_momentum: 100.8 },
            { date: "2026-02-23", rs_ratio: 101.5, rs_momentum: 101.2 },
            { date: "2026-02-24", rs_ratio: 101.9, rs_momentum: 101.7 },
            { date: "2026-02-25", rs_ratio: 102.4, rs_momentum: 102.3 }
        ],
        // MSFT: 模拟在左下方 (Lagging) 弱势，但开始拐头向上改善
        // RS-Ratio < 100 且 RS-Momentum < 100
        MSFT: [
            { date: "2026-02-21", rs_ratio: 98.5, rs_momentum: 97.0 },
            { date: "2026-02-22", rs_ratio: 98.2, rs_momentum: 97.5 },
            { date: "2026-02-23", rs_ratio: 97.9, rs_momentum: 98.2 },
            { date: "2026-02-24", rs_ratio: 97.8, rs_momentum: 98.8 },
            { date: "2026-02-25", rs_ratio: 98.1, rs_momentum: 99.5 }
        ]
    }
};

/**
 * 视觉验收清单 (Visual Acceptance Checklist):
 * 
 * 1. 中心对齐 (Center Alignment): 中心十字准星线完美对齐在坐标 (100, 100) 的位置。
 * 2. 轴边界对称 (Symmetric Boundaries): X轴和Y轴的最小值和最大值应与 100 保持绝对对称距离（最大极差外加边距）。
 * 3. 象限背景 (Quadrants):
 *    - 右上角 (Leading) 应有带低透明度的翠绿 (Emerald) 背景
 *    - 左上角 (Improving) 应有蓝色的背景
 *    - 左下角 (Lagging) 应有红色的背景
 *    - 右下角 (Weakening) 应有黄色的背景
 * 4. 尾巴渐变 (Tail Trails): 历史轨迹线有明显的颜色，并带有一点发光重影和半透明淡化，最新的数据点由实心散点表示。
 */

export default function RRGTestPage() {
    return (
        <div className="min-h-screen bg-slate-950 p-8 text-white font-sans">
            <h1 className="text-3xl font-extrabold mb-6 tracking-wide border-b border-slate-800 pb-4">
                RRG Component Visual Test
            </h1>

            <div className="max-w-6xl mx-auto bg-slate-900/50 p-6 rounded-2xl shadow-2xl border border-slate-800 backdrop-blur-md">
                <RRGChart data={mockRrgData} />
            </div>

            <div className="max-w-6xl mx-auto mt-8 p-6 bg-slate-900 rounded-xl border border-slate-700 text-slate-300 space-y-3">
                <h2 className="text-xl font-semibold text-white mb-2">测试说明</h2>
                <ul className="list-disc list-inside space-y-2 ml-2 leading-relaxed">
                    <li>
                        <span className="text-emerald-400 font-medium tracking-wider">AAPL 数据</span>: 从 (101.0, 100.5) 移动到 (102.4, 102.3)，位于第一象限（Leading 领先），代表相对强势并且动量继续攀升。
                    </li>
                    <li>
                        <span className="text-red-400 font-medium tracking-wider">MSFT 数据</span>: 从 (98.5, 97.0) 移动到 (98.1, 99.5)，整体依然处于第三象限（Lagging 落后），但动量由于斜率陡峭明显拐头向上改善。
                    </li>
                </ul>
            </div>
        </div>
    );
}
