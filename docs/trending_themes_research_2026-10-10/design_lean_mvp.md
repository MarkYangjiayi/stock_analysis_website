# 题材与热门公司追踪：精益版 MVP 方案（基于现有栈，1–2 周）

> 基于仓库 HEAD 06e9bf5，下文路径都相对于仓库根目录。写作时只读了代码，没有改任何文件。各结论用到的研究来源和核验状态列在第 17 节。总览与取舍见同目录 [README.md](README.md)。

---

## 0. 结论摘要

1. **不引入任何开源项目做运行时依赖，全部自建。** 目前没有能端到端完成"题材发现 → 映射到公司 → 热度"的成熟开源项目。可借鉴的只有设计和公式：tradermonty theme-detector 的热度和生命周期设计（MIT），BERTrend 的流行度衰减思路，以及 Akkerman 的平滑动量公式。MVP 只用 `pandas`、`numpy`、`PyYAML`、`httpx` 和现有的 `openai` 客户端，都已在 `requirements.txt` 里，新增依赖为 0。
2. **打分以确定性信号为主，"关注度"权重压低。** 主体是价格/相对强度、篮子内宽度和量能，全部来自已有的 `daily_prices` 和 `stock_screener_snapshot`，不需要新的价格 API 调用。关注度来自 EODHD `/api/sentiments` 的每日文章数（已付费），权重只给 0.15。另外设一个"只有关注、价格未确认"的标志，单独显示，不计入热度。小样本试点（n=14 个题材、10 个对照组）显示：免费关注度序列不比随机更早，且 10/10 的伪热点也会触发。
3. **题材表 = 一个 Git 维护的 YAML 种子文件，加上由程序生成并带证据的成分股。**
   - 起步约 20 个题材，以专题 ETF 为锚（DRAM、LYTE、NCLD、HUMN、OZEM、NUKZ 等）。
   - 成分股借鉴同花顺/东方财富"概念板块"的数据模型：每只成分股都有入选理由、来源、日期、精准/外围分层、龙头标记。
   - 候选股来自 ETF 持仓、业务描述关键词和人工种子三路合并。然后 DeepSeek 逐只核验，必须给出业务描述里逐字可查的原文引用，否则不收。
