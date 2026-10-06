# Graphite amber 迁移规则

日期：2026-10-07
分支：`ui-redesign-graphite-amber`

目标：把组件里的原始 Tailwind 色值（`slate-*`、`emerald-*`、`amber-*`、`rose-*` 等）迁移到语义 token，统一为"精密终端"风格。**只改样式，不改行为、文案、aria 属性、test id 或 DOM 结构。**

## 1. 语义工具类（定义在 `frontend/src/app/globals.css` 的 `@theme inline`）

Token 会随浅色/深色主题自动切换，**迁移后删除对应的 `dark:` 变体**。

| 用途 | 类名 |
| --- | --- |
| 页面画布 | `bg-canvas` |
| 面板 / 卡片 | `bg-surface` |
| 次级底色、表头、骨架屏、hover 底色 | `bg-surface-muted` |
| 浮层、弹窗 | `bg-surface-raised` |
| 边框 | 只写 `border` 即可（默认色已是 `--border`）；需要显式时用 `border-line`，强调用 `border-line-strong` |
| 正文 | `text-fg` |
| 次要文字、说明、标签 | `text-fg-muted` |
| 交互强调色（选中、链接、焦点、主按钮、品牌图标） | `text-accent` / `bg-accent` / `border-accent`，深色字用 `text-accent-strong`，浅底用 `bg-accent-soft`，主色底上的文字用 `text-on-accent` |
| 上涨 / 正向 / 通过 / 改善 | `text-up` / `bg-up/10` / `border-up/30` |
| 下跌 / 负向 / 恶化 | `text-down` / `bg-down/10` / `border-down/30` |
| 持平 / 无方向 | `text-flat` |
| 错误、高严重度 | `text-danger` / `bg-danger/10` / `border-danger/30` |
| 警告、风险、数据陈旧、需复核 | `text-caution` / `bg-caution/10` / `border-caution/30` |
| 中性信息提示 | `text-info` / `bg-info/10` |

透明度修饰符（`/10`、`/30`）可以直接用，Tailwind v4 会用 `color-mix` 生成。

## 2. 颜色映射：先判断含义，再选 token

| 旧写法 | 新写法 |
| --- | --- |
| `text-slate-900/800/700` + `dark:text-white/slate-100/200` | `text-fg` |
| `text-slate-600/500/400` + `dark:text-slate-300/400` | `text-fg-muted` |
| `bg-white dark:bg-slate-900/950` | `bg-surface` |
| `bg-slate-50/100 dark:bg-slate-800/900`（含骨架屏 `animate-pulse`） | `bg-surface-muted` |
| `border-slate-200/300 dark:border-slate-700/800` | `border`（或 `border-line`）；hover 时 `hover:border-line-strong` |
| `hover:bg-slate-50/100 dark:hover:bg-slate-800` | `hover:bg-surface-muted` |
| **emerald 作为品牌/交互**（选中态、链接、按钮、tab 下划线、range 滑块、图标点缀、hover 边框） | `accent` 系列，例如 `accent-emerald-600` → `accent-[var(--brand)]` |
| **emerald 作为正向数据**（涨幅、正收益、通过、verified、ready、raised） | `up` 系列 |
| `rose-*` / `red-*` 作为负向数据 | `down` 系列 |
| `rose-*` / `red-*` 作为错误、删除、高严重度 | `danger` 系列 |
| `amber-*` / `yellow-*` / `orange-*` 作为警告、风险、陈旧、medium 严重度 | `caution` 系列（新方案里琥珀色是主色调，警告改用紫色） |
| `blue-*` / `sky-*` / `indigo-*` 作为信息提示 | `info` 系列 |
| `bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300`（徽章） | 交互类：`bg-accent-soft text-accent-strong`；数据类：`bg-up/10 text-up` |

无法判断时，就看这个颜色是在描述"数据好坏"（用 `up`/`down`/`caution`），还是在表示"可点击、选中"（用 `accent`）。

## 3. 排版与形状

- **数字**：价格、百分比、倍数、金额、日期、ticker 一律加 `font-mono`（或 `.num`）。表格里的数字列右对齐。
- **字重**：`font-black` / `font-extrabold` → `font-semibold`；正文强调的 `font-bold` → `font-semibold` 或 `font-medium`。
- **最小字号**：`text-[10px]`、`text-[11px]` → `text-xs`（12px）。全大写标签可以保留 `uppercase tracking-wide`。
- **圆角**：面板和卡片 `rounded-2xl` / `rounded-xl` → `rounded-lg`；控件 → `rounded-md`；胶囊 `rounded-full` 保留。
- **阴影**：去掉卡片上的 `shadow-sm` / `shadow-md` / `shadow-lg`，层级靠边框表达；弹层可以保留阴影。
- **共享类**：优先使用 `surface-panel`、`research-panel`、`primary-button`、`secondary-button`、`control-field`、`segmented-control`、`status-pill`、`data-state`、`eyebrow`、`metric-label`、`metric-value`、`error-panel`。

## 4. 图表

- ECharts 和 lightweight-charts 里写死的 hex 颜色改为 `chartTheme(dark)`（`frontend/src/lib/chartTheme.ts`）：`brand`、`positive`、`negative`、`caution`、`info`、`text`、`textMuted`、`grid`、`border`、`background`、`tooltipBackground`、`series[]`。
- 分类序列颜色用 `series`；涨跌用 `positive` / `negative`。
- tooltip 背景用 `tooltipBackground`，边框用 `border`，文字用 `text`。
- 需要半透明时用 `${color}80` 这种 8 位 hex，或者 `color-mix`。

## 5. 验收

- 分配到的文件里不应再出现 `slate-`、`gray-`、`zinc-`、`emerald-`、`green-`、`rose-`、`red-`、`amber-`、`yellow-`、`orange-`、`blue-`、`sky-`、`indigo-`、`violet-`、`purple-`、`teal-`、`cyan-`，也不应有写死的 hex 色值（`chartTheme.ts` 除外）。
- 同步更新对应测试里检查类名的断言（例如 `text-emerald-600` → `text-up`）。
- 在 `frontend/` 下运行并通过：
  - `npx vitest run <相关测试文件>`
  - `npx tsc --noEmit`
  - `npx eslint <改动文件>`

## 6. 状态（2026-10-07）

- 全站页面和组件已迁移完毕，过渡用的 `slate`/`gray` → zinc 映射已从 `globals.css` 删除。
- `frontend/src/test/designTokens.test.ts` 会扫描 `src/`：只要出现原始色值类（如 `text-slate-500`），或在 `chartTheme.ts` 以外写死 hex 颜色，测试就会失败。新代码请直接使用第 1 节的语义类。
