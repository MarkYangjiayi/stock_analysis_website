# Quantify 前端升级验收记录

日期：2026-09-10

分支：`codex/frontend-redesign`

范围：设计系统、共享导航与外壳、个股研究页、Screener 基础视觉推广，以及 Market、Anomalies、Factor Lab、RRG 的共享外壳验收。

## 实施结果

- 扩展浅深主题语义变量：画布、三层 surface、边框、正文、品牌、涨跌、状态和图表序列。
- 统一系统字体栈、等宽数字、控件圆角、焦点、触控尺寸、动效和 `prefers-reduced-motion`。
- 新增可复用 `Panel`、`SectionHeader`、`SegmentedControl`、`DataState`、`AnalysisNavigation`、数字格式和图表主题工具。
- 个股页采用唯一主导航：Overview、Valuation、Financials、Price & Factors、Events & Brief。
- 主分区通过 `section` URL 参数保存；浏览器前进/后退可恢复分区。
- 驾驶舱保持挂载，估值草稿在跨分区切换时不丢失；同行比较作为 Valuation 子章节。
- 财务证据跳转顺序为：激活 Financials、切换季度、选择指标、滚动并聚焦证据。
- 公司简介改为明确的 `Show more / Show less`，兼容键盘和触摸。
- 中等视口收起固定自选栏；移动端使用紧凑横向自选区，360px 无页面级横向溢出。
- Screener 使用共享 surface/token，固定股票识别列，数值列右对齐，并提高覆盖率等辅助信息字号。
- Lightweight Charts、ECharts 的背景、网格、正文和涨跌色接入共享主题映射。

## 数据场景

自动化场景覆盖：完整数据、部分财务缺失、估值不可用、股票不在 Screener universe、负自由现金流、长公司名、移动端、Financial Flow 非法响应、浅深主题。缺失值继续显示 `—`，未增加模拟行情或综合评分。

## 缺陷与处理

| 优先级 | 发现 | 处理 |
| --- | --- | --- |
| P1 | Financial Flow 返回不完整结构时，进入 Financials 会发生客户端异常 | 在状态写入前校验响应；失败降级为局部错误，保留其他研究模块 |
| P1 | 证据入口只滚动，隐藏分区下无法可靠定位 | 先切分区与周期，再选择指标、等待渲染、滚动并聚焦 |
| P1 | 驾驶舱标签与页面纵向模块形成两套研究结构 | 改为单层个股主导航，驾驶舱改为受控内容视图 |
| P2 | 768–1199px 固定自选栏挤压研究内容 | 固定侧栏阈值调整为 1280px，较窄宽度使用紧凑入口 |
| P2 | 公司简介依赖 hover 展开 | 增加显式、可访问的展开/收起按钮 |
| 环境 | Playwright 配置仍引用不存在的 `venv` | 改为仓库实际 `.venv`，并安装匹配的 Chromium 测试运行时 |

## 自动化结果

- `npm run lint`：通过。
- `npm run test`：16 个测试文件、88 项测试全部通过。
- `npm run build`：Next.js 生产构建及 TypeScript 检查通过；编译约 1.94 秒。
- `npm run test:e2e`：最终套件 50 项；37 项通过，13 项按既有项目/视口条件明确跳过，无失败。
- 视觉自动化同时断言关键页面无客户端异常、`.app-page` 无页面级横向溢出。

E2E 使用专用 `data/screener_e2e.db`。测试库没有已发布的 Market Overview 与 RRG 行情，因此对应截图展示了明确的不可用/重试状态；完整图表配置与交互由现有 Market/RRG 组件测试覆盖。原计划编写时没有可比较的浏览器性能基线，因此本轮只记录当前构建与流程耗时，不作虚假的前后百分比结论。

## 视觉矩阵

- Analysis：1440×900、1280×800、768×1024、390×844、360×800 浅色；1440×900、390×844 深色。
- Screener、Anomalies、Market、Factor Lab、Sector Rotation：1440×900 浅色与深色。

本目录中的 PNG 为本次自动化生成的验收截图，可由 `decision-cockpit.spec.ts` 与 `site-visual.spec.ts` 重新生成。

## 发布与回退

本次没有后端 API、数据库结构或部署变更。发布可沿用现有前端流程；回退时恢复此分支的前端文件即可，不需要数据库迁移。仓库开始时已有的其他未提交修改均未覆盖。
