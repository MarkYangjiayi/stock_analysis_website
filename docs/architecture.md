# 股票量化分析系统 (Stock Analysis Platform) - 架构与设计文档

## 1. 项目概述 (Project Overview)
本项目是一个全栈股票综合分析平台。系统通过调用 EODHD API 获取原始金融数据（基本面、技术面历史 K 线等），在后端进行清洗、转换和衍生指标计算，并通过 RESTful API 提供给前端进行交互式可视化展示。

## 2. 核心技术栈选型 (Tech Stack)

### 2.1 后端 (Backend & Data Pipeline)
* **核心语言:** Python 3.10+
* **Web 框架:** FastAPI (纯异步架构)
* **数据处理与量化计算:** `pandas`, `numpy`, `TA-Lib` (或 `pandas-ta`)
* **HTTP 请求:** `httpx` (异步调用外部 API)
* **ORM:** SQLAlchemy 2.0 (Async 模式) + asyncpg

### 2.2 前端 (Frontend & UI)
* **框架:** Next.js (React) 或 Vue 3 (使用 TypeScript 强类型)
* **样式:** Tailwind CSS
* **可视化图表:**
    * K线与金融行情: Lightweight Charts (TradingView)
    * 财务数据看板: ECharts

### 2.3 数据存储与基础设施 (Storage & Infra)
* **主数据库:** PostgreSQL (存储结构化/半结构化静态数据与行情序列)
* **缓存与限流:** Redis (存储计算后的热点指标、实时数据缓存)
* **任务调度:** APScheduler 或 Celery (处理盘后 K 线更新与周末财报更新)

---

## 3. 核心数据库模型 (Database Schema)

系统基于 PostgreSQL 构建，主要包含以下 4 张核心表：

1.  **`tickers` (股票基础信息表)**
    * `ticker` (String, PK): 股票代码 (例: AAPL.US)
    * `name`, `exchange`, `sector`, `industry`, `description`, `currency`
    * `last_updated` (DateTime): 基础信息最后同步时间
2.  **`daily_prices` (日 K 线历史行情表)**
    * `id` (Integer, PK, Auto Increment)
    * `ticker` (String, FK/Index)
    * `date` (Date, Index)
    * `open`, `high`, `low`, `close`, `adjusted_close`, `volume` (Numeric/BigInt)
    * *约束 (Constraint):* `(ticker, date)` 联合唯一索引。
3.  **`financial_statements` (财务报表表)**
    * `id` (Integer, PK)
    * `ticker` (String, Index)
    * `fiscal_date` (Date), `period` (String: Quarterly/Yearly)
    * `revenue`, `net_income` (Numeric) - 核心指标独立列
    * `income_statement`, `balance_sheet`, `cash_flow` (JSONB) - 完整原始报表存入 JSONB
    * *约束 (Constraint):* `(ticker, fiscal_date, period)` 联合唯一索引。
4.  **`computed_metrics` (预计算基本面指标表)**
    * `ticker` (String, Index), `date` (Date)
    * `pe_ratio`, `pb_ratio`, `roe`, `debt_to_equity` (Numeric)

---

## 4. 数据流与业务逻辑 (Data Flow)

系统遵循 **Read-Through Cache** 策略，禁止前端请求直接触发不受控的外部 API 调用。

* **冷启动 (Cold Start - 首次查询陌生 Ticker):**
    1. 前端发起请求 -> 后端查 Redis 未命中 -> 查 PostgreSQL 未命中。
    2. 后端并发调用 EODHD API 获取 Fundamentals 和 EOD 数据。
    3. 解析数据入库 (PostgreSQL DB)。
    4. 触发量化引擎计算 MA/MACD/RSI 等技术指标及财务比率。
    5. 结果存入 Redis 缓存 (设置 24h TTL) 并返回前端。
* **热路径 (Hot Path - 日常高频查询):**
    1. 命中 Redis (技术指标) 或 PostgreSQL (90天内更新过的财报)。
    2. 快速组装 JSON 返回，响应时间控制在 50ms 内。
* **后台更新任务 (Cron Jobs):**
    1. **每日收盘:** 盘后批量拉取活跃自选股的最新日 K，追加至 DB，刷新 Redis 中技术面缓存。
    2. **周末巡检:** 扫描 DB 中 `last_updated` 较旧的股票，拉取最新财报更新 JSONB。

---

## 5. 🤖 AI 编程强制约束 (AI Coding Guidelines)
*阅读此文档的 AI 助手，在生成代码时必须严格遵守以下准则：*

1.  **异步优先:** FastAPI 路由、数据库操作（SQLAlchemy AsyncSession）、Redis 交互必须全部使用 `async/await`。
2.  **HTTP Client 生命周期 (极其重要):** * 在与外部 API (如 EODHD) 交互时，**HTTP client (如 `httpx.AsyncClient`) 必须在单次请求 (single request) 的内部进行初始化和关闭。** * **绝对禁止**将 HTTP client 声明为全局变量或单例，以避免在异步并发环境下产生连接池状态污染或混乱。推荐使用 `async with httpx.AsyncClient() as client:` 模式。
3.  **计算解耦:** 外部数据获取与量化指标计算 (`pandas`) 必须解耦到不同的 Service 层方法中，保持数据管道的纯净度。
4.  **JSONB 读写:** 在处理 EODHD 复杂的 Fundamentals 返回值时，不要尝试将所有财务科目映射为强类型 ORM 字段，统一序列化为 PostgreSQL 的 `JSONB` 类型存储，并在分析时按需反序列化。
5.  **异常与限流捕获:** 所有对 EODHD 的请求必须包含 `try-except` 块，并显式处理 HTTP 429 (Too Many Requests) 错误，实现退避重试 (Exponential Backoff)。