# QuantDashboard「趋势题材与公司」设计提案：早期发现与信号质量优先

> 本文的仓库路径都相对于仓库根目录（只读检查，HEAD 06e9bf5）。
> Pilot 的代码在同目录 [`pilot/code/`](pilot/code/)（包括 `analyze.py`、`eodhd_join.py`），结果表在 [`pilot/results/`](pilot/results/)。总览与取舍见 [README.md](README.md)。

---

## 0. 结论

1. **没有能直接用的开源方案，需要自建。**
   - 美股题材检测在开源里只有一个像样的设计：`tradermonty/claude-trading-skills` 里的 theme-detector（MIT 许可）。它只有 15 个固定题材，粒度到 Finviz 行业级，分不出 SMR、量子、GLP-1、人形机器人这类窄题材。
   - 社交热股方面的开源项目大多是 2021 年前后基于 PRAW 的脚本。Reddit 公开 API 将于 2027-03 终止（未注册应用从 2027-01-12 起被移除）。
   - 可以借鉴的部件有四个：theme-detector 的 heat/lifecycle 打分、BERTrend 的 popularity 衰减算法（只照抄，不安装它的包）、MSCI Relevance Score 的成员打分配方、同花顺热榜的证据展示方式。
2. **pilot 最重要的发现：免费注意力数据只能用来提名候选题材，不能用来判定。**
   - Wikipedia pageviews 和 HN 在 10 个"没成气候的伪题材"对照组上全部触发了 z>3。
   - 它们"首次 z>3 早于价格突破"的比例不比随机高。
   - EDGAR 8-K 全文计数能区分真题材和一时热点：持续 z>3 在真题材里是 10/14，在对照组里是 0/10。用新申报公司（new filer）计数时对照组也是 0/10。但它滞后，相对首个催化剂通常晚约 12 周。
   - 所以本设计的核心是：**多个证据族各自独立打分，至少两族同时成立才把题材升级为 emerging**。
3. **定一个必须击败的 baseline：pilot 里的 Rule B**（注意力提名加 EDGAR 确认）。
   - 14 个真题材里命中 14 个，10 个对照里误报 1 个（Web3）。
   - 有 6/11 个题材早于首只专属 ETF 上市，中位提前 12 周。
   - 有 4/7 个题材早于价格突破，中位提前 3 周。
   - 后续每个新探测器都要在同一套 harness 上胜过它，才允许进入线上打分。
4. **"早"的现实目标**：
   - 稳定地早于首只专属 ETF 上市。ETF 上市本身就是晚期信号，Ben-David 等 2023 年的研究发现专属 ETF 上市后 5 年风险调整后亏约 30%。
   - 与价格突破同步，或提前 0–4 周。
   - 不承诺提前价格数月，n=14 的样本不支持这种承诺。
5. **数据骨干**：
   - 语料：SEC 8-K EX-99 新闻稿。免费、合法、可回填到 2016 年，约 28 万篇、约 5GB 文本。再加上已经付费的 EODHD news 和 `/api/sentiments` count。
   - 注意力：Wikipedia（可回填）、HN（只用于科技题材）、ApeWisdom（今天就开始存快照）。
6. **付费建议**：
   - v1 一分钱都不用花。
   - 如果只买一样，选 Finviz Elite，$299.50/年，用作 taxonomy 种子和每周快照对比。前提是先读它的使用条款（ToU），这一点没有验证。
   - X counts 先花 $5 实测再决定。
   - 不买 Adanos、Finnhub、Marketaux、Quiver、Unusual Whales。
7. **成本和工作量**：
   - v1 每月增量约 $0–25，其中 DeepSeek 不到 $10。
   - v0+v1 约 6.5–7.5 人周，v2 约 6–8 人周（全职当量；业余时间开发的话，日历时间约乘以 3）。

---

## 1. 先把"早"定义成可证伪的指标

对每个历史题材 episode 记录五个时钟：

| 时钟 | 定义 |
|---|---|
| `T_cat` | 首个关键催化剂日期，比如 Microsoft 和 Constellation 的 TMI PPA 是 2024-09-20 |
| `T_bo` | 篮子价格突破：周收盘高于前 52 周最高点，并且 13 周收益超过 20% |
| `T_etf` | 首只美国专属 ETF 上市日 |
| `T_peak` | 篮子价格顶点 |
| `T_wiki` | Wikipedia 条目创建日，或 pageview 峰值日 |

系统输出两个时刻：`T_emerge`（状态进入 emerging 的日期）和 `T_confirm`（进入 trending 的日期）。

**各阶段验收线**（第 8 节有完整定义）：
- **v1**：至少 70% 的 episode 在 `T_etf` 之前进入 emerging，中位提前 8 周以上。对照组误报不超过 2/10。全市场每周新增 emerging 告警不超过 3 条。
- **v2**：在 `T_bo` 上胜过 Rule B，即命中率至少 70%、中位提前至少 3 周，同时对照组误报不超过 1/10。另外，不依赖人工词典的发现器（D1/D2）要在 `T_etf` 之前把至少 50% 的 episode 列为候选。

**pilot 的局限**：价格参照用的是 VanEck 和 SSGA 的代理 ETF（比如 memory 题材用 SMH），只有 7 个有效突破，短语和时间窗都是事后选的。第一步要用 `code/eodhd_join.py` 换成真实 ETF 和等权篮子，再把样本扩到约 30 个题材和 30 个对照。