4. **LLM 只做三件事：** 核验成分股、给新聚类起名、写带 [E#] 引用的"为什么在动"简报。都在 worker 里批量跑，浏览页面时不调用任何 LLM。月成本约 $1。
5. **EODHD 新增调用最坏约 1.6 万次/天**（加上现有约 1.5 万，仍远低于 10 万的日配额）。最大的不确定点是 `/api/sentiments` 怎么计费：每个 ticker 收 5 次，还是每次请求收 5 次。第 1 天先用自己的 key 实测，再决定覆盖范围。
6. **合规前提：这个功能默认只给站长自己看**（admin key）。EODHD 个人版条款禁止把数据（包括加工后的数据）展示给他人；Global X、iShares 的持仓文件也只允许个人非商业使用。
7. **"早期发现新题材"在 MVP 里走一条保守路径：** 把"未归入任何题材的异动股"按行业和 60 日相关性聚类，LLM 命名后生成"题材提案"，由站长审核。系统从不自动上线新题材。BERTopic/嵌入式文本发现推到第二期。

---

## 1. 开源方案取舍

| 项目 | 结论 | 用法 |
|---|---|---|
| tradermonty/claude-trading-skills `theme-detector`（MIT，2,977★，活跃） | **只借鉴设计** | 借用热度分量（动量/量能/上升趋势/宽度）、五段生命周期、"同题材 ETF 越来越多 = 拥挤"这三个思路。它用 Finviz 行业做粒度，分不出量子、SMR、人形机器人这类窄题材，而且依赖抓取 Finviz，所以不直接使用。 |
| BERTrend（MPL-2.0） | 第二期再说 | 只借鉴"流行度 + 指数衰减 + 分位数划分弱/强信号"的思路。它依赖很重（RabbitMQ、GPU 建议、和 sentence-transformers 版本冲突）。 |
| BERTopic / Turftopic / GLiNER / fastembed | **MVP 不用** | 会引入 torch/onnx，服务器磁盘本来就紧（`deploy.yml` 里有 prune 步骤）。另外现在没有可回溯的新闻语料，上了也没东西可聚类。 |
| StephanAkkerman 系列（reddit-stock-analyzer 等，MIT） | 只借公式 | 借用 `(m-prev)/(prev+1)` 平滑动量，用在排名变化和"新晋"判定上。 |
| networkx / ruptures / Riskfolio | 不用 | MVP 里的聚类和相关性用 numpy 就够了，参照 `services/similar_stocks.py` 的向量化写法。 |
| OpenBB / TradingAgents / FinRobot 等 Agent 框架 | 不用 | 都是"给一个 ticker、出一份报告"的模式，没有发现题材的能力。 |
| Finviz Themes（40 个题材 / 268 个子题材） | 只人工参考命名 | Elite API 存在，但服务条款未核实。MVP 不抓取，只在人工浏览免费站时参考它的子题材命名。 |

---

## 2. MVP 原则

- **确定性优先。** 热度、排名、生命周期、提醒全部由公式算出，可复现。LLM 不参与打分。
- **严格的时间点一致性（PIT），说清楚不满足的部分。** 成员表用 `valid_from` / `valid_to` 区间记录。上线前回填的历史用的是"今天的成员"，统一标 `pit=false`，界面上加角标提示。
- **沿用现有的发布机制：** `pipeline_runs` + `data_publications` 加质量门。门控失败时，前端继续显示上一期数据并标为"过期"，与 `docs/architecture.md` §4 一致。
- **原始数据可回溯：** sentiments、news、ETF 持仓的原始返回全部通过 `services/raw_store.persist_snapshot` 落盘。
- **GET 接口只读本地数据，不调用外部服务或 LLM。** 需要花钱的操作只能通过 admin key 加限流的 POST 接口触发。
- **界面文案用英文**（项目约定）；飞书推送和 AI 简报正文用中文，与现有异动归因和日报保持一致。

---

## 3. MVP 使用的数据源

| 数据 | 用途 | 成本 | 核验状态 |
|---|---|---|---|
| `daily_prices`（screener 每次会补齐至少 252 个交易日）+ `stock_screener_snapshot`（performance_1d…1yr、relative_volume、MA、距 52 周高点） | 篮子收益、宽度、量能、点火计数 | 0（已有） | 已有代码 |
| 专题 ETF 的 EOD 价格（约 35 只） | 代理 ETF 走势、主题 RRG | 每天约 35 次（参照 `rrg_prices.py` 逐只拉 `eod/{t}`）。也可以从每日原始 `bulk_eod` 快照里提取，成本为 0 | 已有代码 |
| EODHD `/api/sentiments` 的 `count` | 每只股票每天的新闻文章数 → 关注度 | 最坏约 15.5k 次/天，最好约 155 次/天，**待实测** | 已更正（计费公式有歧义） |
| EODHD `/api/news?s=` | 只给龙头股取证据标题 | 约 40 次请求 × 5 = 200 次/天 | 已确认（每次请求只能带 1 个 ticker） |
| 现有 Yahoo RSS（`services/news_fetcher.fetch_yahoo_news`） | 补充证据标题 | 0 | 已有代码 |
| ETF 持仓：Roundhill 合并 CSV、Global X 每日 CSV、KraneShares CSV、iShares `latest-holdings.csv`；其余用 EODHD `fundamentals/{ETF}?filter=ETF_Data::Holdings` 兜底 | 成分股候选、精准度参照 | 发行商文件免费；EODHD 每只 10 次，每周约 150 次 | 已确认（小众 ETF 在 EODHD 的覆盖率未核实） |
| `tickers.description`（EODHD General.Description） | LLM 核验成分股时的证据原文 | 0（已有） | 已有代码 |
| SEC EDGAR 全文检索中的 8-K（第二周的弹性任务） | "主线"阶段的确认门 | 免费，每周约 100 次请求 | 已更正（结果按文档计数，需按 adsh 去重；结果上限 1 万条） |

---

## 4. 题材分类体系：冷启动与维护

### 4.1 种子文件 `services/themes/taxonomy/themes_v1.yaml`（Git 是唯一来源）

```yaml
version: themes-v1
themes:
  - slug: memory-hbm
    name_zh: 存储 / HBM
    name_en: Memory & HBM
    megatrend: ai-infrastructure
    definition: >   # 给 LLM 核验用，1–3 句，写清楚包含和排除的业务
      Companies whose revenue materially depends on DRAM/HBM/NAND/HDD storage ...
    keywords: ["high bandwidth memory", "HBM", "DRAM", "NAND", "hard disk"]
    negative_keywords: ["memory care", "memory foam"]
    proxy_etfs: [DRAM]
    seed_core: [MU.US, WDC.US, STX.US, SNDK.US]
    finviz_ref: [subtheme_semismemory]      # 只作参考，不抓取
    pins: []      # 人工强制纳入
    bans: []      # 人工强制排除
    status: active    # active | watch | retired
```

v1 起步约 20 个题材。只放有专题 ETF 或种子明确的题材；Finviz 没有对应子题材的，就用专题 ETF 来定义：

| slug | 题材 | 代理 ETF（研究中核验过的上市日期） | 核心种子（示例，限 R3000+NDX 美股） |
|---|---|---|---|
| ai-compute | AI 算力芯片 | SMH、SOXX、AIQ | NVDA AMD AVGO MRVL |
| memory-hbm | 存储/HBM | DRAM（2026-04-02） | MU WDC STX SNDK |
| optical-photonics | 光通信/CPO | LYTE（2026-08-06） | COHR LITE CIEN AAOI FN CRDO |
| ai-power | AI 电力与电网 | VOLT（2024-12-03）、ZAP、DTCR | VST CEG NRG TLN GEV VRT PWR |
| neocloud | AI 新云/算力租赁 | NCLD（2026-08-06） | CRWV NBIS IREN CORZ APLD |
| ai-software | AI 应用/软件 | CHAT（2023-05-18）、AGIX | PLTR NOW AI |
| nuclear-smr | 核电/SMR | NLR、NUKZ | OKLO SMR NNE BWXT CEG |
| uranium | 铀 | URA、URNM | UEC UUUU LEU |
| quantum | 量子计算 | QTUM | IONQ RGTI QBTS QUBT |
| humanoid | 人形机器人 | HUMN（2025-06-26）、KOID（2025-06-04）、BOTT | TSLA TER SYM SERV |
| drones-defense | 无人机/防务科技 | DRNZ（2025-10-29）、SHLD | AVAV KTOS RCAT ONDS |
| missile-defense | 导弹防御/防务龙头 | XAR | LMT NOC RTX LHX |
| space | 太空经济 | UFO、ARKX | RKLB ASTS LUNR PL RDW FLY |
| cybersecurity | 网络安全 | CIBR、BUG | CRWD PANW ZS FTNT |
| glp1 | GLP-1/减重 | OZEM（2024-05-21）、HRTS | LLY VKTX HIMS AMGN |
| stablecoin-crypto | 稳定币/加密基础设施 | STBQ（2025-12-22）、BKCH | CRCL COIN HOOD GLXY |
| ev-battery | 电动车/电池 | LIT、KARS | TSLA RIVN QS |
| solar-storage | 光伏/储能 | TAN | FSLR ENPH NXT FLNC |
| robotaxi | 自动驾驶 | CABZ | TSLA |
| critical-minerals | 稀土/关键矿产 | REMX（**未核实**） | MP |

### 4.2 成分股生成流程（`services/themes/membership.py`）

1. **候选生成**，三路取并集：
   - (a) 代理 ETF 中权重 ≥ 0.5% 的持仓，映射到 R3000+NDX 股票池。Roundhill 的 TRS 互换腿按标的 CUSIP 还原成股票。
   - (b) `tickers.description` 命中关键词（≥ 2 个不同关键词，且不命中排除词）。这一条参照 MSCI 的间接相关性规则。
   - (c) `seed_core` 和 `pins`。
2. **LLM 核验**：调用 `services/deepseek_client.generate_deepseek_json`，每次请求创建客户端，JSON 模式，关闭思考模式。每批 12 只候选。prompt 前缀固定为"指令 + 题材定义"，这样能命中 DeepSeek 的缓存计价。每只候选返回：
   ```json
   {"ticker":"MU.US","exposure":3,"role":"pure_play|supplier|adopter|tangential",
    "evidence_quote":"<description 中逐字片段>","confidence":0.0-1.0}
   ```
   **硬校验**：`evidence_quote` 做空白归一化后，必须是业务描述的子串，否则直接丢弃。这和现有 `ai_assistant.validate_evidence_citations` 的逻辑一致，模型捏造的引用进不来。DeepSeek 文档提到 JSON 输出偶尔为空，所以失败最多重试 2 次。
3. **分层**：
   - exposure 3 为 `core`，权重 1.0；exposure 2 为 `core`，权重 0.75；exposure 1 为 `peripheral`，权重 0.4；exposure 0 拒收。
   - `pins` 一律强制为 `core`，`bans` 一律排除。
   - ETF 持仓是参照，不等于自动纳入：只在 ETF 里、没过 LLM 核验的股票，最多进 `peripheral`，并标 `source=etf_only`。
4. **证据**（借鉴东方财富 F10 的"入选理由"）：每只成分股保存原文片段、来源（`description@2026-10-10` / `etf:DRAM@2026-10-09` / `manual`）、模型、prompt 版本，以及 `valid_from`。

### 4.3 维护节奏

- **每周日**：更新 ETF 持仓，并对新出现的业务描述只核验新增候选。被移出的成员写 `valid_to`，不删除记录。
- **每季度**：用新的 prompt 版本全量重新核验，并保留旧版本以便对比。
- **新题材**：只能通过"题材提案"（见 §7.8）进来，由站长批准后手动加进 YAML。admin 接口会直接返回一段可粘贴的 YAML。这样只保留 Git 一个来源，不会出现两边不一致。

---

## 5. 新增数据表（Alembic `migrations/versions/0023_theme_trends.py`，模型写在 `models.py`）

| 表 | 关键字段 | 说明 |
|---|---|---|
| `themes` | id, slug(unique), name_zh, name_en, megatrend, definition, keywords_json, negative_keywords_json, proxy_etfs_json, status, taxonomy_version, updated_at | 由 YAML 同步而来 |
| `theme_members` | id, theme_id, ticker(FK tickers), tier(core/peripheral), weight, exposure(0–3), role, source(seed/etf/keyword/manual), evidence_quote, evidence_source, evidence_as_of, llm_model, prompt_version, review_status(auto/pinned/banned), valid_from, valid_to, source_run_id；UNIQUE(theme_id, ticker, valid_from) | 按区间记录成员，支持 PIT |
| `etf_holdings_snapshots` | etf, as_of_date, ticker(nullable), cusip, holding_name, weight_pct, is_swap, source(issuer/eodhd), raw_snapshot_id；UNIQUE(etf, as_of_date, cusip/ticker) | 从现在开始积累持仓历史 |
| `ticker_attention_daily` | ticker, date, article_count, sentiment_normalized, observed_at, raw_snapshot_id；PK(ticker, date) | 每年约 110 万行，SQLite 能承受 |
| `theme_daily` | theme_id, date, n_members, n_priced, ret_1d, idx, x5, x21, x63, x126, b50, nh, d_b50_10, ign, ign5, vol_z, att, att_breadth, cocorr_pct, P, B, V, A, heat_raw, heat, s5, s20, rank_s5, rank_s20, rank_chg, streak_top5, ts_z, stage, flags_json, pit(bool), scoring_version, source_run_id；PK(theme_id, date) | 每天的核心产出 |
| `theme_member_daily` | theme_id, ticker, date, weight, r1, x5, x21, pth, rvol5, att_z, leader_score, ignited(bool), first_ignition_date；PK(theme_id, ticker, date) | 用于成分股表和龙头排序 |
| `company_trend_daily` | ticker, date, ct_score, rank, x5_sector, rvol5, att_z, pth, theme_slugs_json, flags_json；PK(ticker, date) | "热门公司"榜单 |
| `news_items` | id, url_hash(unique), published_at, title, summary(截断至 500 字), publisher, source(eodhd/yahoo), tickers_json, tags_json, raw_snapshot_id | 只存证据所需的标题，保留 180 天，**仅站长可见** |
| `theme_briefs` | id, theme_id, date, evidence_hash, model, prompt_version, evidence_json, content_json, validation_report, status；UNIQUE(theme_id, evidence_hash, model) | 写法参照 `DecisionBriefCache` |
| `theme_alerts` | theme_id, date, alert_type(emerging/stage_change/proposal), payload_json, delivered_at；UNIQUE(theme_id, alert_type, date) | 去重写法参照 `RsiAlert` |
| `theme_proposals` | id, proposed_slug, label_zh, rationale, evidence_json, tickers_json, first_seen, last_seen, hit_count, status(pending/accepted/rejected/expired) | 新题材候选 |

---

## 6. Worker 作业与调度

在 `core/scheduler.py` 注册，时区 America/New_York，接在现有 04:00 factor 作业和 04:30 RSI 作业之后：

| 时间 | 作业 | 内容 | 依赖与降级 |
|---|---|---|---|
| 二至六 04:35 | `theme_etf_prices` | 约 35 只代理 ETF 的 EOD 价格，参照 `rrg_prices` 的写法（ETF 先插入 `tickers` 表，冲突时跳过） | 独立运行，失败不影响其他作业 |
| 二至六 04:40 | `theme_attention_sync` | `/api/sentiments`，每批 50 只，from=d−6、to=d 做覆盖式更新（迟到的文章会补进来）；有每日调用上限 `THEME_ATTENTION_MAX_CALLS_PER_DAY` | 超预算时改为"市值前 1,500 + 全部题材成员" |
| 二至六 04:50 | `theme_trends` 发布管道 | 计算 theme_daily、member_daily、company_trend_daily，过质量门后发布 dataset `theme_trends` | 必须等 screener 当日已发布。关注度缺失时降级发布 |
| 二至六 05:05 | `theme_briefs` | 为热度前 8 的题材，以及当天"萌芽"或阶段变化的题材，取证据并调 LLM 生成简报 | 失败只记录，不阻塞发布 |
| 二至六 05:15 | `theme_alerts` | 萌芽或阶段跃迁时，发一张飞书事件卡（去重） | — |
| 周一至五 10:00 | 现有 morning briefing | 新增"题材热度"段落（确定性生成，不额外调 LLM） | 数据过期时注明日期 |
| 周六 07:00 | `theme_proposals` | 对未归属题材的异动股做聚类、LLM 命名、发周报卡片 | — |
| 周日 05:00 | `theme_membership_refresh` | ETF 持仓、候选、增量核验 | — |
| 周六 06:00（弹性） | `edgar_theme_counts` | EFTS 检索 8-K，按 adsh 去重，并统计首次提到该题材的公司数 | 直接用 httpx 调用；本地 edgartools 版本固定在 2.34.2，未必带 `search_filings` |

`services/catchup.py`：factor 追平之后，如果 `latest_published_date("theme_trends") < target`，就只对 target 这一天补跑 attention 和 scoring，不补历史。

一次性脚本 `scripts/backfill_theme_trends.py`：
- sentiments 从 **2025-09-01** 回填。这样可以避开 2024-11/12 的数据近乎断档，以及 2025-08-12 到 08-21 的批量误标。
- theme_daily 用现有的 252 个交易日价格回填，全部标 `pit=false`。

---

## 7. 打分公式（`services/themes/scoring.py`，全部是纯函数）

### 7.1 篮子
```
M_T(d) = { i : valid_from ≤ d < valid_to, 未被 ban, d 日有价格 }
w_i    = tier_weight_i，单只股票上限 15%，超出部分重新归一化
         （等权为主，防止 NVDA 一类大盘股主导篮子）
r_T,d  = Σ w_i·clip(r_i,d, ±50%) / Σ w_i
I_T    = cumprod(1 + r_T)
x_k    = ln(I_T,d / I_T,d−k) − ln(SPY_d / SPY_d−k),   k ∈ {5, 21, 63, 126}
```
成员少于 8 只的题材标 `insufficient_members`，不参与排名。

### 7.2 价格 P
```
P = 0.5·z(x21) + 0.3·z(x63) + 0.2·z(x5)
```

### 7.3 宽度 B（复用 `market_breadth.build_price_feature_frame`）
```
B50   = Σ w·1[close > MA50] / Σ w
NH    = Σ w·1[close ≥ 0.98·max252(close)] / Σ w
ΔB50  = B50_d − B50_{d−10}
IGN   = #{i : r_i,d ≥ +5% 且 relative_volume_i ≥ 2}
        （美股版的"涨停家数"；IGN5 = 近 5 日点火过的不同成员数）
B = 0.4·z(B50) + 0.3·z(NH) + 0.3·z(ΔB50)
```

### 7.4 量能 V
```
rvol5_i = ln( mean(vol, 5d) / median(vol, 60d) )
V       = z( weighted_median_T(rvol5_i) )
```

### 7.5 关注度 A（EODHD 文章数；先算个股，再聚合到题材）
```
c7_i,d = 近 7 个自然日的 article_count 之和
a_i,d  = ( ln(1+c7_i,d) − median_{d−97..d−8} ln(1+c7_i) ) / max(1.4826·MAD, 0.3)   # 需 ≥ 60 个观测
a*_i,d = clip( a_i,d − median_{j∈U} a_j,d, ±4 )    # 扣掉全市场新闻量的整体波动
ATT_T  = Σ w·a* / Σ w
AB_T   = Σ w·1[a* > 2] / Σ w                       # 关注度宽度
A      = 0.6·z(ATT) + 0.4·z(AB)
```
两条护栏：
- 当日全市场文章总数如果不在 60 日中位数的 [0.4×, 2.5×] 区间内，判为 `attention_degraded`，A 不计入。
- 已知异常时段直接屏蔽。

### 7.6 合成、衰减、排名
```
z(·)：截面稳健 z。比较总体 = 题材 ∪ EODHD industry 中成员 ≥ 8 的行业篮子（只展示题材），
      用中位数/MAD 标准化，截断 ±3。
      加入行业是为了在"全市场普涨"时，题材热度不会集体虚高。
H_raw = 0.40·P + 0.30·B + 0.15·V + 0.15·A
        （关注度降级时：(0.40P + 0.30B + 0.15V) / 0.85）
Heat  = 100 / (1 + e^(−H_raw))                       # 0–100，50 为中性
S5_t  = λ5·S5_{t−1}  + (1−λ5)·Heat_t,   λ5  = 0.5^(1/5)  ≈ 0.871   （Now）
S20_t = λ20·S20_{t−1} + (1−λ20)·Heat_t, λ20 = 0.5^(1/20) ≈ 0.966   （Trend）
rank_chg   = rank_{t−1} − rank_t
streak_top5 = S5 连续排名前 5 的交易日数               （对应同花顺"连续 N 天上榜"）
```
和 tradermonty 的权重 0.35/0.20/0.25/0.20 相比，这里加入了关注度，下调了量能，宽度也作为同等重要的确认项。依据：试点中关注度不领先；文献也显示关注度飙升随后多半反转（Da 等 2011；Barber 等 2022）。

### 7.7 新兴标志与生命周期（每日判断，阶段需保持 3 天才切换；"萌芽"除外）
```
ts_z   = (H_raw_d − median_{过去 252 日} H_raw) / (1.4826·MAD)        # 需 ≥ 60 个观测
EMERGE = ts_z ≥ 2 ∧ pct_rank(S5, d−21) < 0.5 ∧ IGN5 ≥ 3 ∧ ΔB50 > 0.10 ∧ x21 > 0
ATTN_ONLY = z(ATT) ≥ 2 ∧ P < 0     → 标"关注升温·价格未确认"，不算萌芽
CoCorr = 成员残差日收益（对 SPY，beta 用 120 日估计）60 日平均两两相关系数，
         取其相对自身 252 日历史的分位数                  （Lou-Polk comomentum 的题材版，未经验证）
```
| 阶段（UI 英文） | 条件 |
|---|---|
| Emerging 萌芽 | 近 10 日出现过 EMERGE，且 S20 截面分位 < 0.75 |
| Accelerating 加速 | S5 > S20 + 5，P > 0.5，B > 0 |
| Leading 主线 | 近 20 日中有 ≥ 15 日 S20 截面分位 ≥ 0.75，且 B50 ≥ 0.6；EDGAR 门通过时另加 `filings_confirmed` 标 |
| Crowded 拥挤 | 处于主线或加速，并且满足以下任一条：CoCorr 分位 ≥ 0.8 且 x63 位于前四分之一；x504 ≥ ln 2（Greenwood-Shleifer-You 的"两年翻倍"）；近 12 个月有新的专属 ETF 上市（Ben-David 等 2023：专属 ETF 一般在高估时发行） |
| Fading 退潮 | S5 < S20 − 5，S5 ≤ 20 日最高 − 15，ΔB50 < 0 |
| Neutral | 其他情况 |

### 7.8 热门公司、题材龙头、新题材提案
```
热门公司   CT_i = 0.35·z(x5_i − sector_median) + 0.25·z(rvol5_i) + 0.20·z(a*_i) + 0.20·z(PTH_i)
           PTH = close / max252；过滤条件：收盘价 ≥ $3，20 日日均成交额 ≥ $5M
题材龙头   L_i  = 0.4·pctrank_T(x21) + 0.3·pctrank_T(PTH) + 0.3·pctrank_T(a*)
           取 L 最高的前 3 只（对应同花顺的"龙头股"）
           first_mover = 本轮行情中最早点火的成员
新题材提案（周度）：
  候选 = 近 10 日 ≥ 2 次 [ r ≥ +5% 且 RVOL ≥ 2 ] 或 a* > 3、且不属于任何 active 题材的股票
  按 industry 粗分，再按 60 日收益相关性 ≥ 0.5 做连通分量聚类（复用 similar_stocks 的算法）
  聚类成立条件：≥ 3 只股票，且跨 ≥ 2 个交易日
  → LLM 根据业务描述和已存标题命名，给出理由和 [E#] 引用
  → 写入 theme_proposals(pending)；连续 20 个交易日未再出现则 expired
```

---

## 8. 证据与 AI 简报（`services/themes/briefs.py`）

- **证据集**，ID 稳定：
  - `E1…Ek` 是指标事实，由 theme_daily 和 member_daily 确定性生成，例如 "x21 = +18.4% vs SPY"、"B50 72%（10 日 +25pp）"、"MU 10-08 +7.2%，RVOL 3.1"。
  - `E(k+1)…` 是新闻标题，最多 12 条：龙头股的 Yahoo RSS 加上 EODHD `/api/news?s=…&from=d−3&limit=10`，按 url_hash 和标题 Jaccard ≥ 0.8 去重。
- **Prompt**（中文，版本号 `PROMPT_VERSION="theme-brief-v1"`）分三段：盘面事实 / 同期新闻 / 风险与反方。每句话都要带 [E#]。明确禁止从标题推断因果，只能写"同期新闻包括"，与现有异动归因规则一致。
- **校验**：复用 `validate_evidence_citations`、`validate_evidence_numbers`、`build_evidence_hash` 的写法。校验通过才缓存；超过 3 个交易日的简报在界面上标为过期。
- **按需刷新**：`POST /api/personal/themes/{slug}/brief/refresh`，需要 `require_configured_admin_api_key` 加 `limit_expensive_requests`，并且同一时间只跑一个，写法参照 `anomaly_scans` 的租约。

---

## 9. API（`api/routers.py` + `api/schemas.py`）

默认都挂 `require_admin_api_key`，开关为 `THEMES_PUBLIC=false`。所有接口只读本地已发布数据，不调用外部服务或 LLM。

| 方法 | 路径 | 返回 |
|---|---|---|
| GET | `/api/v1/themes?date=&view=now\|trend&stage=` | 列表：heat、s5/s20、rank/Δ、stage、streak、P/B/V/A、x5/x21、B50、IGN5、前 3 龙头、代理 ETF 21 日涨跌、近 60 日 S5 sparkline、发布元数据（日期、scoring_version、覆盖率、是否降级、pit） |
| GET | `/api/v1/themes/{slug}?date=` | 定义、120 日分量序列、篮子/SPY/代理 ETF 归一化序列、宽度序列、成员表（分层、权重、入选理由、来源、as_of、各项指标、60 日 sparkline）、最新已校验的简报和证据 |
| GET | `/api/v1/themes/{slug}/history?days=252` | 时间序列 |
| GET | `/api/v1/trending-companies?date=&limit=50` | 热门公司榜（带所属题材标签和标志位） |
| GET | `/api/personal/themes/proposals` | 提案列表 |
| POST | `/api/personal/themes/proposals/{id}/decision` | accepted/rejected；accepted 时返回一段 YAML 片段 |
| POST | `/api/personal/themes/{slug}/brief/refresh` | 按需重新生成简报（单飞、限流） |

主题 RRG 不需要新接口：代理 ETF 写进 `daily_prices` 之后，现有 `GET /api/v1/rrg`（最多 50 个 ticker，会回退读 DailyPrice）可以直接画。

---

## 10. 前端（Next.js；界面英文；只用 Graphite amber 设计 token）

- **导航**：在 `frontend/src/components/shell/CommandPalette.tsx` 的 `NAV_PAGES` 里加 `{ name: "Themes", path: "/themes" }`。这是一级功能，不放进 Market 子标签。
- **`frontend/src/app/themes/page.tsx`**
  - 顶栏：as-of 日期、`DataState`（ready/stale/error）、`SegmentedControl` 切换 Now(5d)/Trend(20d)、阶段过滤；当 `pit=false` 时显示 "Backfilled · not point-in-time" 角标。
  - KPI 条：Emerging / Leading / Crowded 各几个题材，以及今日排名上升最快的题材。
  - 主表 `ThemeLeaderboard`：Rank(Δ)｜Theme｜Stage chip｜Heat 进度条｜60 日 `Sparkline`｜P/B/V/A 四个小柱｜5D/21D vs SPY｜% > MA50｜Ignitions(5D)｜Streak｜Leaders（3 个 chip）｜Proxy ETF。
  - 视图切换：`ThemeTreemap`（ECharts treemap，面积 = 成员市值、单只封顶，颜色 = heat 发散色，颜色只取自 `lib/chartTheme.ts`）和 `Rotation`（复用 `RRGChart.tsx`，画代理 ETF）。
  - 第二个标签 "Trending companies"：CT 榜，带题材 chip，只有关注度的股票加 "Attention only" 标。
  - 只在 admin key 存在时显示 "Proposals" 标签。
- **`frontend/src/app/themes/[slug]/page.tsx`**：
  - 头部：定义；120 日阶段色带。
  - 三张图：S5/S20 和分量堆叠图；篮子 vs SPY vs 代理 ETF；B50/NH 折线叠加 IGN 柱状。
  - "Why it's moving" 简报卡：正文中文，[E#] 可展开到证据列表。
  - 成员表：Tier｜Ticker｜Weight｜Why in theme（原文 + 来源 + as-of + exposure）｜5D/21D｜Near high｜RVOL｜News z｜Leader｜First ignition｜sparkline。
  - 底部写明数据来源：taxonomy/scoring 版本、覆盖率、降级原因。
- **展示规则**：缺失值显示 "—"，不填 0，也不显示任何模拟数据（遵循 `docs/frontend_redesign_plan.md` §10.1）。

---

## 11. 飞书

1. **早报段落**（Mon–Fri 10:00）：
   - 在 `services/report_market.collect_market_context` 里新增 `collect_theme_trends()`，只读最新一期发布。
   - `report_renderer` 增加"题材热度"段落：前 5 名，每个题材一行，写 Heat、Δ排名、阶段、x21、B50、前 3 龙头。另列当日萌芽题材和阶段变化。
   - 如果当天有已校验的缓存简报，摘第一句（带 [E#]）。
   - `REPORT_RENDERER_VERSION` 升到 `cross-asset-v2.3`。数据过期超过 1 个交易日时，写"题材数据截至 X 日"。
2. **事件卡**（05:15）：
   - 只在两种情况下发：(a) 某题材 20 个交易日内首次进入"萌芽"；(b) 热度前 10 的题材进入"拥挤"或"退潮"。
   - 用 `theme_alerts` 唯一键去重，每天最多 1 张卡，走 `NotificationManager.broadcast` 和 `report_card` 的折叠布局。
3. **周六提案周报**：待审核的新题材候选，每个附 3 条证据。
4. 只推送给站长自己的群（不算对外再分发）。

---

## 12. 测试与质量门

**后端**（pytest）：
- `tests/test_theme_scoring.py`：稳健 z 的截断；EWMA 半衰期（输入阶跃 5 天后，S5 正好走完一半）；宽度和点火计数；全市场新闻量整体 ×2 时 a* 不变；屏蔽异常时段；生命周期状态机的迁移和"保持 3 天"规则；EMERGE 和 ATTN_ONLY 各自的边界条件；成员少于 8 只的排除逻辑。
- `tests/test_theme_membership.py`：编造的引用会被拒；`valid_from` 之前的日期查不到该成员；pins/bans 生效；ETF 互换腿能映射到标的。
- `tests/test_theme_attention.py`：调用量预算上限；超预算后自动降级股票池；None 和 [] 的语义区分（参照 `get_report_calendar`）。
- `tests/test_theme_pipeline.py`：质量门失败时持久化并抛出；前一期继续以"过期"状态提供；catch-up 只补 target 当天。
- `tests/test_theme_api.py`：mock `deepseek_client` 和 `eodhd_client` 时让它们一被调用就抛错，以此证明 GET 接口不触发外部调用；admin 门控生效。
- **Golden 测试**：3 个题材 × 40 天的固定数据，对 theme_daily 的输出做快照比对。

**发布质量门**（`data_quality.QualityReport`）：
- screener 当日已发布；
- 至少 90% 的成员有当日价格；
- 关注度覆盖率至少 85%，全市场文章总数在合理区间内，否则降级而不是失败；
- active 题材至少 15 个可以排名；
- heat 没有 NaN，排名唯一。

**前端**：
- vitest：排行榜在过期和缺失值状态下渲染正确；
- `designTokens.test.ts` 通过；
- `frontend/e2e/themes.spec.ts` 使用 mock API。

**离线评估**（`scripts/evaluate_theme_heat.py`，不阻塞上线）：
- 用研究中的 14 个题材样本和 10 个对照组，换成 EODHD 真实篮子，统计领先或滞后周数和误报率；
- 用 `services/quant/research.evaluate_factor` 只在 S&P 500 子集上算 heat 对成员未来 21 日收益的 Rank-IC；
- 结果必须注明成员表有前视偏差。

**上线验收**：
- 回看 2026 年数据，memory、photonics、nuclear、quantum、humanoid、drones、space 都曾进入前 10；
- S5 排名的日间秩相关不低于 0.8，避免排名乱跳；
- 10 个对照伪热点在回填期内都没有触发 EMERGE。这是目标，做不到就下调 A 的权重。

---

## 13. 成本估算

**EODHD**（日配额 10 万，现用约 1.5 万）：

| 项目 | 最坏（每 ticker 收 5 次） | 最好（每次请求收 5 次） |
|---|---|---|
| sentiments，约 3,100 只 | 15,500 次/天 | 155–310 次/天 |
| news 证据，约 40 次请求 | 200 次/天 | 200 次/天 |
| 代理 ETF EOD | 约 35 次/天 | 约 35 次/天 |
| ETF 持仓兜底 | 约 150 次/周 | 同左 |
| 一次性回填 | 约 1.55 万次（若按区间拆分请求，最多约 7.7 万，分 2 天跑） | 很少 |
| **合计** | **约 1.6 万次/天，总用量约 31%** | **< 1 千次/天** |

**DeepSeek**（deepseek-flash；配置里的 `DEEPSEEK_MODEL="deepseek-v4-flash"` 是仍被接受的旧别名；按峰时价算：输入未命中缓存 $0.30/M，输出 $1.2/M）：
- 冷启动成员核验约 1,440 对，约 120 次调用，约 0.8M 输入 + 0.1M 输出，约 **$0.4，一次性**；
- 每周增量 < $0.05；
- 每天简报 ≤ 10 份（每份约 3.5k 输入 + 0.7k 输出），约 $0.02/天；
- 提案命名约 $0.02/周；
- **合计约 $1/月。**

**其他**：
- SEC 和发行商文件免费，但需要合规 UA（复用 `SEC_USER_AGENT`）。这些请求会走 WARP，要观察是否被限流。
- 存储：ticker_attention_daily 每年约 110 万行，member_daily 每年约 25 万行，news_items 保留 180 天，总计远低于 1 GB。

---

## 14. 两周排期

| 天 | 交付 |
|---|---|
| D1 | 暂停 worker，用自己的 key 跑研究里的 `eodhd_cost_probe.py`。要确认：sentiments 计费方式和单次 ticker 上限、`/api/news` 不带参数能否拉全市场、`/api/user` 的套餐信息、DRAM/LYTE/NCLD/HUMN 在 EODHD 的持仓覆盖率。然后写 themes_v1.yaml |
| D2 | 0023 迁移和模型；在 `eodhd_client` 加 `get_sentiments`、`get_news`、`get_etf_holdings`；ETF 持仓解析器（Roundhill、Global X、KraneShares、iShares，其余走 EODHD 兜底）；代理 ETF 价格作业 |
| D3 | 成员生成：候选、LLM 核验、引用校验、分层、overrides；人工抽检 |
| D4 | attention 同步作业和回填脚本（从 2025-09-01 起） |
| D5–6 | scoring 纯函数和测试；发布管道和质量门；scheduler；catch-up；252 日回填 |
| D7 | API 和 schemas，以及不调用外部服务的测试 |
| D8–9 | `/themes` 列表页、详情页、热门公司页；vitest 和 e2e |
| D10 | 简报作业和校验；飞书早报段落和事件卡；提案作业；跑离线评估并调阈值；写 `docs/themes/README.md` |
| 弹性 | EDGAR 8-K 确认门 |

---

## 15. 风险

| 风险 | 应对 |
|---|---|
| sentiments 计费公式不明确（最坏 1.55 万次/天） | D1 实测；设每日调用上限；超预算自动缩小股票池 |
| EODHD 新闻 93–96% 来自 Yahoo，`symbols[]` 按字母序截断到 50 个（约 2–3% 漏标），AI 标签 2025 年中才开始普及 | 关注度定位为"主流财经媒体覆盖度"，权重 0.15；用个股基线加扣除全市场的方式归一化；屏蔽异常时段 |
| **许可**：EODHD 个人版禁止向他人展示，Global X 禁止衍生作品，iShares 仅限个人非商业 | 默认仅站长可见（`THEMES_PUBLIC=false`）。现有公开页面也有同样的问题，这里只提示，不在本方案范围内处理 |
| 成员表有前视偏差，回填结果看起来比实际好 | `pit=false` 角标；`valid_from` 区间；从上线日起才算 PIT；回测只在 S&P 500 上做，并注明偏差 |
| LLM 打暴露度结论不稳（arXiv 2609.31013：不同模型秩相关只有 0.52） | LLM 只做核验，必须给出原文引用；固定模型和 prompt 版本；以 ETF 持仓作参照 |
| 关注度飙升随后反转，伪热点误报 | 不能单独触发萌芽；ATTN_ONLY 单独标注；对照组回测 |
| 成分股少、大盘股在多个题材重复出现 | 至少 8 只成员；单只权重上限 15%；详情页显示与其他题材的重叠度 |
| 题材是凭事后结果挑的 | 评估时把题材和对照组事先固定；新题材只能走提案、人工批准 |
| 外部服务通过 WARP 被拦（SEC、发行商） | 失败只降级；必要时把对应域名加进 `NO_PROXY` |
| DeepSeek 模型名变化或 JSON 返回为空 | 模型名做成配置项；重试；校验失败的结果不缓存 |

---

## 16. 明确不做

- Reddit、X、Stocktwits、ApeWisdom、Bluesky 采集器。原因：Reddit 公共 API 2027 年 3 月关停；Stocktwits 条款禁止抓取；试点显示社交关注度不领先；X 按量计费且 `$` 操作符在 counts 端点上能否使用存在矛盾。
- BERTopic、BERTrend、嵌入、GLiNER，以及任何需要 torch/onnx 的组件；也不做基于新闻全文的自下而上主题建模。
- 新闻全文语料回填（Alpaca/Benzinga，SEC 8-K EX-99 约 5 GB）。第二期优先做 SEC EX-99。
- Wikipedia、HN、GDELT 关注度（试点中 10/10 伪热点都会触发；GDELT 2026 年服务退化）。第二期只作为"提名"特征加入。
- Google Trends、期权流、空头成交量、职位和 App 数据。
- A 股/港股概念板块数据（AKShare、Tushare、富途）。这里只借鉴它们的数据模型；HK/TW/EU 核心标的不进入 MVP 题材。
- 盘中实时热度；把热度宣传为交易信号或 alpha；未经审核自动上线新题材；抓取 Finviz 或调用 Finviz Elite API。

---

## 17. 依据与核验状态

| 依据 | 用在何处 | 研究核验结论 |
|---|---|---|
| EODHD `/api/sentiments`（count 等于逐只 ticker 的新闻数，实测 30 日总量吻合） | 关注度 | **corrected**：计费 5n 还是 5+5n 有歧义；每次最多 100 只 ticker 仅见于 MCP 文档 |
| EODHD `/api/news`（每次请求 5 次调用，只能带 1 个 ticker；`t=` 为精确匹配；全市场 feed 是否存在待定） | 证据标题 | confirmed，部分**待定** |
| EODHD `ETF_Data.Holdings`（只有当前持仓，每只 10 次调用） | 成员候选兜底 | confirmed；小众 ETF 覆盖率**未核实** |
| 发行商持仓文件（Roundhill 约 2023-12 起、Global X 2024-08-06 起、KraneShares 约 2024-01 起） | 成员候选与历史 | confirmed；许可条款有限制 |
| 专题 ETF 上市日期（DRAM、LYTE、NCLD、HUMN、KOID、OZEM、STBQ、DRNZ 等） | 代理 ETF、拥挤标志 | confirmed；REMX、NUKZ 的具体日期**未核实** |
| tradermonty theme-detector（权重、生命周期、ETF 增多即拥挤） | 设计借鉴 | confirmed / corrected（发现能力只到行业层面） |
| 同花顺热榜（连续上榜、涨停家数、关联 ETF）、东方财富 F10 核心题材（入选理由 + IS_PRECISE）、韭研公社按题材归类异动 | 数据模型与 UX | confirmed（同花顺、东方财富）；韭研公社 **corrected**（页面由 JS 渲染，未观察到当日具体内容） |
| 关注度试点（n=14 个题材 / 10 个对照组；8-K 持续 z>3：10/14 对 0/10，但中位滞后 12 周） | 关注度降权、EDGAR 只作确认门 | 研究结论为**anecdotal**（只有 7 个有效突破样本） |
| Moskowitz-Grinblatt、George-Hwang、Avramov 等（MAD） | 价格分量 | confirmed / corrected（细节） |
| Lou-Polk comomentum | CoCorr 拥挤指标 | confirmed；题材版用法**未经验证** |
| Greenwood-Shleifer-You 2019；Ben-David 等 2023 | 拥挤/泡沫标志 | confirmed |
| Da 等 2011、Barber 等 2022、Cookson 等 2024 | 关注度会反转 | confirmed（部分数值未核实） |
| arXiv 2609.31013（不同 LLM 打分分歧） | LLM 只做核验 | confirmed |
| DeepSeek 定价、JSON 偶尔返回空 | 成本与重试 | corrected（pro 版并发上限为 500） |
| EDGAR EFTS（结果按文档计数，上限 1 万条） | 弹性确认门 | corrected |
| Finviz Themes（40/268） | 只作命名参考 | corrected（有 API，服务条款**未核实**） |
| Stocktwits、Reddit、X counts | 不做 | corrected（条款禁止 / 服务关停 / `$` 操作符可用性矛盾） |