# 题材与热门公司追踪：开源方案、数据源与实现路线调研

调研日期：2026-10-10。调研对象是给 QuantDashboard 加一个"当前市场在炒什么题材、哪些公司最热，最好能提前发现新题材"的功能。

本次调研由 32 个子 agent 完成：8 个方向的网络调研，1 个本地代码库适配分析；之后对 159 个候选逐条做独立事实核查，再补做 4 个缺口专项调研（含一个小样本回测试点），最后出了两份独立的设计方案。所有结论尽量基于一手来源（官方文档、定价页、GitHub API、SEC/Wikimedia 接口实测）。查不到一手证据的标为"未核实"。

**目录内文件**

| 文件 | 内容 |
|---|---|
| `README.md`（本文） | 结论、开源方案、数据源、方法、推荐路线、待决策事项 |
| [design_lean_mvp.md](design_lean_mvp.md) | 方案 A：基于现有栈的精益 MVP，1–2 周，含表结构、作业、公式、API、前端、测试 |
| [design_early_detection.md](design_early_detection.md) | 方案 B：早期发现与信号质量优先，分 v0–v3 路线，含评估框架 |
| [appendix_a_eodhd_news_cost.md](appendix_a_eodhd_news_cost.md) | EODHD News/Sentiment/ETF 持仓的能力、真实调用成本、许可（含 demo key 实测数据） |
| [appendix_b_taxonomy_etf_holdings.md](appendix_b_taxonomy_etf_holdings.md) | 题材分类种子（Finviz）与专题 ETF 持仓历史来源（发行商文件、N-PORT、EODHD） |
| [appendix_c_attention_pilot.md](appendix_c_attention_pilot.md) | 试点：免费关注度数据能否领先题材行情（14 个真题材 + 10 个对照组） |
| [appendix_d_backfill_corpora.md](appendix_d_backfill_corpora.md) | 可回填的历史语料：SEC 8-K EX-99、Alpaca/Benzinga、通讯社 RSS、Wikimedia、X counts |
| [tools/eodhd_cost_probe.py](tools/eodhd_cost_probe.py) | 用自己的 EODHD key 实测各端点调用成本的脚本 |
| [pilot/](pilot/) | 试点代码与结果表 |

附录 A–D 是调研原始记录（英文），数字和出处最全；本文是中文汇总。

---

## 0. 结论

1. **没有能直接拿来用的开源方案，建议自建。** 美股方向没有任何开源项目能端到端完成"发现题材 → 映射到公司 → 计算热度"。
   - 社交热股类项目几乎都是 2021 年前后基于 Reddit PRAW 的脚本，已停更，而且 Reddit 公共 API 将在 2027 年 3 月终止。
   - Agent 类框架（TradingAgents、ai-hedge-fund、FinRobot、OpenBB 等）都是"给一个 ticker、出一份报告"的模式，不会发现题材。
   - 最接近的是 `tradermonty/claude-trading-skills` 里的 theme-detector（MIT）。它的粒度只到 Finviz 行业级，分不出量子、SMR、GLP-1、人形机器人这类窄题材。
   - A 股方向的项目成熟得多（go-stock、vibe-astock、TrendRadar），但依赖的数据和"涨停原因"机制在美股没有对应物。
   - 可以借鉴的是设计和算法，见第 1 节。
2. **难点在题材分类和成分股映射，热度计算本身不难。** 最便宜的种子有三个：
   - Finviz 今年上线的 40 个主题、268 个子主题；
   - 专题 ETF 的持仓文件（Roundhill、Global X、KraneShares 有带日期的历史日文件）；
   - 免费且时点正确的 SEC N-PORT 季度持仓。

   公司和题材的对应关系建议这样建：ETF 持仓、业务描述关键词、人工种子三路召回，再让 DeepSeek 核验，必须引用原文。不要让 LLM 单独生成成分股篮子，多项研究显示它偏科技、偏大盘，且不稳定。
3. **检测逻辑以价格、宽度、量能为主，关注度只用来提名候选。** 理由有两点：
   - 文献证据：关注度飙升后往往反转（Da-Engelberg-Gao 2011、Barber 等 2022、Cookson 等 2024）。
   - 本次小样本试点（14 个题材、10 个伪热点对照）：Wikipedia 和 HN 的关注度信号在 10 个对照组上全部触发，"领先价格突破"的比例也不比随机高。只有 SEC 8-K 全文计数能区分真题材和一时热点（真题材 10/14 持续触发，对照组 0/10），但它滞后约 12 周，适合做确认门。