---

## 2. 总体架构

```
L0 语料与快照（时点正确）  SEC 8-K EX-99 | EODHD news + sentiments count | Wikipedia/HN/EDGAR FTS | ApeWisdom 快照 | ETF 持仓（发行商日文件 + N-PORT 季度）
L1 实体解析/消歧          发行人 CIK→ticker | 别名 Aho-Corasick | 交易所标注正则 | 停用词表 | DeepSeek 只看 top-N
L2 题材库与成员暴露度      人工种子 taxonomy + 发现候选 → theme_members（tier、证据原文引用、valid_from/to）
L3 发现器                 D1 文本新词涌现 | D2 残差共振社区 | D3 异动股题材归类 + unmapped 桶 | 注意力提名
L4 热度/拥挤/生命周期      P 价格 | B 广度 | V 量能 | D 扩散 | A 注意力 | 拥挤与晚期 → 状态机
L5 输出                   theme_trends 发布 | API | /market/themes 页面 | 飞书周报和状态告警 | 冻结的告警账本（alert ledger）
```

这些层大多能复用现有代码：

| 需求 | 复用 |
|---|---|
| 主题广度 | `services/market_breadth.py:build_price_feature_frame`（把指数成分换成题材成员） |
| 主题轮动 | `services/analyzer.py:calculate_rrg`（输入换成篮子价格序列） |
| 篮子扩展、D2 | `services/similar_stocks.py:rank_matches` |
| 截面标准化 | `services/quant/factor_engine.py:_winsorized_zscore` |
| 个股级 IC 检验 | `services/quant/research.py:evaluate_factor` |
| 发布质量门 | `services/pipeline_runs.py:publish_datasets_and_finish` |
| 原始数据留痕 | `services/raw_store.py:persist_snapshot` |
| 引用校验 | `services/ai_assistant.py` 中的 `validate_evidence_citations` 和 `build_evidence_hash` |
| 异动股 | `services/anomaly_scans.py` |
| 飞书推送 | `services/notifications/` |

---

## 3. 数据源取舍

| 来源 | 角色 | 结论 | 成本 | 可回填深度 | 验证状态与注意点 |
|---|---|---|---|---|---|
| **SEC 8-K EX-99**（edgartools `EightK.press_releases`，每日 `form.idx` 加 Item 9.01 过滤） | 主语料、公司到题材的证据、new filer 信号 | **必做**。v1 增量，v2 回填 | 免费 | 2016 起（EDGAR 本身从 2001 起） | 已确认。R3000 公司每天约 110–125 份 8-K，约 65% 附 EX-99。2016–2026 约 28 万篇、约 5GB 文本、约 60 万次请求，按 5 req/s 约 33 小时。产品和合作类新闻稿很多不会进 8-K |
| **EDGAR FTS**（efts.sec.gov） | 题材短语的 8-K 计数和 new filer | **v1 必做** | 免费 | 2001 起 | **已更正**：命中单位是文档而不是 filing，要按 `adsh` 去重；单次查询上限 10k；`entity_filter` 只返回前 30 个公司；接口没有官方文档 |
| **EODHD `/api/news`** | 新闻语料 | v1 做，但先用 owner 的 key 跑 cost probe | 已付费 | 满量数据从约 2021-03 开始；tags 约从 2025 年中开始 | **已更正，仍有未决项**：通用 feed 是否存在**未定**；不支持多 ticker 查询；`symbols[]` 按字母排序且最多 50 个、只有 35% 是 `.US`；93–96% 来自 Yahoo，与现有 RSS 高度重叠 |
| **EODHD `/api/sentiments` count** | 每个 ticker 的新闻注意力序列 | **v1 必做** | 计费口径未定：每 ticker 5，或 5+5n | 2012 起（AAPL） | count 和新闻篇数一致已用 demo key 验证；最多 100 ticker/请求只出现在 MCP 文档里 |
| **Wikipedia pageviews API** | 题材和公司注意力，可回填 | v1 做，作为低权重提名 | 免费 | 2015-07 起 | **已更正**：2026 新限速是 compliant UA 200/min；2025 年 3–8 月有 bot 流量重分类，人类流量同比约 −8%，需要归一化 |
| **HN Algolia** | 科技题材提名 | v1，只用于科技题材 | 免费 | 2006 起 | 已确认，10k/h/IP。nbHits 是估算值，计数要翻页取全 |
| **ApeWisdom** | 散户 ticker 注意力 | **v0 当天开始每小时存快照** | 免费 | 无，必须自己存 | 已确认。噪声大（IT、YOU、ALL 等常见词误识别）；2027 年 Reddit 断供风险 |
| **发行商 ETF 持仓日文件 + N-PORT** | 成员 ground truth、种子、ETF 上市事件 | **v0 开始存档** | 免费 | Roundhill 约 2023-12；KraneShares 约 2024-01；Global X 2024-08-06；Tema 2026-08-18；N-PORT 季度数据 2019Q4 起 | 已确认。许可限制：Global X 禁止衍生作品，iShares 只允许个人非商业使用。N-PORT 目前只公开季末月份，月度公开推迟到 2027-11-17 以后 |
| **EODHD `ETF_Data.Holdings`** | 当前持仓 | v1 每周拉 | 10 calls/ETF | 无历史 | 只有当前数据，已确认；小型主题 ETF 的覆盖度未验证；不能走 bulk 接口 |
| **Finviz Elite** | 40 个题材 / 268 个子题材的种子和每周快照 | v1 可选购买 | $299.50/年 | 无历史 | taxonomy 已确认；**ToU 未验证**；API 细节来自前端 JS bundle；每次全量抓取约 308 次请求 |
| **X counts** | 题材关键词量 | v3，先用 $5 测试 | $0.005–0.010/请求 | 2006 起 | **counts 端点能否用 `$` cashtag，文档互相矛盾，未验证**；Basic/Pro 能否新购未确认；只查关键词，不做全市场 cashtag |
| **Alpaca/Benzinga** | 独立的新闻历史（2015 起） | v2，有条件 | 可能免费 | 2015 起 | **未验证**：非美国居民资格、Basic 套餐是否含新闻；条款禁止公开展示 |
| **财报电话会文字稿** | 暴露度的最强证据 | v3 | API Ninjas Developer $39/月（年付）或 $59/月 | 近 5 年 | **已更正**：文字稿是 Premium-only，免费层拿不到；defeatbeta 免费数据集来源不清 |
| 东方财富美股人气榜、同花顺 usa 热榜 | 中文散户注意力 | v3，可选快照 | 免费 | 约 120 天 | **已更正**：历史只有约 120 天，不是 1 年；非官方接口 |

