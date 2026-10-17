# 题材热度（Trending themes）

`/themes` 页面回答两个问题：现在市场在炒哪些投资题材，每个题材里哪些公司最热。题材定义由人维护，成员由程序生成，DeepSeek 核验成员并给出原文证据；热度用确定性公式计算，按天发布。

- 调研与方案取舍：[`docs/trending_themes_research_2026-10-10/`](../trending_themes_research_2026-10-10/README.md)
- 实现契约（表、模块、打分公式、作业）：[`IMPLEMENTATION_SPEC.md`](IMPLEMENTATION_SPEC.md)

## 1. 功能一览

| 位置 | 内容 |
|---|---|
| `/themes` → Themes | 题材排行。可在 Now（5 日半衰期）/ Trend（20 日）之间切换，可按阶段筛选。列：热度条、60 日热度走势、P/B/V/A 四个分量、5D/21D 相对 SPY 超额、站上 MA50 的成员占比、近 5 日点火数、连续前五天数、龙头、代理 ETF。另有 Treemap 和 Rotation（代理 ETF 的 RRG）两种视图 |
| `/themes` → Trending companies | 全股票池的热门公司前 50：同行业相对表现、RVOL、新闻关注度 z、接近一年高点程度，以及所属题材 |
| `/themes` → Proposals | 每周从"不属于任何题材的异动股"里聚出的新题材候选。批准后会给出一段可直接粘进 `themes_v1.yaml` 的 YAML |
| `/themes/[slug]` | 题材定义；热度与分量、篮子 vs SPY vs 代理 ETF、宽度与点火三张图；Why it's moving 中文简报，每句都带证据引用；成员表，每个成员附入选理由原文、来源、暴露度、龙头排名和首次点火日 |
| 飞书早报 | 10:00 开盘速递新增"题材热度"一段：前 5 名、当日萌芽题材、阶段变化 |
| 飞书提醒 | 有题材首次进入萌芽，或热度前 10 的题材转为拥挤/退潮时，发一张汇总卡片（去重） |

**访问控制**：所有题材接口都要求 admin key（`THEMES_PUBLIC=false`）。EODHD 个人版条款不允许把数据，包括加工后的结果，展示给他人。

## 2. 数据与成本

| 数据 | 来源 | 频率 | EODHD 调用 |
|---|---|---|---|
| 题材定义 | `services/themes/taxonomy/themes_v1.yaml`（Git 维护，20 个题材） | 每次作业同步 | 0 |
| 成员候选 | 代理 ETF 持仓（`fundamentals/{ETF}?filter=ETF_Data`）、业务描述关键词、人工种子 | 每周日 | 约 36 只 ETF × 10 ≈ 360 次/周 |
| 成员核验 | DeepSeek JSON 模式，必须给出业务描述里的逐字引用，结果按输入哈希缓存 | 每周日，只核验新增或变化的候选 | 0（DeepSeek 每月约 $1） |
| 价格、成交量 | 本地 `daily_prices`（screener 已同步） | 每日 | 0 |
| 代理 ETF 价格 | `eod/{ETF}` | 每日 | 约 37 次/天 |
| 新闻关注度 | 全市场 `/api/news`（不带 ticker），每页 1,000 篇 | 每日 | 约 10 次/天；回填一年约 3,700 次 |

2026-10-10 用站长 key 实测：全市场 feed 每页固定 5 次调用，与页大小无关；工作日约 1,900 篇；ETF 持仓每只 10 次。详见[调研附录 A](../trending_themes_research_2026-10-10/appendix_a_eodhd_news_cost.md)。

## 3. 计算方法（摘要）

- **篮子**：成员按层级加权（core 1.0 / 0.75，peripheral 0.4），单只上限 15%，收益相对 SPY 取对数超额。
- **分量**：
  - P：5/21/63 日超额；
  - B：MA50 以上占比、接近 52 周高点占比、10 日宽度变化；
  - V：成交量放大；
  - A：新闻篇数相对自身 90 天基线的稳健 z，扣除全市场整体波动后再聚合。

  四项都在"题材 ∪ 行业篮子"的截面上做稳健 z。
- **热度**：Heat = logistic(0.40P + 0.30B + 0.15V + 0.15A)。新闻关注度不完整时 A 不计入，界面会提示。
- **阶段**：萌芽、加速、主线、拥挤、退潮、中性。换阶段要连续 3 天确认，进入萌芽除外。拥挤由成员联动分位和 x63 判断，或者近 12 个月有新的专属 ETF 上市。阶段历史延续已发布的记录，不随窗口重算。
- **关注度只是辅助信号**：调研试点显示，免费关注度数据在伪热点上全部触发，所以只占 15% 的权重。另有"只有关注、价格未确认"的标记，不计入热度。

