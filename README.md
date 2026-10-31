<div align="center">
  <img src="https://img.shields.io/badge/Python-3.12-blue?style=flat-square&logo=python" alt="Python">
  <img src="https://img.shields.io/badge/Next.js-16+-black?style=flat-square&logo=next.js" alt="Next.js">
  <img src="https://img.shields.io/badge/FastAPI-0.100+-009688?style=flat-square&logo=fastapi" alt="FastAPI">
  <img src="https://img.shields.io/badge/SQLite-WAL-003B57?style=flat-square&logo=sqlite" alt="SQLite">
  <img src="https://img.shields.io/badge/License-MIT-green?style=flat-square" alt="License">
</div>

# QuantDashboard - 现代全栈量化投资终端

> **v2:** The system now separates API and worker lifecycles, preserves point-in-time fundamentals/universe history and corporate actions, quality-gates every publication, and publishes a versioned daily factor cross-section. See [the current architecture](docs/architecture.md).

**QuantDashboard** 是一款专为个人投资者与极客打造的全栈式股票量化分析与投研平台。基于现代前后端分离架构搭建，它深度融合了数据同步抓取、基本面多因子量化评估、实时技术指标测算以及由 DeepSeek 大模型驱动的 AI 智能研报引擎。

平台致力于将枯燥的财务数字与 K 线走势，转化为直观、惊艳的交互式图表与极具洞察力的量化读数。

<br/>

<div align="center">
  <!-- TODO: 替换为您自己的真实运行截图 -->
  <img src="./docs/screenshot.png" alt="QuantDashboard Screenshot" width="850" />
</div>

<br/>

## 🚀 核心功能亮点 (Features)

*   **📈 全市场数据同步 & 落地**
    异步对接 EODHD API 海量金融数据源，支持拉取美股/A股等全球市场的历史日 K 线数据与季度/年度权威财务报表。底层已全面切转至极轻量的 **SQLite** 数据底座，并在 FastAPI 异步启动层注入了 `PRAGMA journal_mode=WAL` (预写式日志) 钩子，实现了极低服务器开销与高并发读写的完美平衡。
*   **🧠 硬核多因子估值引擎 (Screener)**
    自研基本面财务分析引擎。内置经典的 DCF (现金流折现模型) 测算股票绝对内在价值 (`Intrinsic Value`) 与安全边际 (`Margin of Safety`)。创新的五维多因子雷达：覆盖 `Value (价值)`、`Quality (质量)`、`Growth (成长)`、`Health (健康)` 与 `Momentum (动量)` 维度，让优质公司显像化。
*   **🧭 Personal Stock Decision Cockpit**
    个股页顶部提供无黑盒总分的决策驾驶舱：由公司历史增长信号生成 Bear/Base/Bull 三种五年 FCFF 经营情景；三者共享按 CAPM、资本结构、债务成本和税盾估算的公司 WACC 与成熟期增长率；另提供 WACC × terminal growth 5×5 敏感性矩阵，以及以 Base 假设反解的市场隐含五年 FCFF 增长率。页面同时展示 20 项行业/板块 midrank 分位、确定性基本面预警、分析师 EPS 预期下修与空头仓位预警（[规则](docs/expectation_signals.md)；同一组指标也可在 Screener 筛选、并作为飞书自选股标签推送），以及价格、Screener、财报和因子的独立来源日期。缺失数据会给出明确原因，不会回填为零估值。
*   **🧾 Earnings Quality / 一次性损益**
    Analysis 页先用本地结构化财报生成可追溯的异常候选与数据质量提示；美国 SEC 公司可在解锁后按单个年度或季度点击分析对应 10-K/10-Q 与同期 earnings 8-K。页面浏览、同步和定时任务不会批量调用 SEC 或 AI。Reported 始终为默认口径，只有金额、税后勾稽和 SEC 引用全部通过验证时才并列显示 normalized Net Income / adjusted EPS，且 adjusted 结果不进入 DCF、ROE、P/E、peer benchmark 或 factor score。