**明确不用的来源**：
- **Stocktwits 轮询**：ToS 第 5 条禁止自动抓取，开发者注册已关闭。
- **Reddit API / PRAW、twscrape**：Reddit 公开 API 即将关闭；X 抓取违反 ToS。
- **Google Trends**：官方 API 仍是 alpha，pytrends 已归档。
- **GDELT**：**已更正，可信度低**。DOC API 持续返回 429，2026 年 10 月数据明显降级。
- **Quiver**：WSB 数据停在 2025-02-21。
- **Tradestie**：**已更正**。sentiment 字段是冻结的，只有评论数可用。
- **AltIndex**：社交数据集标为 Delayed。
- **Adanos、Finnhub、Marketaux、Unusual Whales**：都是黑盒或价格偏贵。

---

## 4. 实体解析与 ticker 消歧

按文档类型用不同策略，规则优先，LLM 只做兜底：

1. **8-K EX-99**：
   - 发行人直接由申报公司 CIK 确定，映射用 `company_tickers_exchange.json` 加 security_master，最可靠。
   - 文中提到的其他公司，先用 `(NASDAQ: XXX)`、`(NYSE: XXX)` 这类交易所标注正则，这是精度最高的模式。
   - 再用 pyahocorasick 别名自动机匹配：`tickers.name` 和 SEC 公司名，用 rapidfuzz 去掉 Inc./Corp./Holdings 等后缀。
   - 加人工产品别名表，比如 Wegovy/Ozempic→NVO，Zepbound/Mounjaro→LLY，TSMC→TSM。
2. **EODHD news**：
   - `symbols[]` 只保留 `.US`。HK/TW/EU 的核心标的按 ISIN 映射回来。
   - 按出现位置加权：标题命中 1.0，正文命中 0.5，只出现在 `symbols[]` 里 0.2。
   - `symbols[]` 达到 50 个上限的文章，只有标题或正文命中才算。probe 数据显示标题提及率 25–40%，正文约 87%。
3. **社交数据（ApeWisdom）**：
   - 只接受 R3000+NDX universe 内的 ticker。
   - 停用词表 = asad70 的 114 词黑名单 + 所有 1–2 个字母的 ticker + 同时是英文常用词的 ticker（AI、IT、ALL、ON、NOW、YOU、GO、OR、SO、EU 等）。
   - ApeWisdom 不返回是否带 `$`，所以停用词表里的 ticker 权重记为 0，除非其他来源能印证。
   - 剔除 SPY、QQQ 等宽基 ETF。
4. **LLM 兜底**：只对当天要展示的前 50 个候选调用 DeepSeek，返回 JSON `{ticker, is_company_reference, confidence, quote}`。
5. **标注评估集**：400 条分层样本（8-K 100、news 150、ApeWisdom 150）。目标：展示出来的 ticker 精度 ≥0.95，召回 ≥0.8。stock-recognizer 的 F1 0.80 是作者自报，没有第三方复核，而且它强依赖 torch，最多放到 v3 作为备选。

---

## 5. 公司到题材的暴露度打分

先用 ETF 持有者 ∪ embedding 相似度前 200 ∪ 词典命中 >0 召回候选，平均每家公司约 5 个候选题材。每对（公司，题材）计算五类特征：

| 特征 | 计算方式 | 依据 |
|---|---|---|
| `f_etf` | 该时点各专属 ETF 中的最大权重，以及持有它的 ETF 数量 | THEME 论文（CIKM 2025）用 969 只主题 ETF 作监督信号 |
| `f_emb` | 公司画像与题材定义的余弦相似度，在题材内做 z-score。公司画像是 `tickers.description` 加 10-K Item 1 的分块 max-pool；题材定义是定义文本、10–30 个关键词和种子成员的中心向量。模型用 fastembed `BAAI/bge-small-en-v1.5`（ONNX，67MB，不依赖 torch），备选 model2vec `potion-retrieval-32M` | Vamvourellis 2023；DeepSeek 没有 embeddings 接口 |
| `f_lex` | 近 4 个季度该公司 EX-99、8-K、10-Q MD&A 里题材词出现的句子占比，同时记录首次提及日期 | Sautner 等 JF 2023 |
| `f_llm` | DeepSeek 给出等级 0–3、角色（pure-play / supplier / adopter / tangential）、收入占比档位和**原文引用**。引用在源文中找不到就丢弃。关闭 thinking，用 JSON mode，返回空内容时重试 | arXiv 2507.20957 的偏差证据；DeepSeek 文档提到 JSON 可能返回空 |
| `f_ret` | 叙事 beta：个股周度残差收益对题材篮子残差（留一法，排除自身）的 52 周滚动 beta | Lee-Lou-Ozik-Sadka 的思路 |

