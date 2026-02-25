# 异动归因与新闻聚合系统架构 (Anomaly Attribution & News Aggregation)

## 1. 系统概述
本模块旨在为量化终端引入“消息面”维度，包含两个核心子功能：
1. **单股新闻信息流**：在单股分析页面 (Analysis) 展示该股票的最新相关新闻。
2. **开盘异动监控与 AI 归因**：定时扫描市场异动，抓取相关新闻，并利用 LLM 生成结构化的归因简报。

## 2. 后端架构设计 (Backend - FastAPI & Python)

### 2.1 数据抓取层 (News Fetcher)
* **新建服务**：`services/news_fetcher.py`
* **功能**：接入免费的金融新闻 RSS 订阅源（如 Yahoo Finance RSS: `https://finance.yahoo.com/rss/headline?s={ticker}`）或 EODHD 的 News API。
* **清洗规则**：去除 HTML 标签，提取标题 (Title)、发布时间 (PubDate) 和摘要 (Summary)，并限制只拉取过去 72 小时内的新闻以保证时效性。

### 2.2 异动检测与调度层 (Anomaly Detector)
* **新建服务**：`services/anomaly_detector.py`
* **触发机制**：使用 `APScheduler` (BackgroundScheduler) 设定在美股开盘后 15-30 分钟触发。
* **异动规则**：比对当日开盘价/现价与昨收价，涨跌幅绝对值 `> 4%`，或者开盘 15 分钟成交量显著放大的标的。

### 2.3 AI 归因引擎 (LLM Attribution)
* **扩展服务**：在现有的 `services/ai_assistant.py` 中新增 `generate_anomaly_attribution(ticker, price_change, news_list)` 函数。
* **架构红线 (Strict Rule)**：无论使用 Gemini 还是其他大模型，AI 客户端 (Client) 必须在单个请求/调用函数内部进行初始化。绝对禁止在文件顶部全局初始化 client，以防止并发冲突和环境变量读取失效。
* **Prompt 策略**：输入股票代码、涨跌幅、以及刚刚抓取到的新闻摘要。要求模型严格基于传入的新闻进行归因；若新闻无关联，必须明确输出“缺乏明确新闻催化剂”。

### 2.4 API 路由扩展 (Routers)
* `GET /api/stocks/{ticker}/news`：供前端调用，返回该股票的最新新闻列表。
* `GET /api/market/anomalies`：返回当日的异动股票及 AI 归因简报列表（支持主动查询）。

## 3. 前端架构设计 (Frontend - Next.js & React)

### 3.1 单股分析页面的新闻流 (Stock Analysis Page)
* **新建组件**：`src/components/NewsFeed.tsx`
* **UI 设计**：在分析页面的右侧或下方，新建一个可滚动的卡片区域（类似 Twitter Feed 风格）。
* **数据展示**：每条新闻展示时间标签、醒目的标题，以及两行摘要。支持点击标题跳转至新闻原链接。

### 3.2 异动监控大盘看板 (Anomaly Dashboard)
* **入口**：在顶部的 `TopNavBar` 中新增一个『Market Anomalies』标签。
* **页面设计**：`src/app/anomalies/page.tsx`
* **UI 布局**：左侧为异动股票列表（按涨跌幅绝对值排序，涨幅标绿，跌幅标红）；右侧或下方展示该股票的详细 AI 归因简报（Markdown 渲染）。