4. **你已付费的 EODHD 能覆盖一大半需求。** 目前代码里还没用到 `/api/news` 和 `/api/sentiments`。sentiments 的每日 `count` 就是逐只股票的新闻篇数，可以直接当关注度序列用。需要注意：
   - 93–96% 的新闻链接指向 Yahoo Finance，和现有 Yahoo RSS 高度重叠；
   - 2025 年中以前 tag 很少；
   - 2026-10-10 已用你的 key 跑过 [探测脚本](tools/eodhd_cost_probe.py)：**不带 ticker 的全市场 news feed 可用**，`limit=1000` 每页只算 5 次调用，周五（10-09）全市场 1,897 篇。每天拉全市场约 10 次调用，逐只股票的篇数可以在本地数出来；不需要按 ticker 计费的 `/api/sentiments`（实测每 ticker 5 次）。详见 [§2.1 实测结果](#21-已付费eodhd最值得先用)。
5. **许可约束：这个功能只能你自己看。** 以下条款都禁止向他人展示数据或其衍生结果：
   - EODHD 个人版条款禁止向他人展示数据，包括加工后的形式；
   - Global X 的持仓文件禁止衍生作品，iShares 只允许个人非商业使用；
   - Alpaca/Benzinga 新闻禁止公开展示。

   新页面应放在 admin key 后面。另外，从已有的审计文档看，站点已经部署在公网，现有公开页面展示的 EODHD 衍生数据也有同样的问题。这不在本次范围内，但值得你知道。
6. **推荐路线：先做精益 MVP（方案 A，约 2 周），再按评估结果加发现器（方案 B 的 v2）。**
   - MVP 的增量成本：DeepSeek 约 $1–10/月；EODHD 在现有日配额内（最坏每天多约 1.6 万次调用）；不新增 Python 依赖。
   - 发现新题材时采用"多证据族同时成立 + 人工审核"，不自动上线。

---

## 1. 开源方案调研

### 1.1 总览

| 类别 | 代表项目（2026-10 状态） | 能提供什么 | 结论 |
|---|---|---|---|
| 美股题材热度 | **tradermonty/claude-trading-skills · theme-detector**（MIT，2,977★，每天都有提交） | Heat = 0.35 动量 + 0.20 量能 + 0.25 上升趋势 + 0.20 宽度；五段生命周期（Emerging/Accelerating/Trending/Mature/Exhausting）；同题材 ETF 数量作拥挤度；可选的"领涨证据"；`--discover-themes` 把未匹配的行业聚成候选 | **借鉴设计，不直接用**。约 15 个固定题材，按 Finviz 行业匹配，窄题材分不出来；依赖抓 Finviz；uptrend 数据来自另一个无许可的仓库 |
| 新兴话题检测 | **BERTrend**（rte-france，MPL-2.0，PyPI 0.4.19） | 在 BERTopic 上按时间片建模并合并，popularity 指数衰减（λ=0.01），按分位数分噪声 / 弱信号 / 强信号 | **照抄算法（约 100 行），不装包**。依赖很重（RabbitMQ、nicegui、限制 sentence-transformers 版本），推荐 GPU，评估语料是通用新闻 |
| 社交热股追踪 | iam-abbas/Reddit-Stock-Trends（1.6k★，2022-03 停更）、asad70（2023-03）、Stocksera（2024-08）、wsbtickerbot（已归档） | 用 PRAW 数原始提及次数 | **不可用**。Reddit 公共 API 正在关闭，且只数原始次数 |
| 社交热股（新） | **StephanAkkerman** 的 reddit-stock-analyzer / stock-recognizer / fintwit-web（MIT，2026-09/10 仍活跃，0–1★） | 平滑动量 `(m−prev)/(prev+1)`；每帖每 ticker 只计一次；单作者最多计 3 次；"新晋"判定；fintwit-web 的架构（FastAPI + SQLite + worker）和本项目几乎一样 | **借公式**。单人维护，Reddit 和 X 的数据获取方式有条款风险 |
| 投研平台 | OpenBB v5（5.0.0 于 2026-09-30 发布，改为 Apache-2.0，同时移除了 yfinance/FMP/Benzinga/Finviz 等 16 个 provider） | 只有通用的涨跌榜、活跃榜；免 key 的有 Nasdaq movers、ETF 反查持仓、562 个 RSS 源 | **不作依赖**。没有题材能力，整体引入太重 |
| LLM Agent | TradingAgents（110k★，Apache-2.0）、ai-hedge-fund（64k★，MIT）、FinRobot（8.2k★）、FinGPT（21k★，模型停在 2024-10）、Dexter（无许可） | 对单只 ticker 做深度研究 | **不解决发现题材的问题**。TradingAgents 以后可以做"已检测到的公司"的可选深挖 |
| 量化框架 | Qlib（49k★）、FinRL（16.6k★）、RD-Agent | 价格因子、回测、强化学习 | 与题材检测无关，现有 Factor Lab 已覆盖动量部分 |
| A 股题材 | go-stock（GPL-3.0，7.8k★）、**vibe-astock**（Apache-2.0，672★）、TrendRadar（GPL-3.0，62.8k★）、plate-rotation-skill（MIT） | 每日炒作题材、从涨停原因拆出的题材树、多平台热榜聚合 + LLM + 飞书推送、同花顺 × 开盘啦交叉验证（"真主线 vs 妖板"） | **借鉴数据模型和 UX**。数据只覆盖 A 股；GPL 项目的代码不能复制 |
| 其他小项目 | ThemePulse-AI、sector-tracker、momentum-terminal 等（0–2★） | 个人原型 | 证明了这块确实空白 |

### 1.2 可以直接复用的开源组件（v2 及以后）

| 组件 | 用途 | 状态 | 注意 |
|---|---|---|---|
| **BERTopic** 0.17.4（MIT） | 用 `zeroshot_topic_list` 把文档分到已知题材，剩下的聚出新 topic；`topics_over_time`；`merge_models(min_similarity≈0.7)` 合并后找新 topic | 维护节奏慢（2025-12 之后没有发布） | `partial_fit` 会失去 HDBSCAN 的新颖性检测，所以用"每周一个模型再 merge"；短标题聚类效果差 |
| **fastembed**（Apache-2.0）/ **model2vec**（MIT） | CPU 上算 embedding，不需要 torch（`bge-small-en-v1.5` 约 67MB） | 活跃 | DeepSeek 没有 embedding 接口，只能本地跑；model2vec 的 `distill` 需要 torch |
| **networkx 3.7**（BSD） | Louvain/Leiden 社区发现、最小生成树 | 2026-09-21 发布 | `leiden_communities` 默认 `metric='cpm'`，要显式设为 `modularity` |
| **ruptures**（BSD-2） | 变点检测 | 活跃 | Kleinberg burst 的 Python 包许可不清，自己重写约 60 行 |
| **datasketch**（MIT） | MinHash 去重，去掉转载副本 | 活跃 | 不去重的话，"爆发"多半只是转载造成的 |
| **pyahocorasick + rapidfuzz** | 公司名、别名到 ticker 的匹配 | 活跃 | 常用词 ticker（AI、IT、ON、ALL、NOW）需要停用表 |
| **GLiNER2**（Apache-2.0） | 零样本实体识别 | 活跃 | 本地推理需要 torch，放到 v3 |
| **edgartools**（MIT） | 8-K 新闻稿、全文检索、N-PORT、13F、Form 4 | 活跃（v5.61） | **仓库目前固定在 2.34.2**，新功能需要升级 |
| alphalens-reloaded / vectorbt | 离线评估信号 | 前者已转入只修 bug 的维护模式 | 只放在研究脚本里用 |

---

## 2. 数据源调研

价格以 2026-10-10 看到的为准。"状态"一列：**已核实**表示一手来源确认；**有更正**表示核查发现原说法有误、已修正；**未核实**表示没有找到一手证据。

### 2.1 已付费：EODHD（最值得先用）

| 端点 | 能力 | 成本与限制 | 状态 |
|---|---|---|---|
| `/api/news` | 全文、`symbols[]`、`tags[]`、情绪分；`limit` ≤1000，`offset` 实测没有上限；`t=` 是大小写不敏感的精确匹配，不做连字符和空格归一化 | 每次请求 5 次调用（4 个官方来源一致）；**每次只能查 1 个 ticker**（多 ticker 返回 422）；3,100 只全拉约 1.55 万次/天 | 有更正 |
| ↳ 数据质量 | 93–96% 的链接指向 finance.yahoo.com；`symbols[]` 按字母排序且最多 50 个，只有 35% 是 `.US`，有 2–3% 的文章漏掉了它自己的主角 ticker；AI 标签是开放词表（3 个月内出现 12,314 个，77% 只出现一次），2025 年中才开始普及；2024 年 11–12 月几乎断档，2025-08-12 至 08-21 有大规模误标；情绪分不可靠（"Trading Lower" 的标题打出 0.99 正面） | 满量数据约从 2021-03 开始，更早很稀疏 | 已核实（demo key 实测） |
| ↳ 不带 `s`/`t` 的全市场 feed | **实测可用**（2026-10-10，你的 key）：`limit=1000` 返回 1,000 条，`offset` 到 200,000 仍有数据 | 每页 5 次调用；10-09 当天全市场 1,897 篇，每天约 2 页、10 次调用；回填一年约 3,700 次 | 已核实 |
| `/api/sentiments` | 支持多 ticker；每天返回 `count` 和 `normalized`（−1..1）。**`count` 就是逐只股票的新闻篇数**（30 天总量实测吻合）；AAPL 历史从 2012 年开始 | **实测每 ticker 5 次调用**，与日期范围无关；单次最多 100 只（101 只返回 422）；3,100 只全覆盖约 1.55 万次 | 已核实 |
| `/api/news-word-weights` | 按 ticker 给关键词权重 | 只基于约 300 篇样本，结果多是泛词（stock、market、ai） | 价值低 |
| `/api/screener` | 只有 6 个信号：200d_new_lo/hi、bookvalue_neg/pos、wallstreet_lo/hi | 每次 5 次调用；只有 EOD+Intraday 和 All-In-One 套餐有；是收盘快照，不能回测 | 有更正（文档里没有 50d 信号） |
| `fundamentals/{ETF}` → `ETF_Data.Holdings` | 完整持仓、权重、Inception_Date，覆盖 10,000+ 只 ETF | 每次 10 次调用；**只有当前持仓，没有历史**；bulk 接口不含 ETF；小众专题 ETF 的覆盖度未知 | 已核实 / 覆盖度未核实 |
| 期权附加包（Unicorn Bay） | 6,000+ 标的的 EOD 期权数据，约 2 年历史 | 前 3 个月 $29.99，之后 $39.99/月 | 已核实 |
| **许可** | 个人版允许自己存储和分析；**禁止向他人展示，包括加工后的形式**；商业版从 $399/月起 | — | 已核实 |

代码库现状：只用到 fundamentals、eod、bulk EOD、real-time、日历、UST 等端点，news、sentiment 和 screener 都没用过。从注释推断套餐日配额是 10 万次，目前约用掉 1.5 万次。

**用你的 key 实测（2026-10-10 美东周六下午，worker 空闲，所有读数都没有被其他调用干扰）**

| 测试 | HTTP | 实际消耗 | 结果 |
|---|---|---|---|
| `news s=AAPL.US limit=50` / `limit=1000` | 200 | 5 / 5 | 计费和 `limit` 无关 |
| `news s=AAPL.US,TSLA.US` | 422 | 0 | 只允许 1 个 ticker |
| `news t=AI` / `t=artificial-intelligence` / `t=artificial intelligence` | 200 | 5 各 | 181 / 17 / 0 条，标签是精确匹配 |
| `news` 不带 `s`、`t`，`limit=1000` | 200 | 5 | **全市场 feed 可用**；2026-10-09 共 1,897 篇；`offset` 200,000 仍有数据 |
| `news s=INVALIDXX.US` | 200 | 5 | 不存在的 ticker 也收费 |
| `sentiments` 1 / 10 / 50 / 100 只 | 200 | 5 / 50 / 245 / 500 | 每 ticker 5 次；1 天和 30 天价格一样 |
| `sentiments` 101 只 | 422 | 0 | 上限 100 |
| `news-word-weights` | 200 | 5 | 只处理 300 / 6,314 篇 |
| `fundamentals/URA.US`（有无 `filter`） | 200 | 10 / 10 | 48 只持仓；`filter` 不省钱 |
| 40 只小盘股 30 日新闻篇数 | 200 | 200 | 2 只为 0，中位数 12，最大 158 |

账户信息：`dailyRateLimit` = 100,000；测试前当天已用 2,003 次。

**由此确定的语料路线**：每天用全市场 feed 拉前一个交易日的新闻（约 10 次调用），在本地去重并按 `symbols[]` 统计每只股票的篇数，`symbols[]` 达到 50 个上限的文章再用标题和正文补匹配。历史基线也用同一个 feed 按日期回填，从 2025-09-01 起约 3,700 次调用，保证统计口径一致。`/api/sentiments` 不再需要。

### 2.2 题材分类与成分股来源

| 来源 | 内容 | 历史 | 成本 | 状态 |
|---|---|---|---|---|
| **Finviz Themes** | 40 个主题、268 个子主题（如 `subtheme_aicompute`、`energybasenuclear`、`quantumhardware`），2026-01-23 上线；免费站可用筛选器 | **没有成员历史** | Elite $39.50/月或 $299.50/年，含导出 API（10 次/5 秒；全量扫一遍约 308 次请求，约 2.6 分钟）；导出结果里没有 theme 列 | 分类已核实；**使用条款没找到**；成员划得很宽（AMZN、GOOGL 也算量子） |
| **发行商持仓日文件** | Roundhill（约 2023-12 起）、KraneShares（约 2024-01 起）、Global X（2024-08-06 起）、Tema（2026-08-18 起）有按日期命名的历史文件；ARK、VanEck、iShares、Defiance、Themes 只有当前文件 | 见左 | 免费；Global X 禁止衍生作品，iShares 只允许个人非商业使用 | 已核实 |
| **SEC N-PORT** | 所有基金的持仓，包括专题 ETF 和其中的互换 | **2019Q4 起，季度公开**（季末月），申报后最多 60 天可见；月度公开推迟到 2027-11 之后，2026 年的新提案又打算改回只公开季末 | 免费、合法 | 已核实 |
| 新上市的窄题材 ETF | DRAM（2026-04-02）、LYTE、NCLD（2026-08-06）、HUMN、KOID（2025-06）、OZEM（2024-05）、STBQ（2025-12）、DRNZ（2025-10）等 | — | — | 已核实；ETF 上市本身就是"题材确认或已拥挤"的信号 |
| Finnhub Investment Themes | 24 个静态主题 | — | 付费，$50/月/市场 | 太粗，没有核电、量子、GLP-1 |
| 中文平台的概念板块 | 同花顺约 390 个概念，东方财富约 460 个概念板块 | Tushare 的 `dc_member` 从 2024-12-20 起有每日成分快照 | 见 2.6 | 主要借鉴数据模型 |

### 2.3 新闻与文本语料

| 来源 | 历史 | 体量 | 成本 | 许可与限制 | 结论 |
|---|---|---|---|---|---|
| EODHD news | 约 2021 起 | 大盘股每天 26–45 篇 | 已付费 | 只能自己用 | v1 用 |
| **SEC 8-K EX-99 新闻稿** | 2001 起 | R3000 公司约 110–125 份 8-K/天，约 65% 附 EX-99；2016–2026 回填约 28 万篇、约 5GB 文本（压缩后 1.7GB）、约 60 万次请求，按 5 次/秒约 33 小时 | 免费 | 公共记录，需要带联系方式的 User-Agent、≤10 次/秒；很多产品和合作类新闻稿不会走 8-K | **主语料，v1 增量、v2 回填** |
| **EDGAR 全文检索**（efts） | 2001 起 | 支持短语、NEAR、OR | 免费 | 未正式文档化；命中是按文档算的，要按 `adsh` 去重；单次查询上限 1 万条 | **v1 用作确认门** |
| Alpaca News（Benzinga） | 2015 起 | 130+ 篇/天 | 可能免费 | 免费 Basic 是否含新闻**未核实**；条款写了"仅限美国居民"；禁止公开展示 | 你确认资格后再用 |
| Massive（**原 Polygon.io，2025-10-30 改名**） | 付费套餐 2016 起 | — | Starter $29/月；Benzinga 数据集每个 $99/月 | 仅限个人 | 可选 |
| Finnhub | 免费 1 年公司新闻 | — | 新闻和社交情绪要 Fundamental-1，$50/月/市场，按季付 | — | 可选 |
| Marketaux | 3 年 | 自带"热门实体"聚合 | 从 Standard $49/月起 | 打分公式不公开 | 可选 |
| Alpha Vantage NEWS_SENTIMENT | — | 15 个粗主题 | **现在是付费功能**，$49.99/月起 | — | 不推荐 |
| Tiingo / NewsAPI / Intrinio | 3 个月 / — / — | — | $30 / $449 / ≥$1,250 每月 | — | 不推荐 |
| 通讯社 RSS（GlobeNewswire、PR Newswire） | 只有最近 20 条 | — | 免费 | PR Newswire 条款禁止爬虫、数据挖掘和 AI 训练 | 最多只拿标题和 ticker，往后存 |

### 2.4 关注度数据

| 来源 | 能力 | 2026 年现状 | 结论 |
|---|---|---|---|
| **Reddit 官方 API / PRAW** | 社区全文 | 自助申请 key 据报道已于 2025-11-11 停止（低可信）；2026-10-31 起不再接受新申请；RSS 于 2026-11-13 停止；未注册应用从 2027-01-12 起被移除；**2027 年 3 月公共 Data API 全部终止**（来源：developers.reddit.com/app-registration，2026-10-10 获取）；匿名访问 `.json` 已返回 403 | **不要自建采集** |
| **ApeWisdom API** | 免费、无需 key；约 835 只 ticker 的提及数，附带 24 小时前的数值；扫描约 15 个子版块和 4chan /biz/ | 可用；只给快照，必须自己存档；ticker 识别噪声大（IT、YOU、ALL、ON）；依赖 Reddit | 可选：每小时存一次快照 |
| Tradestie | WSB 前 50，日度历史从约 2021-04 起 | 可用；**情绪字段对每个 ticker 是固定值** | 只能用评论数做回测 |
| Stocktwits | 公开的 trending 端点（trending_score，附 AI 写的"为什么上榜"） | 不登录也能访问，但**开发者注册已关闭；条款（2026-07-10）第 5 节禁止自动抓取** | 不用 |
| **X API counts** | 按请求计费（自 2026-02-06 起）：counts/recent $0.005，counts/all $0.010 | `$` cashtag 运算符能否用在 counts 端点上，文档互相矛盾；单个关键词回填 2016–2026 约 $1.27 | 先花约 $5 实测，只查题材关键词 |
| Google Trends | 官方 API 是 alpha，需申请（2025-07-24 公布）；pytrends 已于 2024-08 归档；SerpApi 每月免费 250 次 | 没有可靠的免费程序化途径 | 可以先申请 alpha |
| **Wikipedia 浏览量 API** | 2015-07 起的每日浏览量，免费，CC0 | 2026 年新限速：带合规 User-Agent 200 次/分钟；2025 年 bot 流量重新分类后，真人浏览量同比约 −8%，需要归一化 | 低权重提名特征 |
| HN Algolia | 免费，10k 次/小时/IP | 偏科技 | 只给科技题材做提名 |
| GDELT | 免费的全球新闻量 | DOC API 限 1 次/5 秒且频繁 429，**2026-10 数据明显降级** | 不用 |
| Bluesky | 2026-01 支持 cashtag，Jetstream 免费 | 量太小（NVDA 24 小时约 86 帖） | 不用 |
| Adanos | Reddit、X、新闻多源的 buzz 分和热门行业 | Hobby 版 $29/月，非商业；新厂商，方法是黑盒 | 不推荐 |
| Quiver / AltIndex | 曾经有 WSB、Wikipedia 数据集 | **Quiver 的 WSB 数据停在 2025-02-21**；AltIndex 的社交数据标为 Delayed | 不用 |
| Nasdaq RTAT | 散户成交占比，时点正确，2016 起 | 免费的只有前 10（RTAT10），全量价格要登录才能看 | 可选，用作拥挤度参考 |
| Polymarket / Kalshi | 事件概率 | 交易量主要是体育和政治 | 只做少数题材的催化剂参考 |
| YouTube Data API | 视频标题、浏览量 | 条款要求 30 天内删除数据，且禁止衍生指标 | 不用 |

### 2.5 市场确认类

FINRA Reg SHO 每日卖空量（2018-08 起，免费）和半月度空头仓位；13F / Form 4（edgartools，滞后）；EODHD 期权附加包；Unusual Whales（$150/月起，历史只有 2 年）。这些都属于"确认或拥挤"信号，不能用来发现题材。

### 2.6 A 股和港股（以借鉴为主）

- **AKShare**（MIT，v1.19.1）：以下函数名已核实。
  - 东方财富人气榜：`stock_hot_rank_em`、`stock_hot_up_em`（飙升榜）、`stock_hot_keyword_em`（个股的概念热词）、`stock_hot_rank_detail_em`（**现在只有约 120 天历史**）、`stock_hk_hot_rank_em`
  - 雪球：`stock_hot_follow_xq` / `tweet_xq` / `deal_xq`
  - 百度：`stock_hot_search_baidu`（参数要传 `全市场`，不是 `全部`）
  - 东方财富概念板块：`stock_board_concept_name_em` / `cons_em` / `hist_em`
  - 同花顺概念板块：`stock_board_concept_name_ths` 等
  - 涨停池：`stock_zt_pool_em`
  - 财联社电报：`stock_info_global_cls`

  可用性：东方财富 push2 接口自 2026-01 起被限流，探测时返回 502；push2ex 和 emappdata 仍可用；雪球需要过 WAF；pywencai 在 2026-06 问财升级后就基本不能用了。
- **Tushare Pro**：有同花顺、东方财富、通达信、开盘啦的板块和热榜数据，包括美股的 `dc_hot` 和 `ths_hot`。需要 6000–8000 积分，年费大约 ¥500–1000。
- **富途 OpenAPI**：`get_hot_list` 覆盖港股和美股，热度拆成成交、搜索、新闻三项；支持概念板块。**长桥 OpenAPI**：`rank_list`，例如 `hot_all-us`。两者都需要券商账户，富途还要跑本地 OpenD 进程。
- 中文平台的美股热榜：同花顺 `stock_type=usa`（50 只）、东方财富股吧美股人气前 100。两者都是非官方接口。

**最值得借鉴的是中文平台的数据模型**：
- 题材主表：代码、名称、驱动事件、描述；
- 成分股：入选理由加日期出处，东方财富 F10 还有 `SELECTED_BOARD_REASON` 和 `IS_PRECISE`（精准匹配）标记；
- 龙头标记；
- 题材每日指数；
- 热度信号：涨停家数、资金流、人气排名、连续上榜天数。

### 2.7 明确不推荐的数据源

所有基于 PRAW 的 Reddit 采集、抓取 Stocktwits、twscrape/twikit 抓 X、pytrends、GDELT DOC API、Quiver 的 WSB 数据、Tradestie 的情绪字段、YouTube 衍生指标、EODHD word-weights、全量 Wikipedia dump（2016–2026 约 490GB）、Arctic Shift Reddit 全量（约 3.8TB）。

---

## 3. 方法调研

### 3.1 价格、宽度、量能（检测主干）

| 方法 | 公式要点 | 证据 | 用在哪里 |
|---|---|---|---|
| 行业/题材动量 | 篮子相对 SPY 的 R21、R63、R126 | Moskowitz-Grinblatt 1999；但 Ehsani-Linnainmaa 2022 发现因子动量包含了行业动量 | 先剥离已有的 lfq-v1 因子暴露，再判断是否为题材 |
| 52 周高点接近度 | P/max₂₅₂；题材层面用成员接近新高的比例 | George-Hwang 2004；Hong-Jordan-Liu 2015（行业层面） | 宽度和龙头排序 |
| 均线距离 MAD | SMA21/SMA200 | Avramov 等 2021 | 平滑的趋势分 |
| 宽度 | 成员站上 MA50 的比例、10 日变化、新高比例 | 实务做法；可直接复用 `market_breadth.py` | 美股版"涨停家数"：当日 ≥+5% 且 RVOL≥2 的成员数 |
| 异常量能 | ln(DV5/median DV60) | Gervais-Kaniel-Mingelgrin 2001 | 点火信号；持续放量反而偏负 |
| 收益共振社区 | 残差收益 → 去掉市场模态和 Marchenko-Pastur 噪声 → Leiden → 跨窗口 Jaccard 匹配 | MacMahon-Garlaschelli 2015 | 发现跨行业的新篮子（方案 B 的 D2） |
| 拥挤度 | 成员残差两两相关均值（comomentum 的题材版） | Lou-Polk 2022：comomentum 处于最高 20% 时，动量次年收益低 12.7% | "拥挤"阶段（题材版**未经文献验证**） |
| 泡沫诊断 | 2 年相对市场涨 100% 以上、增发占比、加速度、年轻公司表现 | Greenwood-Shleifer-You 2019：这种情况崩盘概率约 53%，但平均还要再涨约 30%、约 6 个月后见顶 | 晚期风险标记 |
| ETF 上市 | 近 12 个月有新的专属 ETF | Ben-David 等 2023：专属 ETF 上市后 5 年风险调整后约 −30% | 拥挤或晚期标记 |

### 3.2 关注度：只能提名候选

- Google 搜索量上升 → 之后 2 周价格偏高，一年内反转（Da-Engelberg-Gao 2011）。
- Robinhood 用户集中买入的股票，此后 20 天约 −4.7%（Barber 等 2022）。
- 社交媒体关注度预示次日收益为负（Cookson 等 2024）。
- 叙事关注度的半年趋势有正向溢价（Lee-Lou-Ozik-Sadka 2023，FF6 α 约 7%/年）。但该文的"强度"是负面情绪占比，不是关注量，所以换成浏览量属于**未经检验的改造**。

设计含义：短期的关注度飙升只用来提名候选、标记过热；只有关注度没有价格配合时，标为"关注先行·价格未确认"，不计入热度。

### 3.3 文本发现与公司映射

- **已知题材**：用题材定义、关键词、种子公司的中心向量，与文档或公司描述做 embedding 相似度召回；也可以用 BERTopic 的 zero-shot 模式。
- **新题材**：
  - 对没归入已知题材的剩余文档，每周聚一次类，再用 `merge_models` 合并，没被合并的就是新 topic；
  - 加 BERTrend 的 popularity 衰减；
  - 再做 Kleinberg burst 检验；
  - 看相对 1–2 年背景语料新出现的 n-gram（Kalyani 等 QJE 2025），能在聚类成形前抓到 "SMR""HBM4""humanoid" 这类新词。
- **公司到题材**：
  - 用 ETF 持仓做弱标签和评估集。THEME 论文（CIKM 2025）用 1,153 只专题 ETF 构建了约 200 个题材；GPT-4.1 零样本的 HR@3 是 0.71，P@3 只有 0.52。
  - 用 10-K Item 1 或 EODHD 描述的 embedding 召回。
  - 再让 DeepSeek 核验，必须给出原文引用。
  - MSCI Relevance Score 可以作为成员门槛的参考：主题相关收入占比 ≥25%。
- **LLM 的风险**：
  - 7 个 LLM 给同一批电话会打分，排名相关只有 0.52（arXiv 2609.31013）；
  - LLM 偏向科技和大盘股（arXiv 2507.20957）；
  - 回测时 LLM 已经知道哪些题材后来成功了，所以历史测试不用 LLM，或者把公司名匿名化。

### 3.4 试点结果：免费关注度能否领先？

完整过程和数据见 [附录 C](appendix_c_attention_pilot.md)。

- **样本**：14 个真题材（大麻、EV、SPAC、元宇宙、GenAI、AI 电力、核电、量子、GLP-1、人形机器人、稳定币、航天、无人机、存储/HBM），10 个伪热点（NFT、Web3、LK-99、室温超导、培养肉、垂直农业、核聚变、Vision Pro、Clubhouse 等）。
- **规则**：周度稳健 z（52 周中位数和 MAD），z>3 算触发；"持续"指 3 周中至少 2 周触发。

| 数据源 | 真题材触发 | 对照组触发（z>3 / 持续） | 首次触发早于价格突破 | 判断 |
|---|---|---|---|---|
| Wikipedia 浏览量 | 13/14 | **10/10 / 9/10** | 4/7（随机预期约 68%） | 只能提名 |
| HN | 14/14 | **10/10 / 8/10** | 5/7（随机预期约 76%） | 只能给科技题材提名 |
| EDGAR 8-K 计数 | 14/14（持续触发 10/14） | 2/10 / **0/10** | 3/7（相对突破中位滞后约 19 周；相对首个催化剂中位滞后约 12 周） | **确认门** |
| EDGAR 首次提及的公司数 | 9/14 | 0/10 / 0/10 | 2/7 | 确认门 |
| **Rule B**：Wiki 或 HN 近 4 周 z>3，**且** EDGAR 近 8 周 z>2 | 14/14 | **1/10**（Web3） | 4/7（中位提前 3 周）；早于 ETF 上市 6/11（中位提前 12 周） | **作为要击败的基线** |

局限：只有 14 个题材，有效突破只有 7 个；价格参照用的是代理 ETF；短语和时间窗都是事后挑的。用 EODHD 真实篮子重跑的脚本是 [`pilot/code/eodhd_join.py`](pilot/code/eodhd_join.py)。

### 3.5 评估方法

- **时点正确（PIT）**：
  - 成员表用 `valid_from` / `valid_to` 记录区间；
  - 回测只用 N-PORT 加发行商日文件；
  - 现在的 `tickers.description` 没有历史版本，**不能用于回测**；
  - 只有 S&P 500 有严格的历史成分，统计检验只在它上面做。
- **指标**：
  - 相对首只专属 ETF 上市、价格突破、首个催化剂、价格顶点的领先周数；
  - 对照组误报率；
  - 成员质量：对比当时已存在的 ETF 持仓算 P@10、HR@10；
  - 事件研究：对比匹配好的安慰剂篮子；
  - 个股层面的 Rank-IC，复用 `services/quant/research.py`，门槛 t>3（Harvey-Liu-Zhu）。
- **样本**：规则化选样并预先登记，目标扩到约 30 个题材和 30 个对照，包括已清盘的 ETF。

---

## 4. 商业产品参考（只借鉴设计）

| 产品 | 值得抄的设计 |
|---|---|
| **同花顺热榜** | 热度排名、概念标签、"连续 N 天上榜"、"N 家涨停"、关联 ETF；AI 解读拆成**行业原因 / 公司原因**两部分，各自引用带日期的公告；标题用"A+B+C"格式 |
| **东方财富 F10 核心题材** | 每只成分股都有**入选理由（带出处日期）**和**精准匹配标记**；飙升榜看排名变化；新粉 / 铁粉占比 |
| 韭研公社 / 开盘啦 | 当日异动股**按题材分组**，每只附理由；板块强度 = 加权涨幅 + 涨停数；每日热词云 |
| Finviz Themes | 按结构性主题而不是行业画热力图；主题直接作为筛选条件 |
| Stocktwits | 关注度和自己的历史基线比；带时间戳的"为什么上榜"说明 |
| Deepvue / TradingView Theme Tracker | 多周期的题材排行表，可下钻到龙头；四象限读法本质上就是 RRG |
| Exploding Topics | 生命周期用语：Regular / Exploding / Peaked |
| MSCI、Kensho、Bigdata.com、AlphaSense | 成员分 core / non-core 层级；种子词扩展；LLM 生成题材思维导图后逐公司打暴露度；公司 × 问题的表格，每格附引用 |

**可以马上落地的一个点**：给现有的异动扫描（`services/anomaly_scans.py`）加题材标签，再按题材分组展示，就是美股版的"异动解析"。没法归入任何题材的异动股越聚越多，本身就是新题材的报警信号。

---

## 5. 推荐方案

### 5.1 总体架构

```
数据层   EODHD 价格（已有）| EODHD news 与 sentiments count | 专题 ETF 持仓（发行商日文件、EODHD、N-PORT）
        | SEC 8-K EX-99 与 EDGAR 全文检索 |（可选）Wikipedia、HN、ApeWisdom 快照
题材库   YAML 种子（Git 维护）→ 成分股：召回（ETF、关键词、种子）→ DeepSeek 核验（原文引用）
        → 分层 core/peripheral，按 valid_from/to 记录区间
打分层   P 价格、B 宽度、V 量能、（D 扩散）、A 关注度 → Heat，加 5 日和 20 日两条 EWMA
        → 生命周期：Emerging / Accelerating / Leading / Crowded / Fading
发现层   v1：未归入题材的异动股聚类 → LLM 命名 → 人工审核
        v2：8-K 和新闻里的新 n-gram（FDR 校正）、残差共振社区、BERTopic 合并；至少 2 个证据族一致才成为候选
输出层   theme_trends 发布（质量门）| /themes 页面（仅站长可见）| 飞书早报段落和事件卡 | 带 [E#] 引用的题材简报
```

能复用的现有代码（详见方案 A 第 6–9 节）：
- `market_breadth.build_price_feature_frame`：算题材宽度；
- `analyzer.calculate_rrg`：画题材 RRG；
- `similar_stocks`：相关性聚类；
- `factor_engine._winsorized_zscore`：截面标准化；
- `pipeline_runs` / `data_publications`：质量门；
- `raw_store`：原始数据留痕；
- `ai_assistant.validate_evidence_citations`：引用校验；
- `anomaly_scans`：租约式单飞作业；
- `notifications`：飞书推送。

### 5.2 MVP 打分公式（方案 A）

```
篮子     等权为主，单只上限 15%；x_k = ln(I_T,d / I_T,d−k) − ln(SPY_d / SPY_d−k)，k ∈ {5, 21, 63, 126}
P        0.5·z(x21) + 0.3·z(x63) + 0.2·z(x5)
B        0.4·z(B50) + 0.3·z(NH) + 0.3·z(ΔB50_10d)；IGN = 当日 ≥+5% 且 RVOL≥2 的成员数
V        z( 成员 ln(mean vol 5d / median vol 60d) 的加权中位数 )
A        EODHD 每只股票 7 日文章数 → 相对自身基线的稳健 z → 扣掉全市场中位数 → 聚合到题材，取均值和宽度
H_raw    0.40·P + 0.30·B + 0.15·V + 0.15·A      （z 是在题材和行业篮子之间做的截面标准化）
Heat     100 / (1 + e^(−H_raw))；S5、S20 分别是半衰期 5 日、20 日的 EWMA
萌芽      自身 252 日时序 z ≥ 2，且 21 日前截面分位 < 0.5，且 IGN5 ≥ 3，且 ΔB50 > 0.10，且 x21 > 0
只有关注   z(ATT) ≥ 2 且 P < 0 → 单独标注，不算萌芽
```

这些权重和阈值都是工程默认值，没有文献依据。上线前要用第 3.5 节的离线评估校准。验收目标是：10 个对照组在回填期内都不触发"萌芽"。

### 5.3 分阶段路线

| 阶段 | 内容 | 工作量 | 增量成本 |
|---|---|---|---|
| **v0：现在就做** | ① ~~跑探测脚本~~：2026-10-10 已完成，结论是全市场 feed 可用。② 开始每天存档专题 ETF 持仓（Roundhill、Global X、KraneShares 的日文件）。③ 写 `themes_v1.yaml`，约 20 个题材，见方案 A §4.1。④（可选）每小时存 ApeWisdom 快照 | 1–3 天 | $0 |
| **v1：精益 MVP**（方案 A） | 新增 11 张表（Alembic 0023）；作业链：ETF 价格 → sentiments → 打分发布 → 简报 → 告警；成分股核验；`/themes` 列表和详情页、热门公司榜；飞书早报"题材热度"段落；EDGAR 8-K 确认门；离线评估脚本 | 约 2 周全职 | DeepSeek 约 $1/月；EODHD 最坏 +1.6 万次/天 |
| **v2：发现器**（方案 B） | 8-K EX-99 从 2016 年起回填（压缩后约 2GB，约 33 小时）；D1 新 n-gram 检测加 BERTopic merge；D2 残差共振社区；合并 / 拆分建议和审核界面；基于 N-PORT 的时点成员历史；事件研究和消融实验；目标是胜过 Rule B | 6–8 人周 | $0–10/月；新增 fastembed、onnxruntime、networkx 等，模型约 70MB |
| v3：可选 | 电话会文字稿（API Ninjas $39–59/月）；X counts 题材关键词（先 $5 实测）；确认资格后接 Alpaca/Benzinga；中文散户关注度快照 | 4–6 人周 | $50–80/月 |

方案 B 对 v1 有更高的要求：上线前先用 EODHD 真实篮子复现试点（G0），再把 8-K 语料并进 v1。两份方案的完整差异见各自文档。建议先按方案 A 上线，同时把方案 B 的评估框架（§8）和告警账本（每条告警冻结时间戳和证据）一起做进去，这样 v2 才有数据可比。

### 5.4 明确不做

- 自建 Reddit、X、Stocktwits 采集；
- 盘中实时热度；
- 把热度当交易信号或 alpha 宣传；
- 不经审核自动上线新题材；
- 在服务器上跑 torch 系模型；
- 知识图谱或 GraphRAG；
- 微调 embedding；
- 付费黑盒数据（Adanos、Finnhub 情绪、Marketaux、Unusual Whales、AlphaSense、Bigdata.com）。

---

## 6. 决策记录（2026-10-11）

1. **可见性**：站点只给站长自己用，不打算对外展示。新功能仍然放在 admin key 后面，现有页面不另做处理。
2. **EODHD 探测**：2026-10-10 已完成，全市场 feed 可用，每天约 10 次调用。
3. **Finviz Elite**：不买。分类以专题 ETF 持仓和人工种子为主，Finviz 公开站只作命名参考。
4. **v2 是否引入轻量 ML 依赖**（fastembed、onnxruntime，约 70MB）：待 v2 时再定。
5. **网络出口（NO_PROXY）**：MVP 不用 Wikimedia。SEC 目前经 WARP 访问正常（edgartools 已在用），所以暂不改 `NO_PROXY`。如果日志里出现 SEC 或发行商站点返回 403/429，再把对应域名加进去。
6. **User-Agent 联系方式**：生产环境的 `.env` 已配置 `SEC_USER_AGENT`，直接复用。

---

## 7. 调研过程说明

- **核查方式**：每个候选都由独立的核查 agent 对照一手来源复核，结论分为已核实、有更正、已证伪、无法核实。调研后期网络搜索额度用尽，部分条目改为直接抓取一手页面核实；没有核实的都在附录中注明。
- **隐私**：试点里有 1 次早期测试请求把你的邮箱带进了发往 wikimedia.org 的 User-Agent。此后所有请求都改用不含个人信息的 UA，入库的代码里也没有邮箱。
- **条款**：试点 agent 在核对 Nasdaq 条款之前，向 api.nasdaq.com 发了约 75 次请求。后来发现该站条款禁止自动采集，这部分数据已全部丢弃，没有用于任何结论；对应的采集脚本也没有入库。
- **其他**：没有修改任何业务代码，没有访问任何公司内部系统，没有使用任何凭据。本目录是唯一的新增内容。