**合成方式**：
- `exposure ∈ [0,1]` 由逻辑回归给出，用 numpy 或 scikit-learn。标签是"权重 ≥1% 的专属 ETF 成员"，按题材做留一法交叉验证。
- 分档：core（≥0.7 且 `f_llm≥2`），member（≥0.4），peripheral（≥0.2，标灰显示）。
- 篮子权重：exposure × √mcap，单股上限 10%。同时保留一个等权版本。

**不能只靠 LLM 建篮子**：THEME 论文里 GPT-4.1 的 P@3 只有 0.519。另外 arXiv 2609.31013 发现 7 个 LLM 之间的排名相关性只有 0.52。处理方式是固定模型版本、缓存结果，再用第二个模型对 100 对样本做一次校准抽检，不做多模型集成。

**时点正确（PIT）要求**：
- 每条成员记录有 `valid_from = max(证据的 available_at, 运行日)`。
- 历史回填只允许用当时已提交的 10-K/8-K 计算 `f_etf`、`f_emb`、`f_lex`、`f_ret`，标记为 `method='pit_backfill'`。
- `f_llm` 只从上线日开始往后积累，标记为 `method='llm_forward'`。
- `tickers.description` 没有历史版本，不能用于回测。

**成本**：全量 3,000 家公司约 1.5 万次调用，用 flash 模型加缓存前缀约 $3–7。按季度全量刷新，有新申报时增量刷新。

---

## 6. 动态题材发现

### D1 文本新词涌现：主力早期探测器
- **语料**：EX-99 与 EODHD news。先按链接去重，再用 MinHash（datasketch）去掉标题 Jaccard ≥0.8 的近似重复。
- **预处理**：用正则切掉 "Forward-Looking Statements" 段落和风险因素等固定套话。实体名和 ticker 用第 4 节的别名词典剔除。
- **候选短语**：scikit-learn `CountVectorizer(ngram_range=(1,3))` 加自定义停用词。
- **每个短语 p 每周 w 的统计量**：
  - `df_cik`：使用 p 的不同公司数；
  - `new_cik`：2016 年以来第一次使用 p 的公司数；
  - `share`：含 p 的文档数 ÷ 当周文档总数；
  - 104 周背景期的 median/MAD；
  - Poisson 尾概率加 Benjamini-Hochberg FDR 校正，q<0.01。
- **触发条件**：q<0.01，且 `df_cik≥3`，且同时出现在 8-K 和 news 两类来源，且 3 周里至少 2 周满足。Kleinberg burst 作为辅助信号，需要自己重写约 60 行。`burst_detection` 包标注"仅非商业"且没有 license 文件，**已更正**。
- **聚合成候选题材**：用文档共现图加 embedding 相似度做连通分量。DeepSeek 给候选起名，必须引用 `[E#]` 证据。
- **v2 增强**：每周对没匹配到已知题材的剩余文档跑一个 BERTopic 模型（`zeroshot_topic_list`，相似度阈值 0.7，后端用 fastembed），再 `merge_models(min_similarity=0.7)` 并入主模型，没被合并的 topic 就是新候选。在此基础上加 BERTrend 的 popularity：`p_t = p_{t-1}·e^{-λΔt²}`，λ=0.01，落在 P10–P50 之间且斜率为正的算弱信号。
  - **已更正**：`partial_fit` 会失去 HDBSCAN 的离群和新颖性检测，所以用"每周一个模型 + merge"的方式。
  - 不安装 `bertrend` 包：它锁定了 sentence-transformers <5.4，还依赖 RabbitMQ 和 nicegui。

### D2 残差共振社区：交易层面的叙事
- **样本**：按 60 日成交额中位数取前约 1,200 只。
- **残差收益**：对 SPY 和所属行业 ETF 回归后的日收益，β 用 250 日窗口。11 只 SPDR 行业 ETF 已经存在 RRG 快照里。
- **建图**：120 日残差相关，每只股票保留 15 个正相关最强的邻居，形成稀疏 kNN 图。这样可以绕开 T<N 带来的协方差估计问题。RMT（Marchenko-Pastur）过滤放到 v2 再做。
- **社区检测**：networkx 3.7 的 `leiden_communities`，**必须显式设 `metric='modularity'`**，默认值是 `cpm`（已更正）。跑 20 个随机种子，共同归属率 ≥0.8 的部分作为稳定核心。
- **跨月匹配**：按月滚动，相邻窗口的社区用 Jaccard ≥0.5 匹配。
- **触发条件**：
  - 稳定核心至少 5 只；
  - 至少持续 3 个窗口；
  - 与任何 EODHD 行业或 GICS 子行业的最大 Jaccard <0.5（GICS 需要从 raw fundamentals 解析，字段是否存在未验证）；
  - 跨至少 2 个行业板块；
  - 组内平均残差相关在 3 个窗口里上升超过 0.05；
  - 篮子相对强度 > 0。
