# 异动归因与新闻聚合系统架构 (Anomaly Attribution & News Aggregation)

## 1. 系统概述
本模块旨在为量化终端引入“消息面”维度，包含两个核心子功能：
1. **单股新闻信息流**：在单股分析页面 (Analysis) 展示该股票的最新相关新闻。
2. **开盘异动监控与 AI 归因**：定时扫描市场异动，抓取相关新闻，并利用 LLM 生成结构化的归因简报。

## 2. 后端架构设计 (Backend - FastAPI & Python)

### 2.1 数据抓取层 (News Fetcher)
* **新建服务**：`services/news_fetcher.py`
* **功能**：接入免费的金融新闻 RSS 订阅源（如 Yahoo Finance RSS: `https://finance.yahoo.com/rss/headline?s={ticker}`）或 EODHD 的 News API。
* **清洗规则**：去除 HTML 标签，提取标题 (Title)、发布时间 (PubDate) 和摘要 (Summary)。单股新闻页使用 72 小时窗口；异动归因只使用过去 24 小时且带有效发布时间、HTTPS/HTTP 链接的前三条新闻。

### 2.2 异动检测与调度层 (Anomaly Detector)
* **新建服务**：`services/anomaly_detector.py`
* **触发机制**：定时任务由独立 Worker 执行并持久化；前端手动刷新通过异步任务触发，HTTP 请求只创建任务并轮询状态。
* **异动规则**：当前版本使用行情源相对昨收的实时涨跌幅，默认阈值为绝对值 `>= 4%`。返回行情源时间戳并拒绝早于最近已完成交易日的陈旧数据。成交量异动属于后续独立规则，不在当前版本中伪装实现。
* **执行边界**：默认归因前 20 个标的、并发数 3、单标的 30 秒、整轮 90 秒；单个新闻或 LLM 失败以部分结果返回，不影响其他标的。
* **持久化**：`anomaly_scan_runs` 保存 queued/running/completed/failed 状态、实时行情时间、Screener universe 日期及结果 JSON。

### 2.3 AI 归因引擎 (LLM Attribution)
* **扩展服务**：在现有的 `services/ai_assistant.py` 中新增 `generate_anomaly_attribution(ticker, price_change, news_list)` 函数。
* **架构红线 (Strict Rule)**：无论使用 DeepSeek 还是其他大模型，AI 客户端 (Client) 必须在单个请求/调用函数内部进行初始化。绝对禁止在文件顶部全局初始化 client，以防止并发冲突和环境变量读取失效。
* **Prompt 策略**：输入股票代码、涨跌幅、以及刚刚抓取到的新闻摘要。要求模型严格基于传入的新闻进行归因；若新闻无关联，必须明确输出“缺乏明确新闻催化剂”。

### 2.4 API 路由扩展 (Routers)
* `GET /api/stocks/{ticker}/news`：供前端调用，返回该股票的最新新闻列表。
* `GET /api/market/anomalies`：只读取最近一次成功扫描，不触发外部行情、新闻或 LLM 调用。
* `POST /api/market/anomalies/scans`：创建或复用当前 single-flight 扫描任务，立即返回 `202` 和任务 ID。
* `GET /api/market/anomalies/scans/{scan_id}`：查询任务状态及完成后的异动归因结果。

## 3. 前端架构设计 (Frontend - Next.js & React)

### 3.1 单股分析页面的新闻流 (Stock Analysis Page)
* **新建组件**：`src/components/NewsFeed.tsx`
* **UI 设计**：在分析页面的右侧或下方，新建一个可滚动的卡片区域（类似 Twitter Feed 风格）。
* **数据展示**：每条新闻展示时间标签、醒目的标题，以及两行摘要。支持点击标题跳转至新闻原链接；上游故障返回明确的 502，不再与“没有新闻”混淆。

### 3.2 异动监控大盘看板 (Anomaly Dashboard)
* **入口**：在顶部的 `TopNavBar` 中新增一个『Market Anomalies』标签。
* **页面设计**：`src/app/anomalies/page.tsx`
* **UI 布局**：异动股票按涨跌幅绝对值排序，涨幅标绿、跌幅标红；展示实时 quote 时间、归因状态，以及带标题、媒体和发布时间的编号来源。页面打开只读取缓存，手动扫描时轮询后台任务。