*   **🏅 质量评分卡**
    用最多 6 个财年的年报给 5 项支柱打分（盈利能力、增长持续性、现金转化、资产负债表、资本配置），附 Piotroski F 分数和 Altman Z；按固定阈值打分，银行/保险与 REIT 换模板。Analysis 页 Overview 显示评分卡，筛选器可按综合分、各支柱、F 分数和 Z 值筛选。规则与阈值见 [docs/quality_scorecard.md](docs/quality_scorecard.md)。
*   **🤖 Evidence-cited AI Brief**
    DeepSeek 简报仅按需生成，并且只能使用驾驶舱输出的稳定证据 ID。四个分析章节都必须包含 `[E#]` 引用；未知或缺失引用会被拒绝，只有校验通过的结果才会按证据哈希缓存。AI 不可用时不影响确定性驾驶舱。
*   **📊 专业级沉浸交互图表**
    完美集成顶级图表库体系。使用 **TradingView Lightweight Charts** 高性能渲染带交互的蜡烛图、成交量潮，并支持动态挂载服务端实时算出的 `MACD`、`RSI`、`MA20/50` 指标。使用 **ECharts** 构建震撼的双 Y 轴（历史金额对比+毛利率走势）柱线复合财务趋势图。
*   **🌐 Point-in-Time 市场总览**
    `/market` 在同一条联动时间轴上展示 11 个美股板块相对 SPY 趋势、`RSP/SPY`、MA20/50/200 市场宽度、涨跌家数、新高新低、McClellan 与横截面离散度。当前发布严格历史成分口径的 S&P 500；Russell 2000 与合并股票池在可靠历史成分源接入前暂时禁用，绝不以当前成分回填历史。
*   **🏛️ 美债收益率曲线**
    `/market/yield-curve` 展示美国财政部最新名义 par yield curve，并与约 1 个月、3 个月和 1 年前的曲线形状叠加比较；历史视图支持 1Y/3Y/5Y 区间与最多六个期限，同时单列 `10Y−2Y`、`10Y−3M` 利差。数据通过现有 EODHD 配置获取官方 Treasury 序列，并以财政部年度 CSV 直连作为兜底，服务端磁盘缓存可在上游短暂不可用时继续提供最近数据。
*   **📐 S&P 500 历史整体 P/E**
    `/market/index-valuation` 自 2010 年起逐月末重构 S&P 500 整体市盈率：严格按当月真实成分聚合 Σ公司市值 / Σ TTM 报告盈利（亏损公司保留负贡献），只发布已完成月份，并列示仅盈利公司与中位数口径。多重股权按 SEC CIK 归并为一家公司（供应商在每个类股上都报公司总股本，故公司市值取主类股口径、盈利只计一次）；单股盈利沿用 Price & valuation history 的全部披露时点与勾稽门槛；覆盖不足的月份留缺口、原因随月份落库，绝不插值。一次性回填与完整口径见 [Index Valuation](docs/index_valuation.md)。
*   **📋 联动侧边栏与持久化自选 (Watchlist)**
    内置暗黑悬浮侧边栏。个人自选和每只股票的估值假设持久化到服务端 SQLite，并由现有 `X-API-Key` 保护。网页解锁时用 Admin Key 换一个 HttpOnly 会话 cookie（`POST /api/auth/session`，`SameSite=Strict`，生产环境 `Secure`，`Path=/api`），有效 30 天、每次打开网站自动续期（`ADMIN_SESSION_DAYS`）；浏览器不保存 Admin Key 本身。cookie 签名由 Admin Key 派生，轮换 Admin Key 即让所有会话失效。飞书、命令行等脚本照旧发 `X-API-Key` 请求头。本地开发请让前端和 API 同一主机名（都用 `localhost` 或都用 `127.0.0.1`），否则 cookie 跨站不会发送。首次解锁会在服务端列表为空时幂等导入旧 `my_watchlist` LocalStorage 数据，之后以服务端为准。同一客户端 15 分钟内输错 Admin Key 10 次后锁定 15 分钟（锁定期间正确的 key 也拒绝；不带 key 的请求不计数，`ADMIN_AUTH_MAX_FAILURES` / `ADMIN_AUTH_FAILURE_WINDOW_SECONDS`）。API 与页面统一加 `nosniff`、禁止被嵌入 iframe、`Referrer-Policy` 等安全响应头，生产环境加 HSTS；个人数据接口返回 `Cache-Control: no-store`。