- **预期**：与价格突破同步或略早，正好补上 D1 偏晚的问题。

### D3 异动股题材归类：美股版的"涨停原因"
- 在现有异动归因里加结构化输出：题材 id 列表，加 `unmapped:<短语>`。
- unmapped 短语用 embedding 聚类，15 个交易日内有至少 4 只不同异动股落进同一类，就生成候选。
- 已映射题材每天的异动股数量本身就是一个广度信号。
- **局限**：当前异动扫描只覆盖市值前 1,000、单日波动 ≥4%、每次最多 20 只，而题材往往从小盘股开始。v2 改成基于 EOD 数据，在 R3000 内扫描单日 ≥8% 且 RVOL≥2 的股票，再配上 news 和 8-K 证据。

### 候选融合与人工审核
- D1、D2、D3、注意力提名（任一注意力源持续 z>3）这 4 个证据族中，**至少 2 个在 6 周内指向至少 3 只共同成员**，候选进入"emerging（待审）"。
- owner 一键处理：批准、并入已有题材、拒绝、暂缓。人工审核成本很低，但必不可少。

---

## 7. 热度、拥挤度与生命周期

**每日五个分项**。每项都做两种 z：相对所有题材和 GICS 行业的截面 z（±3 截尾），以及相对自身 252 日历史的时序 z。

| 分项 | 内容 |
|---|---|
| P 价格 | 残差 R21/R63，并对 lfq-v1 各因子多空收益回归去掉因子暴露（Ehsani-Linnainmaa JF 2022 发现因子动量包含行业动量）；SMA21/SMA200；52 周新高接近度，市值加权和等权各一版 |
| B 广度 | 成员站上 MA50 的比例；该比例 10 日变化；距 52 周高点 2% 以内的成员比例 |
| V 量能 | 成员 ln(DV5/median DV60) 的中位数；RVOL>2 的成员比例 |
| D 扩散 | 题材词典的 EDGAR new filer 数（4 周）；首次在 8-K/10-Q 提及的公司数；近 90 天新增的 LLM 核验成员数 |
| A 注意力 | EODHD 篇数 share-of-voice 的 z；Wikipedia ASVI，算法为 ln(PV_w) 减前 8 周中位数的 ln；HN（只用于科技题材）；ApeWisdom 成员提及的 z |

**合成**：`Heat = 0.35P + 0.25B + 0.15V + 0.15D + 0.10A`。注意力权重从文献默认的 0.2 降到 0.1，依据是 pilot 结果。再做两条 EWMA：半衰期 5 天（当前热度）和 20 天（趋势）。总共不超过 5 个自由参数，防止在 14 个 episode 上过拟合。

**hype 标记**：A 的 z>3 而 P 和 B 都 ≤0 时，显示为"注意力先行，未确认"，不计入热度。依据是 Barber 2022 和 Cookson 2024 的结论：只有注意力、没有价格配合时，后续收益会反转。

**状态机**（每次转换写入 `theme_events`）：

| 转换 | 条件 |
|---|---|
| candidate → emerging | 满足融合规则并经人工批准；至少 3 个证据族同时成立时自动转换 |
| emerging → trending | 最近 20 个交易日里至少 10 天 Heat 截面百分位 ≥80，且 B50 ≥60%，且 D 的 z>2 持续。D 这一条就是 pilot 里的 EDGAR 判别器 |
| trending → crowded/late | 以下至少 3 条成立：CoTheme 百分位 ≥80（成员 52 周残差相关的均值，Lou-Polk comomentum 的题材版本，**未经检验**）；加速度 R(t−24m→t) − R(t−24m→t−12m) > 0，且 2 年相对市场涨超 100%（Greenwood-Shleifer-You 2019：这种情况崩盘概率约 53%）；增发占比上升；近 12 个月有新的专属 ETF 上市；A 处于极值而 V 在回落 |
| 任意状态 → fading | 连续 20 天 Heat 百分位 <40，且 popularity 衰减到自身历史 P10 以下 |
| fading → dormant | 持续 63 个交易日；再次满足 emerging 条件就复活，沿用原 theme_id |

**合并**：核心成员 Jaccard ≥0.6，或者"定义 embedding 余弦 ≥0.9 且两个篮子 120 日残差相关 ≥0.8"，系统提出合并建议，被合并的一方变成别名或子题材。

**分裂**：题材内部成员残差相关图的 Leiden modularity ≥0.3，出现至少 2 个各含 5 只以上成员的社区，并且 63 日收益差距 ≥20pp 持续 4 周，系统提出拆分子题材。例子是 AI 基础设施拆成电力、存储、光通信和 neocloud；Roundhill 在 2026 年先后推出 DRAM、LYTE、NCLD 三只 ETF，正好对应这种分裂。

**层级**：父子两级，类似 Finviz 的 theme / sub-theme，两级都计算热度。

---

## 8. 评估框架