完整公式见规格 §4。

## 4. 作业（worker，America/New_York）

| 时间 | 作业 |
|---|---|
| 周二至周六 04:35 | 代理 ETF 价格 |
| 周二至周六 04:40 | 新闻摄取：刷新最近 3 天，并回填至多 120 个缺失日 |
| 周二至周六 04:50（07:50 重试） | `theme_trends` 发布 |
| 周二至周六 05:05 | 中文简报：热度前 8，加上萌芽或阶段变化的题材 |
| 周二至周六 05:15 | 飞书阶段提醒。如果 07:50 会重跑，就等重跑后再发 |
| 周六 08:30（07:50 重试之后） | 新题材候选 |
| 周日 05:00 | 成员刷新 |
| worker 启动后约 120 秒 | 一次性 bootstrap：缺什么补什么，本身不发提醒，不阻塞启动；完成后补跑时段已过的简报和提醒 |

开关：`THEMES_ENABLED`（总开关）、`THEME_BRIEFS_ENABLED`、`THEME_ALERTS_ENABLED`、`THEME_PROPOSALS_ENABLED`。全部参数见 `.env.example` 的 Trending themes 段。

## 5. 首次部署

1. 部署后 Alembic 会执行 `0023_theme_trends`（新增 13 张表）。
2. worker 启动约 2 分钟后，bootstrap 依次同步题材、生成成员（DeepSeek 核验，约几分钟）、拉代理 ETF 价格、拉近期新闻，然后发布当天的 `theme_trends`。这时新闻历史不足，关注度会标记为降级，属于正常情况。
3. 想马上看到完整历史，在服务器上执行：

   ```bash
   docker compose exec worker python scripts/backfill_theme_trends.py --news-only
   ```

   ```bash
   docker compose exec worker python scripts/backfill_theme_trends.py --trends-only --sessions 120
   ```

   新闻回填约 3,700 次 EODHD 调用，分批执行，可以中断后重跑。不手动回填的话，每天的 04:40 作业每次补 120 天，大约 4 天补齐。
4. 回填出来的历史用的是当前成员，标为"Backfilled · not point-in-time"。上线之后每天的发布才是时点正确的。

## 6. 维护题材

- 编辑 `services/themes/taxonomy/themes_v1.yaml`：定义、关键词、排除词、代理 ETF、种子、`pins`（强制纳入）、`bans`（强制排除）、`status`（active / watch / retired）。
- 下次作业时自动同步。删掉的题材会标为 retired，数据库里的记录不删。
- 新题材的来源：Proposals 页面批准后复制 YAML 片段；也可以直接手写。

## 7. 已知限制

- **成员不是严格时点数据**：业务描述只有当前版本，成员区间从上线日起才是时点正确的。回测只适合在 S&P 500 子集上做，并要注明这一点。
- **新闻来源单一**：EODHD 新闻 93–96% 来自 Yahoo，和现有 Yahoo RSS 高度重叠；AI 标签 2025 年中以前很少；2024 年 11–12 月和 2025-08-12 至 08-21 有数据异常。
- **swap 持仓缺失**：使用总收益互换的 ETF（如 Roundhill DRAM）在 EODHD 持仓里没有 swap 腿，需要靠种子补齐。读取发行商持仓文件放在 v2。
- **简报可能退回模板**：DeepSeek 的输出如果三次都没通过校验（例如引用了错误的证据 ID），会改存一份确定性的模板简报，界面标为 Template brief，下次作业自动重试 AI 简报。
- **v2 再做**：自下而上的文本题材发现（8-K 新词、BERTopic）、残差共振社区、Wikipedia/HN 提名特征、发行商持仓日文件和 N-PORT 历史。

## 8. 测试

```bash
.venv/bin/python -m pytest -q tests/test_theme_*.py
```

```bash
npm --prefix frontend test -- src/app/themes src/components/themes
```

- `tests/test_theme_migration.py` 会比较迁移建出的表结构和 `models.py` 是否一致。
- `frontend/e2e/themes.spec.ts` 用 mock 掉的 API 覆盖锁定和解锁、过期提示、窄屏溢出和导航宽度。