*   **⏱️ 个股页实时价**
    美股常规交易时段（09:30–16:00 ET），个股页头部显示 EODHD WebSocket 推送的成交价（延迟约 2–6 秒），涨跌以延迟行情的昨收为基准，与自选股侧边栏一致；WebSocket 不可用时退回 15 分钟延迟价。盘前盘后不显示盘外成交（EODHD 的 WebSocket 不推收盘集合竞价，16:00 起都是盘后成交）。收盘后头部先显示当天最后一笔常规成交（`Last trade`），EODHD 当天日线出来（约收盘后 20 分钟）即换成官方收盘价；日线接口失败时，收盘 35 分钟后改用已稳定的延迟报价。当天收盘价在次日日线同步后由日线接管。Worker 持有唯一一条 WebSocket（EODHD 每个 API key 最多 50 个代码，所有连接合计），只订阅正在被浏览的个股页，最多 `LIVE_QUOTE_MAX_SYMBOLS`=45 个；自选股和 Today 页仍用 15 分钟延迟价（`/api/v1/quotes/live`，`services/live_quotes.py`）。
*   **🗓️ Today 首页（需解锁）**
    未选股票时，首页显示最新一份日报的市场状态和按题材聚类的异动（与飞书推送同一份证据），以及全部自选股的延迟行情、与日报相同规则的标签（RSI、52 周高低、预期修正、空头）和未来 7 天的美国宏观数据与财报。自选股标签和日历按请求计算，缓存 15 分钟（`/api/personal/today`，`services/today.py`）。未解锁时仍是搜索入口。
*   **📡 智能盯盘与多渠道触达网络 (Bot & Notifications)**
    构建了企业级高可用推送路由，完美支持**飞书 (Lark) 富文本卡片**穿透。
    *   **Scheduled Daily Reporter**: 美股交易日默认美东 10:00 / 16:30 生成并推送开盘、盘后报告，包含全球股票市场、外汇、商品/债券 ETF 代理、VIX/加密资产、日频美债收益率与 bp 变化、板块/已发布市场宽度、固定核心资产与自选股、事件日历、带新闻引用的个股异动，以及盘后相对早报的变化。首屏另有一行规则生成的市场状态（趋势、宽度、参与度、VIX、FRED 高收益债利差、利率曲线、估值），同一题材或行业的异动股会先聚成一组再逐只列出。逐项标注数据日期、延迟和缺失状态，并保存可重放证据。配置与本地预览见 [Daily Report v2](docs/daily_report_v2/README.md)。
    *   **Daily RSI Monitor**: 可选监控服务端 watchlist（或独立配置的一批股票），在日线 RSI(14) 进入超卖/超买区时汇总飞书提醒，并按股票与交易日去重。
    *   **数据过期提醒**: 美东周二至周六 08:45 检查筛选器、日线价格、板块 ETF 价格、标普 500 历史成分、市场宽度、因子、指数市盈率（启用题材时还有题材热度）是否已更新到最近完成的交易日；落后的数据集汇总成一张飞书卡片，附最近一次运行的失败原因，每个数据集每个交易日只提醒一次（`DATA_FRESHNESS_ALERTS_ENABLED`）。
    *   **Real-time WebSocket Monitor**: 可选（`ENABLE_WS_MONITOR`），与个股页实时价共用 Worker 里的同一条 WebSocket，`WS_SYMBOLS` 常驻订阅。基于滑动时间窗口计算，**支持自定义熔断阈值（如绝对波幅 ≥1.5%）与告警冷却**。