### 8.1 Ground truth
- **`theme_episodes`**：14 个，取自 pilot 的事件目录。cannabis 2018、EV 2020、SPAC 2020、metaverse 2021、GenAI 2023、AI 电力 2024、核电 2024、量子 2024、GLP-1 2023、人形机器人 2025、稳定币 2025、航天 2025、无人机 2025、存储/HBM 2025。每个都带 `T_cat`、`T_etf` 和来源 URL。其中标注"from memory"的日期可信度低。
- **`theme_controls`**：10 个，取自 pilot。NFT、Web3、LK-99、室温超导、2022 年以后的 metaverse、培养肉、垂直农业、核聚变、Vision Pro、Clubhouse。
- **扩样本并预先登记**：用规则选样，而不是事后挑。2017–2026 年间上市一年内 AUM ≥$5000 万的美国主题 ETF，每只定义一个 episode。对照组取 pageview 出现 10 倍以上暴涨但始终没有 ETF 的条目。已清盘的主题 ETF 也要纳入，避免幸存者偏差。目标约 30 个 episode、30 个对照。

### 8.2 指标
1. **领先/滞后**：`T_emerge` 和 `T_confirm` 分别对比 `T_etf`、`T_bo`（EODHD 等权代表股篮子和真实 ETF 各算一次）、`T_cat`、`T_peak`。报告中位数、命中率，以及按基础触发率算出的随机命中率（沿用 pilot 的 `chance_before`）。
2. **误报**：对照组触发率；线上 emerging 告警在 26 周内没能进入 trending 的比例；每周告警数。
3. **成员质量**：
   - 以时点正确的 ETF 持仓为 ground truth：取同一时点所有专属 ETF 中权重 ≥1% 的成员并集，N-PORT 按申报日对齐，近期数据用发行商日文件。计算 P@10、HR@10、Recall@50。
   - "成员领先"：ETF 上市当日的成分股里，有多大比例被系统提前 4 周以上列为成员。
   - ETF 会纳入不少只是沾边的大市值股（比如量子 ETF 里有 AMZN），所以每个题材再人工抽检 30 只。
4. **事件研究**：以 `T_emerge` 为 t0，计算篮子在 +5/+20/+60/+120 日的 CAR，分别对比 SPY 和 100 个按行业、市值、波动分组匹配的 placebo 篮子，标准误按日期聚类。
5. **个股级检验**：`theme_heat_exposure = Σ exposure × heat`，写成 FactorValue，`version='theme-v1'`，带 `available_at`。用 `evaluate_factor` 在严格时点正确的 S&P 500 universe 上算 5/21/63 日 Rank IC、分位收益和换手。门槛是 t>3（Harvey-Liu-Zhu 2016）加 Deflated Sharpe。预期结果较弱，**不作为主要成功标准**。
6. **消歧精度**：在 8.1 之外的 400 条标注集上测。
7. **消融**：每次去掉一个证据族重跑，证明每一族都有贡献，并且整体胜过 Rule B。

### 8.3 时点正确与偏差控制
- 所有派生记录都带 `available_at`。SEC 文档用受理时间，新闻用发布时间加抓取延迟，回填数据用申报日。
- **事后词典偏差**：只有 D1 和 D2 这类不依赖人工词典的发现器，才能证明"能提前发现新题材"。检验方法是看它们能否在 `T_etf` 之前生成一个与 episode 代表股重合 ≥30% 的候选。已知题材的热度跟踪允许用事后词典，但结果要标注为"已知题材跟踪"，不能当作发现能力。
- **LLM 前视偏差**：DeepSeek 知道哪些题材后来成功了。所以：
  - 历史发现测试中不使用 LLM；
  - 历史标注测试时把公司名、ticker、日期全部匿名化（Glasserman & Lin 2023）；
  - LLM 层只在上线后的**告警账本**上评估：每条告警冻结时间戳、哈希和证据。
- **幸存者偏差**：Russell 的历史成分只有每天观测到的实时成分，不是严格时点数据。episode 要用 `exchange-symbol-list?delisted=1` 补上退市股。严格的统计检验只在 S&P 500 上做。
- **已知数据伪影**：EODHD 2024 年 11–12 月近乎断流，2025-08-12 至 08-21 有大批量误打标签，这两段要屏蔽；EODHD 的 tags 在 2025 年以前的回测里不能用；Wikipedia 2025 年的 bot 重分类用全站 user 总流量归一化。

### 8.4 阶段门
- **G0**：用 EODHD 篮子复现 pilot 的数值。通过之前，不新建除免费源以外的采集器。
- **G1（v1 上线）**：第 1 节的 v1 目标，加上成员 P@10 ≥0.6，消歧精度 ≥0.95。
- **G2（v2）**：胜过 Rule B，并达到第 1 节的发现器目标。
- **线上**：每月复盘告警账本。至少 40% 的 emerging 告警要在 26 周内进入 trending，否则提高门槛。

评估代码放在 `services/themes/evaluation.py` 加 `scripts/theme_eval.py`，离线运行，不进 worker。可以直接移植 `pilot/code/analyze.py` 的 `robust_z`、`breakout`、`chance_before`、`rule_b`。评估报告用 raw_store 留痕。

---

## 9. 数据模型与代码落点

**新增表**（Alembic `0023` 及之后）：

