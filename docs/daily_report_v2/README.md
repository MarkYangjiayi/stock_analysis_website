# Daily Report v2

开盘及盘后推送现包含全球市场和固定核心资产；没有股票越过异动阈值时，也会生成完整市场报告。报告由结构化证据确定性渲染。v2.1 的个股章节只展示已保存的新闻标题及引用，不再把 AI 因果判断当成已验证事实。

## 覆盖与口径

默认共 45 项行情：9 个股票市场指标、4 个外汇指标、4 个贵金属代理、2 个能源/工业金属代理、3 个债券价格代理、VIX/BTC/ETH、RSP、11 个板块 ETF 和 8 只核心股票。美国股票市场使用 SPY/QQQ/IWM ETF 代理；海外使用沪深300、恒生、恒生科技、日经225、台湾加权及欧洲斯托克50指数。美元指数使用供应商目录中核验过的 NYICDX.INDX。

黄金/白银/铂金/钯金分别采用 GLD/SLV/PPLT/PALL 上市基金或信托份额（统称 ETF 代理），原油/铜采用 USO/CPER ETF 代理。报价是份额价格，不能视为现货每盎司价格或指定期货合约报价；债券价格采用 TLT/HYG/LQD，不能把它们的价格变化解释为 OAS 信用利差变化。第一版没有接入独立的信用利差序列。

美债曲线复用官方 Treasury 日频服务，展示 2Y/10Y/30Y 收益率及相邻有效观察日的 bp 变化，并列示 10Y−2Y 利差。市场宽度只读取成功发布的 S&P 500 历史成分快照；7个自然日以内的旧快照明确显示历史日期，超过7天或日期无法核验时仅提示缺口、不展示过时指标。原始快照仍保留，不触发庞大的日线回填。

在美股已收盘的当天采集时，美债服务会尝试刷新，即使早报产生的12小时缓存尚未过期；刷新失败仍可带提示使用缓存。

股票/指数采用各自交易所日历，处理节假日、午间休市和提前收盘。报告使用现有 pandas_market_calendars 中的中国及日本日历，避开 exchange_calendars 4.5.6 的中国日历截止2025年和日本旧收盘时间问题。日历无法计算时，行情不会进入当期强弱结论。

## 调度与配置

默认美股交易日 America/New_York 10:00 和 16:30 触发。夏令时随时区切换，US 非交易日不发送。提前收盘日仍在配置的盘后时间触发。原有股票异动上限仍为开盘5只、盘后10只。

| 配置 | 默认 | 含义 |
| --- | --- | --- |
| DAILY_REPORT_MORNING_HOUR / MINUTE | 10 / 0 | 开盘报告时间 |
| DAILY_REPORT_CLOSE_HOUR / MINUTE | 16 / 30 | 盘后报告时间 |
| DAILY_REPORT_CORE_SYMBOLS | AAPL.US,MSFT.US,NVDA.US,AMZN.US,0700.HK,9988.HK,2330.TW,ASML.AS | 最多30只固定核心资产，必须带交易所后缀 |
| DAILY_REPORT_INCLUDE_WATCHLIST | true | 加入服务器自选股 |
| DAILY_REPORT_WATCHLIST_LIMIT | 12 | 核心池以外的自选股上限，0–30 |
| DAILY_REPORT_EVENTS_ENABLED | true | 美国重要经济事件及观察池财报日历 |

目前核心池支持 US/HK/SHG/SHE/TW/TSE/AS/XETRA；未知交易所或无效代码会提示并跳过，避免套用错误交易日历。

## 数据质量和失败处理