---

## 🛠 技术栈概览 (Tech Stack)

### Backend (Data & Core Analysis)
*   **Language**: Python 3.12+
*   **Web Framework**: FastAPI (高性能异步通信)
*   **ORM / DB Driver**: SQLAlchemy 2.0 (Async Engine), `aiosqlite`
*   **Database**: SQLite (WAL 模式并发优化)
*   **Quant & Analytics**: `pandas`, `pandas-ta-classic` (技术指标换算)
*   **AI SDK**: `openai` (DeepSeek OpenAI-compatible API)

### Frontend (UI & Visualization)
*   **Framework**: Next.js (App Router 模式), React 18
*   **Styling**: Tailwind CSS (利用 `@tailwindcss/typography` 增强排版)
*   **Charts**: 
    *   `echarts` & `echarts-for-react` (雷达图、复式财报分析图)
    *   `lightweight-charts` (毫秒级 TradingView K线引擎)
*   **Components & Icons**: `lucide-react` (极简线形图标), 原生 `fetch` (实现流式读取)

---

## 🏗 系统架构设计 (Architecture)

整体遵循优雅的 Monorepo 分层架构：

```text
stock_analysis_website/
├── api/          # FastAPI 基础路由注入 (解耦业务控制器层) 
│   └── routers.py    
├── core/         # 核心配置字典 (Pydantic BaseSettings 托管全局环境变量)
├── models.py     # SQLAlchemy 数据库映射基类 (Ticker, DailyPrice等)
├── database.py   # DB Engine 创建与异步 Session 抛出
├── services/     # 💎 后台三大核心服务层：
│   ├── analyzer.py       # 量化分析引擎 (Pandas清洗, DCF模型, 因子提取)
│   ├── data_sync.py      # EODHD 外部接口异步抓取与落库同步网络
│   └── ai_assistant.py   # DeepSeek LLM Prompt 构建与 Generator 分发
│
└── frontend/     # Next.js 客户端应用群落
    ├── src/app/          # 全局页面入口, globals.css, Route Layout
    ├── src/components/   # ECharts、AIReport、Watchlist、Dashboard 视图组件
    └── src/lib/api.ts    # 基于 Axios / fetch 构建的网关直通 Typescript SDK
```

---

## 💻 本地运行指南 (Getting Started)

跟随以下步骤在本地点火启动整个量化终端。

### 环境前置要求
*   已安装 Node.js (v18+) & NPM / Yarn
*   已安装 Python 3.12 或更高版本
*   *(无需安装任何外部数据库，系统内置了对 SQLite 的全自动化支持)*

### 1. 克隆项目
```bash
# 获取源码
git clone https://github.com/YourUsername/QuantDashboard.git
cd QuantDashboard
```

### 2. 配置环境变量

项目根目录包含了一个示例环境配置文件：

```bash
cp .env.example .env
```
打开 `.env` 并填入属于您的授权信息（**注意：您无需配置数据库，QuantDashboard 在第一次启动时会自动在本地生成 `quantify_local.db`**）：
```env
# Database connection setup (Auto-creates SQLite file)
DATABASE_URL="sqlite+aiosqlite:///./quantify_local.db"

# EODHD API Key (Financial Data Provider)
EODHD_API_KEY=your_eodhd_api_key

# DeepSeek API (AI Reporting)
DEEPSEEK_API_KEY=your_deepseek_api_key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-flash
DEEPSEEK_THINKING_ENABLED=false

# Required only for user-triggered SEC filing analysis. Use a real organization/email identity.
SEC_USER_AGENT="Your Company research@example.com"
```

### 3. 启动后端服务 (FastAPI)