| 表 | 字段 |
|---|---|
| `corpus_documents` | source, source_doc_id, url_hash, published_at, available_at, issuer_ticker, form_type, title, text_path（存在 raw_store）, near_dup_group, raw_snapshot_id |
| `document_mentions` | doc_id, ticker, method, position_weight, confidence |
| `attention_observations` | source, key_type（ticker/theme/phrase/article）, key, obs_date, value, denom, available_at |
| `social_snapshots` | source, board, ts, ticker, mentions, upvotes, rank, extra_json |
| `etf_holdings_snapshots` | etf, as_of, available_at, holding, cusip, weight, source（issuer/nport/eodhd） |
| `themes` | slug, parent_id, status, definition, lexicon_json, origin（curated/finviz/discovered）, taxonomy_version |
| `theme_members` | exposure, tier, role, features_json, evidence_doc_id, evidence_quote, method, model_version, valid_from, valid_to, available_at |
| `theme_candidates` | detector, evidence_json, proposed_label, proposed_members, status |
| `theme_daily_metrics` | heat 及各分项, cotheme_pct, accel, state, run_id |
| `theme_events` | 生命周期事件 |
| `theme_alerts` | 告警去重，参照 `RsiAlert` |
| `theme_episodes`、`theme_controls`、`alert_ledger` | ground truth 与冻结告警 |

**新增模块**：`services/themes/` 下的 `registry`、`corpus_sec`、`corpus_news`、`resolver`、`attention`、`exposure`、`discovery_text`、`discovery_comove`、`mover_tagging`、`scoring`、`lifecycle`、`evaluation`。

**调度**（`core/scheduler.py`，America/New_York 时区）：

| 时间 | 任务 |
|---|---|
| 每小时第 :07 分 | ApeWisdom 快照（all-stocks 第 1–9 页）。v0 |
| 周二至周六 04:45 | theme_trends 计算，放在 04:00 因子和 04:30 RSI 之后，用 `latest_published_date` 守卫 |
| 周二至周六 05:00 | ETF 发行商持仓文件快照 |
| 周二至周六 05:15 | 前一日 8-K EX-99 增量抓取，≤5 req/s，设置 `EDGAR_RATE_LIMIT_PER_SEC=5` 和 `SEC_USER_AGENT` |
| 每天 06:00 | Wikipedia（约 3,500 个条目，约 18 分钟）和 HN 计数 |
| 周日 04:00 | D1/D2 发现器、EDGAR FTS 词典计数、BERTopic（v2）。避开 03:00 的备份 |
| 每季度 | N-PORT 抓取、暴露度全量刷新 |

`services/catchup.py` 里同步加上 theme_trends 的补跑逻辑。

**网络出口**：SEC 和 Wikimedia 都要求带联系方式的 UA。生产环境经过 WARP 的共享出口 IP 可能被连坐封禁，是否把 sec.gov 和 wikimedia.org 加进 `NO_PROXY` 需要 owner 决定。

**API**：
- `GET /api/v1/themes`、`GET /api/v1/themes/{id}`：只读本地缓存，页面访问时不调用 AI。
- `GET /api/v1/themes/candidates` 与 `POST /api/v1/themes/candidates/{id}/review`：需要 `require_configured_admin_api_key`。

**前端**：
- 在 `/market/themes` 增加一个 MarketTabs 标签。
- 内容：题材排行和热力图（ECharts，颜色走 chartTheme）、题材 RRG、生命周期时间线、成员表（tier、证据原文、sparkline）、候选审核队列。UI 文案用英文。

**飞书**：中文周报，加上状态变成 emerging 或 crowded 时的即时告警，带去重。

**依赖**：
- 新增 scikit-learn、fastembed（含 onnxruntime）、pyahocorasick、rapidfuzz、datasketch、networkx。
- v2 加 BERTopic 轻量模式，不引入 torch。
- 模型约 70MB，整体在服务器磁盘限制以内。

**许可约束**：EODHD 个人版 ToS 禁止向他人展示数据（包括加工后的形式），Alpaca 禁止公开展示，Global X 禁止衍生作品。因此站点必须只对 owner 可见。

---

## 10. 分阶段路线图

| 阶段 | 内容 | 工作量 | 增量成本 |
|---|---|---|---|
| **v0**（第 1–2 周） | **立刻开始存档**：ApeWisdom 每小时、ETF 发行商日文件、EODHD `ETF_Data` 每周。回填种子题材的 Wikipedia、HN、EDGAR FTS 数据。用 owner 的 key 跑 `eodhd_cost_probe.py`，确认通用 feed 和计费口径，这决定语料路线。建立 14+10 的 ground truth，跑 `eodhd_join.py` 替换代理 ETF（G0）。准备约 40 个题材的 taxonomy v0：Finviz 风格，加上 owner 关心的 AI 算力/电力/HBM/光模块/neocloud、GLP-1、核电/SMR、铀、量子、人形机器人、稳定币、航天、无人机等 | 约 1.5 人周 | $0 |
| **v1**（第 3–8 周） | 8-K EX-99 增量抓取加 1 年回填；EODHD news（通用 feed 可用时约 30 calls/天，否则分层逐 ticker 约 6.5k/天）和 sentiments count；去重；消歧器和标注集；暴露度流水线（ETF、embedding、词典、DeepSeek 核验）；P/B/V/D/A 热度、状态机、Rule B emergence；D3 异动股归类；theme_trends 发布、API、页面、飞书周报。通过 G1 | 约 5–6 人周 | DeepSeek 不到 $10/月；Finviz $299.50/年可选；EODHD 已付费，日用量预计 23–33k/100k |
| **v2**（第 9–16 周） | 8-K 从 2016 年起全量回填（约 60 万次请求、约 33 小时、压缩后约 2GB）；D1 新词检测（FDR、new filer、BERTopic + merge_models + BERTrend 衰减）；D2 残差共振社区；拥挤和晚期指标；合并/拆分建议与审核 UI；基于 N-PORT 的时点成员历史；事件研究、IC、消融。通过 G2 | 约 6–8 人周 | 约 $0–10/月 |
| **v3**（可选） | 电话会文字稿（API Ninjas $39–59/月）用于暴露度和扩散；X counts 题材关键词（先 $5 测试，50 个查询回填约 $64，之后约 $8/月）；资格确认后回填 Alpaca/Benzinga；中文散户注意力快照；RMT 过滤；题材内部领先-滞后结构；规则消歧不够用时再考虑 GLiNER2 | 约 4–6 人周 | $50–80/月 |

