<div align="center">
  <img src="https://img.shields.io/badge/Python-3.9-blue?style=flat-square&logo=python" alt="Python">
  <img src="https://img.shields.io/badge/Next.js-16+-black?style=flat-square&logo=next.js" alt="Next.js">
  <img src="https://img.shields.io/badge/FastAPI-0.100+-009688?style=flat-square&logo=fastapi" alt="FastAPI">
  <img src="https://img.shields.io/badge/PostgreSQL-15+-336791?style=flat-square&logo=postgresql" alt="PostgreSQL">
  <img src="https://img.shields.io/badge/License-MIT-green?style=flat-square" alt="License">
</div>

# QuantDashboard - 现代全栈量化投资终端

**QuantDashboard** 是一款专为个人投资者与极客打造的全栈式股票量化分析与投研平台。基于现代前后端分离架构搭建，它深度融合了数据同步抓取、基本面多因子量化评估、实时技术指标测算以及由最新 Google Gemini 大模型驱动的 AI 智能研报引擎。

平台致力于将枯燥的财务数字与 K 线走势，转化为直观、惊艳的交互式图表与极具洞察力的量化读数。

<br/>

<div align="center">
  <!-- TODO: 替换为您自己的真实运行截图 -->
  <img src="./docs/screenshot.png" alt="QuantDashboard Screenshot" width="850" />
</div>

<br/>

## 🚀 核心功能亮点 (Features)

*   **📈 全市场数据同步 & 落地**
    异步对接 EODHD API 海量金融数据源，支持拉取美股/A股等全球市场的历史日 K 线数据与季度/年度权威财务报表。通过 `asyncpg` + `SQLAlchemy` 实现并发极致性能，稳健存入 PostgreSQL 15 本地数据湖。
*   **🧠 硬核多因子估值引擎 (Screener)**
    自研基本面财务分析引擎。内置经典的 DCF (现金流折现模型) 测算股票绝对内在价值 (`Intrinsic Value`) 与安全边际 (`Margin of Safety`)。创新的五维多因子雷达：覆盖 `Value (价值)`、`Quality (质量)`、`Growth (成长)`、`Health (健康)` 与 `Momentum (动量)` 维度，让优质公司显像化。
*   **🤖 Streaming AI 流式智能研报**
    无缝接入 Google **Gemini 2.5 Flash** 最新大模型。我们将数十项冷血的数据字典投喂至 LLM 提示词矩阵，瞬时生成包含“核心观点、估值诊断、因子解读、潜在风险”的专业 Markdown 财报。配备原生 Event-Stream 打印机效果。
*   **📊 专业级沉浸交互图表**
    完美集成顶级图表库体系。使用 **TradingView Lightweight Charts** 高性能渲染带交互的蜡烛图、成交量潮，并支持动态挂载服务端实时算出的 `MACD`、`RSI`、`MA20/50` 指标。使用 **ECharts** 构建震撼的双 Y 轴（历史金额对比+毛利率走势）柱线复合财务趋势图。
*   **📋 联动侧边栏与持久化自选 (Watchlist)**
    内置暗黑悬浮侧边栏。支持自定义极客自选股，与前端 LocalStorage 永久绑定。对接后端独库并发 `Batch Factors` 评分接口，支持一键依照特定因子（如高成长、低估值）进行列表横向截面降序排名。

---

## 🛠 技术栈概览 (Tech Stack)

### Backend (Data & Core Analysis)
*   **Language**: Python 3.9+
*   **Web Framework**: FastAPI (高性能异步通信)
*   **ORM / DB Driver**: SQLAlchemy 2.0 (Async Engine), `asyncpg`
*   **Database**: PostgreSQL 15 关系型数据底座
*   **Quant & Analytics**: `pandas`, `pandas-ta-classic` (技术指标换算)
*   **AI SDK**: `google-genai` (Gemini API 官方套件)

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
│   └── ai_assistant.py   # Google LLM API Prompt 构建与 Generator 分发
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
*   已安装 Python 3.9 或更高版本
*   已安装并在后台运行 PostgreSQL 15+

### 1. 克隆项目与配置数据库

```bash
# 获取源码
git clone https://github.com/YourUsername/QuantDashboard.git
cd QuantDashboard

# 打开 psql (或您喜欢的 PG 客户端)
psql -U postgres
# 创建对应的本库表 (替换为您自有的设定)
CREATE DATABASE quant_db;
CREATE USER quant_user WITH PASSWORD 'your_secure_password';
GRANT ALL PRIVILEGES ON DATABASE quant_db TO quant_user;
```

### 2. 配置环境变量

项目根目录包含了一个示例环境配置文件：

```bash
cp .env.example .env
```
打开 `.env` 并填入属于您的授权信息与本地库连接：
```env
# Database Credentials
DB_USER=quant_user
DB_PASSWORD=your_secure_password
DB_HOST=127.0.0.1
DB_PORT=5432
DB_NAME=quant_db

# EODHD API Key (Financial Data Provider)
EODHD_API_KEY=your_eodhd_api_key

# Google Gemini API Key (AI Reporting)
GEMINI_API_KEY=your_gemini_api_key
```

### 3. 启动后端服务 (FastAPI)

```bash
# 在根目录建立 Python 虚拟环境
python3 -m venv venv
source venv/bin/activate  # Windows 用户: venv\Scripts\activate

# 安装依赖矩阵
pip install -r requirements.txt

# 使用 Uvicorn 唤起异步雷达网络 (端口常驻 8000)
uvicorn main:app --reload
```
访问 `http://127.0.0.1:8000/docs` 即可查阅/调试全自动生成的 Swagger 互交式 API 文档。

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