```bash
# 在根目录建立 Python 虚拟环境
python3 -m venv venv
source venv/bin/activate  # Windows 用户: venv\Scripts\activate

# 安装依赖矩阵
pip install -r requirements.txt

# 使用 Uvicorn 唤起异步雷达网络 (端口常驻 8000)
alembic upgrade head
python scripts/migrate_legacy_data.py  # 旧库升级时执行一次，可重复运行
uvicorn main:app --reload
```
访问 `http://127.0.0.1:8000/docs` 即可查阅/调试全自动生成的 Swagger 互交式 API 文档。

另开一个终端启动唯一的后台 Worker：

```bash
source venv/bin/activate
python worker.py
```

生产环境的 Docker Compose 会强制使用 `ENVIRONMENT=production`。服务器未配置
`ADMIN_API_KEY` 时，公开只读功能仍会启动，但同步、回测和运维等管理接口会返回
`503 Admin operations are disabled`；配置后，请通过 `X-API-Key` 调用这些接口。
个人工作区的 watchlist 与估值情景读取、写入接口也使用同一个请求头；公开的
`GET /api/stocks/{ticker}/decision-support` 在没有该请求头时只返回默认估值情景，
不会暴露保存的个人假设。

watchlist 单只增删用 `POST /api/personal/watchlist/items`（`{"ticker": "NVDA"}`，和网页一样放到最前面）
与 `DELETE /api/personal/watchlist/items/{ticker}`；整份替换的 `PUT` 会用调用方手里的旧列表覆盖其他客户端的改动，
网页和本地命令都不再用它。本地终端用 `scripts/watchlist.py`（只依赖标准库，从 `FINBRAIN_ADMIN_API_KEY` 读 key）：

```bash
python scripts/watchlist.py ls
python scripts/watchlist.py add NVDA AMD   # NVDA 排在最前
python scripts/watchlist.py rm NVDA
python scripts/watchlist.py core           # 列出核心档
python scripts/watchlist.py core add MELI  # 标为核心档，最多 8 只
python scripts/watchlist.py core rm MELI   # 退回规则档
```

核心档（watchlist 上的 `is_core` 标记）用 `PUT /api/personal/watchlist/items/{ticker}/core`
（`{"core": true}`）设置，只能标已在 watchlist 里的票；删掉一只票也会去掉它的核心标记。
所有 watchlist 接口的返回都带 `core`（按 watchlist 顺序）。

每只票的投资逻辑（thesis）在分析页的 Thesis 标签页编辑，存在 `ticker_thesis_versions`，
每次保存追加一个版本：`GET /api/personal/stocks/{ticker}/thesis` 读当前版本，
`GET .../thesis/versions` 读历史，`PUT .../thesis` 保存时要带 `base_version`（编辑开始时的版本号，
没有 thesis 时为 0），版本已被别处更新时返回 409，不会覆盖。

Earnings-quality 的 `GET /api/stocks/{ticker}/earnings-quality` 只读取本地数据；任务状态 GET
也需要同一个 `X-API-Key`，只有带 `X-API-Key` 的
`POST /api/personal/stocks/{ticker}/earnings-quality/analyses` 才会创建一个单期 SEC/AI
任务。相同报表指纹、模型与 prompt 版本的完成结果会直接命中缓存。

首次建立可信数据集时运行：

```bash
python scripts/cold_start_init.py
```

该命令会先导入 GSPC 严格历史成分，再回填市场宽度所需的 504 个交易日，并准备板块 ETF、SPY 与 RSP 快照；Market Overview、Screener 与首个因子截面均仅在各自质量门通过后发布。Russell 2000 与合并市场宽度将在可靠 PIT 历史源接入后恢复，不使用近似历史成分。

### 4. 启动前端容器 (Next.js)

开启一个新的 Terminal 终端标签页：

```bash
cd frontend

# 安装客户端全量依赖包
npm install

# 唤醒 Next.js Turbopack 极速热更新沙盒
npm run dev
```

打开浏览器访问 [http://localhost:3000](http://localhost:3000)，感受您专属的极客金融终端吧！

---

<div align="center">
  <sub>Built with ❤️ and Quant Tech. 自由量化探索之旅。</sub>
</div>