---

## 11. 对个人项目属于过度工程的部分

1. **微调 embedding**，比如 THEME 那种在 7B 模型上做 LoRA 对比学习。零样本 GPT-4.1 的 HR@3 已有 0.71，CPU 小模型召回加 LLM 核验就够了。
2. **知识图谱**：FinDKG/KGTransformer、GraphRAG/LightRAG。
3. **安装 BERTrend 包或 RabbitMQ**。照抄约 100 行算法即可。
4. **Wikipedia 全量 dump**（2016–2026 约 490GB）。用 API 拉精选条目列表，加 top 端点做发现。
5. **Reddit 全文**（Arctic Shift 约 3.8TB）、自建 Reddit 采集、Stocktwits 轮询、X 帖子正文读取、twscrape。
6. **实时和分钟级**：Bluesky Jetstream、分钟级热度、盘中题材资金流。题材是按天和周演变的。
7. **GDELT 接入**：服务当前降级，频繁 429。
8. **服务器上跑 torch 系模型**：GLiNER2、FinBERT/FinTwitBERT 情绪、Qwen3-Embedding。情绪只是次要信号，DeepSeek 只处理 top-N 就够。
9. **多 LLM 集成打分**。做一次 100 对样本的校准抽检即可。
10. **研究型算法**：Hermitian 领先-滞后聚类、SigniTrend 哈希词对、k-LLMmeans、逐文档跑 TopicGPT。
11. **基于题材热度做交易策略或组合优化，或在大网格上调热度权重**。n=14 只会得到过拟合结果。
12. **付费黑盒数据**：Adanos、Finnhub、Marketaux、Unusual Whales、AlphaSense、Quiver、Bigdata.com。
13. **通用 feed 可用时仍逐 ticker 每天全量拉新闻**。
14. **追求采集器 SLA 或高可用**。做好优雅降级即可。

---

## 12. 依赖来源中验证结论不是 "confirmed" 的项

- **EODHD**：
  - 通用 news feed 是否存在：**未定**。
  - `t=` 查询和 sentiments 的计费口径：**未定**。news 单次 5 calls 由四个官方来源一致确认，MCP 文档写的 10 是矛盾值。
  - sentiments 每次最多 100 ticker：只有 MCP 文档提到。
  - 小盘股覆盖：**未知**。
  - ETF 持仓：只有当前数据。
  - `/api/news` 候选条目：**已更正**。
- **Finviz**：ToU 未验证；Elite 导出的列表未验证；API 细节来自 JS bundle，不是渲染后的文档页。
- **X**：counts 端点是否支持 `$`，文档矛盾；Basic/Pro 能否新购未确认；非美国银行卡能否付款未确认；$20 赠金有条件（**已更正**）。
- **Alpaca**：非美国居民资格、Basic 套餐是否含新闻，都**未验证**；条款禁止公开展示。
- **EDGAR FTS**：**已更正**（文档级命中、10k 上限、只返回前 30 实体）。
- **8-K 体量**：R3000 占比是估算值。
- **Wikipedia**：限速已更新，2025 年 bot 重分类（**已更正**）。
- **GDELT**：数据质量**低可信**（已更正）。
- **ApeWisdom、Arctic Shift**：2027 年以后的可用性**未知**。
- **Tradestie**：sentiment 冻结（已更正）。**Stocktwits**：ToS 禁止抓取（已更正）。**Quiver**：WSB 数据冻结（已确认停更）。
- **工具层**：BERTopic 的 `partial_fit` 限制；networkx leiden 默认 `cpm`；model2vec 的 `distill` 需要 torch；granite 和 Qwen3 不在 fastembed 支持列表里；`burst_detection` 许可问题；TopicGPT 的 `base_url` 只在 main 分支。以上都已更正。
- **文献**：
  - Lee 等的 intensity 是负面情绪占比，不是注意力量（**已更正**），所以用 pageviews 替代属于**未检验的改造**。
  - Moat 2013 的 "141%" 在论文里没找到。
  - Eisfeldt 等的 "0.4%/天" 未确认。
  - Kensho、Bloomberg BI、Morningstar 的方法论**无法验证**。
  - Arnott 因子动量的具体数字未验证，改用 Ehsani-Linnainmaa 2022。
  - 题材级 comomentum、breadth thrust、跳空不回补规则都是**未经文献检验的改造**。
- **pilot 本身**：属于轶事级证据（n=14/7，代理 ETF，事后选取短语）。部分催化剂日期凭记忆填写；ai_power、稳定币、无人机三个题材的"首只 ETF"未核实。