- [EODHD delayed snapshots](https://eodhd.com/financial-apis/live-ohlcv-stocks-api) 提供最新快照；每批最多20个标的，最多4个并发请求。数据源按标的计费，批量请求不会降低符号计费数量。
- 即时接口缺失或过期时，尝试最近14天日线回退。单次回退最多10秒，总回退预算30秒；超时保留已完成结果。默认45个标的通常需要45次符号计费，加上必要的回退与日历调用。
- 非有限数值、非正价格、未来报价时间或返回错误的 ticker 不会被当作有效报价。前收盘缺失时，涨跌显示缺失而非0%。
- 当前交易日的未完成日线不会作为收盘回退。复权日收益使用复权收盘之间的比值，避免拆股或分红形成虚假大幅涨跌，并标明口径。
- 市场已收盘但快照明显早于收盘时，标为“收盘行情待更新”。时间晚于收盘的快照不会称作正式收盘价。
- 股票盘中超过45分钟、外汇超过5分钟、加密资产超过10分钟的快照及跨交易日旧报价不参与当期结论。海外市场已完成的最近交易日不会仅因过去数小时而被误判为过期。
- 每个栏目独立降级；异动扫描失败但仍有跨资产证据时，可发送注明覆盖不足的报告。异动和跨资产证据同时失败时，审计记录为 evidence_failed，不发送空白报告。
- 经济事件接口没有明确的时区字段，因此保留供应商原文时间并标注时区未确认，不自行换算或断言事件尚未公布。日历窗口为纽约当日至下一美股交易日，采集最多100条，去重/合并同一时刻与周期的事件后展示最多12组。
- 盘后只和同一个纽约交易日、已成功送达的早报比较；仅对时间有效、属于同一资产交易日的价格计算早报以来变化。没有可比快照时明确说明。

## 审计与上线

新增迁移 `0020_daily_report_market_context`。`DailyReportRun.source_results` 保留原来的个股异动数组；新的 `market_context` JSON 保存归一化报价、时间、前收盘、收益率、宽度、事件、异常提示和用于比较的早报快照。当前代码 `renderer_version` 为 `cross-asset-v2.2`；v2.2 无新增数据库迁移。旧报告无需改写。

部署时先备份数据库并执行 `alembic upgrade head`，再重启 worker 以加载新调度。无需新的数据源密钥；日历及海外数据可用性取决于现有 EODHD 订阅。

主分支部署流水线会在停服前创建并校验 SQLite 在线备份，失败即中止发布；备份保存在 `BACKUP_DIR/predeploy`，独立保留最近两份，不影响每周备份。启动后检查 API/前端健康，并输出数据库迁移版本、worker 报告版本和生效的纽约时间配置。

服务器部署命令超时为30分钟，为大数据库备份和启动检查留出余量。若镜像已加载但启动中断，可手动触发本工作流的 `workflow_dispatch` 恢复分支：仅启动服务器现有镜像并核验健康/版本，不构建、不拉取、不清理镜像、不重复备份；这不是部署新代码的入口。只有普通push流程会执行完整测试和镜像发布。

## 不发送消息的真实数据预览

```bash
python scripts/preview_daily_report.py --output docs/daily_report_v2/preview.md
python scripts/preview_daily_report.py --report-type morning_briefing --output docs/daily_report_v2/morning-preview.md
```

脚本只抓取市场证据，复用当天已有的个股扫描；不会启动额外的 AI 异动扫描或发送通知。Markdown 和伴随 JSON 保存在指定路径，预览可能在盘中生成，不能当作正式收盘报告。

复现文本只需调用 `render_daily_report(evidence['anomalies'], report_type=evidence['report_type'], market_context=evidence['market_context'])`，无需重新抓取行情。

## v2.2 贵金属、BTC 与涨跌颜色

- 首屏摘要固定显示四种贵金属代理和 BTC。缺失、旧行情、涨跌未知及旧快照未覆盖的资产都有明确标记，不用历史涨跌冒充当期行情。
- 明细单独列出贵金属、能源/工业金属、波动率及 BTC/ETH；旧存量快照按 ticker 兼容分组。盘后与早报的比较也覆盖贵金属和 BTC/ETH，仍要求同一资产交易日且报价更新、更具可比性。
- 飞书卡片用原生 Markdown `<font>` 给数值着色：绿涨、红跌，保留正负号及“微升/微降”；平盘和未知不加涨跌色。颜色仅表示方向，不代表投资利好/利空或行情新鲜度。
- 仅行情章节、个股报价头行和利率变动着色；新闻标题、引用、链接、经济日历、收益率绝对值及利差绝对值不变。数据库审计正文保持无颜色标签的完整 Markdown，普通通知不变。
- 卡片最多五个顶层块、明细默认折叠；无个股异动时将提示并入数据提示，不创建空面板。中性底色避免装饰色与涨跌色竞争。已在飞书桌面端验收新配色、展开内容和引用链接；移动端尚未实测。

### v2.2 信息密度改进

- 模块缺口、通用行情口径和新闻因果边界集中说明；逐项报价时间、异常状态及来源仍保留。无数据模块不再创建重复的空标题。
- 同一事件的同比/环比、同一时刻的美联储不同年度利率预测合并展示，实际值/预期缺失不再用大量横线占位；真实零值和无数值的重要事件仍保留。
- 个股标题按保存的公司名称（去除公司后缀）或证券代码做保守的明确提及筛选，过滤关联不明的标题并去重；原始新闻和编号不改写。标题提及不代表相关性/因果关系已验证，该启发式可能漏掉仅使用品牌别名的新闻，不能据此断言没有公司新闻。无法匹配的旧快照明确提示关联不明。
- 盘前预览不把昨日价格变动写成“开盘以来”；仅当期美股基准和核心股保留这一补充指标。自选股名称等于代码时不重复打印。
- 同一份保存证据的 Markdown 从10,013缩至7,309字节（约27%）；这不是对所有报告长度的承诺。

本次 [发布前验收记录](release_20260916/QA_REPORT.md) 包含报价核对、真实卡片验收和回归测试结果。

代理代码核对：[白银 SLV](https://www.ishares.com/us/products/239855/ishares-silver-trust-fund)、[铂金 PPLT](https://www.aberdeeninvestments.com/en-us/investor/funds/view-all-funds/abrdn-physical-platinum-shares-etf-us0032601066)、[钯金 PALL](https://www.aberdeeninvestments.com/docs?editionid=0f2bbbf3-25ce-4024-9343-765d2e4e3e01)。

## v2.1 质量改进与折叠卡片

- 新闻：将“已验证驱动”改为“个股异动与新闻线索”；原始新闻标题和安全链接构成展示证据，不原样展示自由文本 AI 归因。保留无效引用拦截；无新闻时明确无法确认原因，不猜测资金面/技术面。AI 生成端也改为“新闻事实 / 关联判断 / 证据边界”，但提示词本身不被当作事实核验机制。
- 时间：每只异动股显示纽约报价时间（EST/EDT），用采集时点和交易日历区分盘前、旧快照、不同交易日、收盘待更新及有效延迟报价。无明确时区/采集时间时显示未核验；缺失及非有限涨跌值显示未知。
- 数字：真实微小变动若会四舍五入成带符号零，显示“微降/微升（不足 0.01%）”；实际零显示 `0.00%`，不改变任何原始数值。
- 卡片：仅日报显式传入 `card_layout="daily_report"`，采用 Card 2.0 默认宽度。首屏为摘要与数据缺口，三组明细默认折叠，全部资产、新闻链接和口径保留；不需要服务器交互回调。普通系统/RSI 通知保持原格式。旧飞书客户端对 Card 2.0 的兼容性需注意，未测试手机端。
- 审计：`DailyReportRun.content` 仍保存完整 Markdown，折叠仅影响飞书布局。没有更改发送时刻、迁移方案或调度触发。

质量修正版的测试及实际飞书验证：[验证记录](quality_revision_20260915_final/QA_REPORT.md)、[最终回放正文](quality_revision_20260915_final/post.md)、[Card JSON](quality_revision_20260915_final/post.card.json)。完整供应商原始证据及中间验收回放仅保存在本地，不随代码发布；原先 v2 预览和验收卡保持原样，便于前后比较。

仅回放、不发送的命令（输出目录应为新目录）：

```bash
python scripts/qa_daily_report_delivery.py --case post --output-dir /path/to/new-qa-dir --replay /path/to/saved-report.json
```

核对本地生成正文后，显式追加 `--deliver` 且不传 `--replay`，才会发送已保存正文；已尝试发送的证据禁止重复发送。

## 验证记录

相关测试86项通过：`test_daily_reporter`、`test_report_market`、`test_daily_report_migration`、`test_anomalies`、`test_yield_curve`、`test_market_breadth`。覆盖缺失/非法报价、错代码、未来时间、交易日与夏令时、提前收盘、日本延长交易时间、中国2026年日历、日线回退与复权收益、bp 换算、缓存刷新、各栏目独立降级、固定池/自选去重、调度配置、旧报告迁移及早晚快照选择。

本地真实采集预览：[报告](preview.md)，完整证据保存在本地 `preview.json`。采集时间为2026-09-14 16:46 UTC（美股盘中）；42项行情均有可用观察，正文约7.1 KB。当地已收盘市场使用对应交易日的最新观察。当前本地数据库没有可用的已发布市场宽度，因此明确展示缺失提示。已验证保存证据能重放相同报告，预览未发送消息。

参考：[EODHD 行情口径](https://eodhd.com/financial-apis/live-ohlcv-stocks-api)、[经济日历](https://eodhd.com/financial-apis/economic-events-data-api)、[财报日历](https://eodhd.com/financial-apis/calendar-upcoming-earnings-ipos-and-splits)、[JPX 收盘时间变更](https://www.jpx.co.jp/english/corporate/news/news-releases/1030/20230920-01.html)。
