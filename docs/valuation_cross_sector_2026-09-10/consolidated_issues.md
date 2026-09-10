# 估值模型综合问题清单：本站、Simply Wall St、Alpha Spread 与官方财报

实施更新：用户随后授权修复 V01–V04、V06–V10，V05 继续 Hold。代码改动、逐项状态及验证边界见 [修复记录](../valuation_repair_2026-09-10/README.md)。以下保留当时诊断时点，所有“本轮”“Hold”“当前默认”均指原审计快照。

评估日期：2026-09-10。**此前四个方向全部保留，实施状态均为 Hold；本轮仅补充证据和问题清单，没有修改生产模型、默认参数或线上数据。**

## 结论与证据边界

先处理输入完整性、业务适用性和输出语义，再评估风险参数及预测规则。不能因为 NVDA 偏离市价就放宽增长上限，也不能以接近 SWS 或 Alpha Spread 的结果作为修复通过标准。

沿用原先冻结的 33 家、11 个板块诊断面板；Alpha Spread 对照沿用 SWS 对照的每板块第一家公司，共 11 家，没有看完结果再换样本。本站原始快照、复算和统一敏感性实验见 [前一轮报告](README.md)。这些股票均已经被观察，不是留出验证集。

来源分工：公司财报及 SEC 文件核查已发生的财务事实；SWS、Alpha Spread 核查其他模型的选择与输出；估值方法文献核查现金流、资本成本和股权桥的逻辑。后两类不能提供唯一正确的内在价值。

## Alpha Spread 核对结果

用户提供的 [NVDA relative-valuation 页面](https://www.alphaspread.com/security/nasdaq/nvda/relative-valuation) 是倍数相对估值，约 **$289.69**。其 [DCF 页面](https://www.alphaspread.com/security/nasdaq/nvda/dcf-valuation/base-case) 约 **$236.60**；[Summary](https://www.alphaspread.com/security/nasdaq/nvda/summary) 明确选择 DCF 作为 NVDA 的 intrinsic value。本次观察不支持把两者简单取平均。

| NVDA 方法 | 本站当前默认 | 用户提供的 SWS 明细 | Alpha Spread 当前 DCF 页 |
|---|---|---|---|
| 每股输出 | $78.28 | $305.48 | $236.60 |
| 现金流 | 供应商 FCF 加税后利息，估算 FCFF | Levered FCF / FCFE | 页面模板为 FCFE |
| 预测基础 | 历史增长信号中位数，再施加上限 | 分析师逐年现金流，覆盖后再外推 | 收入增长、利润率、现金转化率 |
| 显式期 | 5 年 | 10 年 | 7 年 |
| 折现率 | WACC 16.33%；Ke 16.35% | Ke 约 11.24% | 模型页约 10%；资本成本页 Ke 10.01% |
| 终值 | 永续增长 2.5% | 永续增长 3.7% | 退出 P/S 8.6 倍 |

Alpha Spread 的收入增长 21.1%、净利率向 51% 变化、现金转化率 92%，与本站的“FCFF 增长 20%”不是同一变量。其退出倍数也把相对定价假设引入终值，不能仅因都标为 DCF 就视为完全相同的方法。

上表只对照公开方法和输入摘要；没有取得 Alpha Spread 全部逐年现金流、净借款与权益调整明细，**尚未独立复现其 $236.60**。SWS 的 NVDA 表已复算，其他十家 SWS 仅取得公布值及页面日期。

### 相同 11 个板块代表的输出

美元/股；本站为原冻结快照，股价日期 2026-09-09。SWS 沿用前轮记录。Alpha Spread 是本轮读取的公开页面快照，行情和模型更新时间未统一，部分页面带有较旧的抓取缓存；因此不计算所谓跨平台准确率或同日折价排名。

| 板块 | 股票 | 本站 Base | SWS 现金流估值 | Alpha Spread DCF 页 | Alpha Spread 可见模板 / 年数 / 折现率 / 终值 |
|---|---|---:|---:|---:|---|
| 科技 | NVDA | 78.28 | 305.48 | [236.60](https://www.alphaspread.com/security/nasdaq/nvda/dcf-valuation/base-case) | FCFE / 7 / 10.0% / P/S 8.6× |
| 通信服务 | GOOGL | 89.06 | 487.01 | [252.07](https://www.alphaspread.com/security/nasdaq/googl/dcf-valuation/base-case) | FCFE / 5 / 7.9% / P/S 2.9× |
| 可选消费 | AMZN | 不可用 | 433.42 | [242.27](https://www.alphaspread.com/security/nasdaq/amzn/dcf-valuation/base-case) | FCFE / 5 / 8.0% / 永续增长 0% |
| 必需消费 | PG | 86.55 | 196.96 | [129.43](https://www.alphaspread.com/security/nyse/pg/dcf-valuation/base-case) | FCFE / 7 / 7.9% / P/E 17.5× |
| 医疗 | JNJ | 116.81 | 364.27 | [197.45](https://www.alphaspread.com/security/nyse/jnj/dcf-valuation/base-case) | FCFE / 7 / 8.7% / P/S 3.4× |
| 金融 | JPM | 2,432.14* | 493.29 | [328.73](https://www.alphaspread.com/security/nyse/jpm/dcf-valuation/base-case)† | Bank / 5 / 7.6% / P/E 10.9× |
| 能源 | XOM | 113.59 | 192.65 | [118.98](https://www.alphaspread.com/security/nyse/xom/dcf-valuation/base-case) | FCFE / 5 / 8.1% / 永续增长 0% |
| 工业 | CAT | 206.73 | 887.64 | [441.48](https://www.alphaspread.com/security/nyse/cat/dcf-valuation/base-case) | FCFE / 5 / 9.0% / 永续增长 0% |
| 原材料 | LIN | 440.03 | 489.58 | [285.20](https://www.alphaspread.com/security/nasdaq/lin/dcf-valuation/base-case) | FCFE / 7 / 8.7% / 永续增长 0% |
| 公用事业 | NEE | 不可用 | 76.48 | [93.65](https://www.alphaspread.com/security/nyse/nee/dcf-valuation/base-case) | FCFE / 10 / 7.9% / P/E 19.2× |
| REIT | PLD | 198.56* | 127.71 | [133.71](https://www.alphaspread.com/security/nyse/pld/dcf-valuation/base-case)‡ | FCFE / 10 / 8.1% / P/E 36× |

\* 本站统一 FCFF 的业务适用性需要审查。† JPM DCF 页抓取缓存标记为五天前；另一个 Summary 页显示 DCF $327.78，未勾稽为同一版本。‡ PLD 缓存标记为上月，仅保留方法和历史页面观察，不视为已确认的当前值。缓存年龄不等同财报日期或模型更新时间。各页原始显示精度、报价、参数和限制见 [alphaspread_benchmarks.json](alphaspread_benchmarks.json)。SWS 各页链接及更新日期见 [sws_benchmarks.json](sws_benchmarks.json)。

几个有效的诊断信号：XOM 本站与 Alpha Spread 相对接近；LIN 本站高于 Alpha Spread；JPM 本站显著高于两家；AMZN、NEE 在本站不可估。**差异并非都指向上调估值。** 第三方为负 TTM FCF 公司给出估值，说明存在另一类预测路径，不证明其转正假设已经验证。

### 外部来源自身也要校验

- Alpha Spread 的 [2023 年帮助文档](https://kb.alphaspread.com/hc/en-us/articles/18213235146513-What-is-Intrinsic-Value) 仍描述 DCF 与相对估值取平均；当前页面却宣布按公司选择方法。以当前具体页面识别方法，不能沿用旧说明推断。
- [NVDA 资本成本页](https://www.alphaspread.com/security/nasdaq/nvda/discount-rate) 显示 Ke 10.01%、Beta 1.23、ERP 4.3%，同时显示债务成本 **40%**、债务权重 0.6%、WACC 10.19%。40% 是待核实的合理性异常，尚不能确定是显示问题还是底层输入问题；不采用它校准本站。
- Alpha Spread 的 PLD 页本次仍标为 FCFE，并非明确的 AFFO 模板；JPM 明确为 Bank。不能声称它已对所有特殊行业选用了我们认为最适合的方法。
- 用户给出的 SWS NVDA Beta 表存在算式结果不一致：按展示输入计算约 1.6846，而结果栏是 1.705。现金流折现用反推的未四舍五入折现率可复现每股结果。这些差异应进入来源质量记录，不能隐去。

## 官方财报新增勾稽

以下单位均为十亿美元。不是把财务页全部字段当作正确答案；优先识别漏项，再确定借款、租赁、合并范围等定义。

| 公司 | 本站 DCF 债务 | 本站财务页债务 | 官方事实与结论 |
|---|---:|---:|---|
| NVDA | 1.509 | 38.860 | 短期及长期借款 1.000+32.366=33.366，DCF 借款不完整；股数 24.285 十亿对应季度稀释加权平均数。[财报](https://nvidianews.nvidia.com/news/nvidia-announces-financial-results-for-second-quarter-fiscal-2027) |
| WMT | 16.543 | 73.755 | 借款加融资租赁 57.243，另有经营租赁 16.512；DCF 存在漏项，73.755 也须与现金流的租赁处理配套。[财报](https://stock.walmart.com/sec-filings/all-sec-filings/content/0000104169-26-000145/earningsreleasefy27q2.htm) |
| UNH | 3.827 | 73.328 | 短期 3.827+长期 69.501=73.328；DCF 恰好只保留短期部分，长期债务漏计已确认。[10-Q](https://www.sec.gov/Archives/edgar/data/731766/000073176626000197/unh-20260630.htm) |
| O | 28.418 | 2.760 | 合并借款本金 30.9906，扣未摊销折价及融资费用后 30.6517，按经济权益比例计 31.1655；财务页明显不代表完整债务，DCF 也尚未勾稽。[官方补充资料第 33 页](https://www.sec.gov/Archives/edgar/data/726728/000072672826000044/realtyincomeq22026supple.htm) |

UNH 的 10-Q 披露现金与等价物约 28.6，其中约 1.1 可用于一般公司用途；同时存在保险监管资本要求。本模型加回现金及短期投资 31.468，因此不能把该合计直接理解为可分配剩余现金。**这不意味着正确修复就是只加回 1.1**，需要与保险业务价值和资本要求一并分析。UNH 季末普通股约 905 百万，而本模型采用 906 百万的季度稀释加权平均股数。[同一份 10-Q](https://www.sec.gov/Archives/edgar/data/731766/000073176626000197/unh-20260630.htm)

NVDA 当季供应商 FCF 21.400 与官方 21.341 的差额 0.059，对应设备相关本金支付，是可解释的定义差异；不要直接定性为算错。其现金输入还含可交易股权证券。现金流与资产价值如何配套必须记录。[官方 FCF 调节表](https://nvidianews.nvidia.com/news/nvidia-announces-financial-results-for-second-quarter-fiscal-2027)

四家逐项事实见 [official_reconciliations.json](official_reconciliations.json)。其余差异仍待逐份报表核验；此前 17/33 是接口差异计数，不升级为“17 家全部已确认同一种 bug”。

## 综合待解决问题列表

优先级：P0 为影响数据或模型适用性的基础问题；P1 为下一阶段必须完成的口径和验证；P2 为输入通过后再比较的设计候选。**下表优先级不代表已经开始实施，十项均为 Hold。** 原四方向：A=Beta/资本成本，B=财务输入口径，C=模型适用性，D=增长/预测期。

| ID | 优先级 / 来源方向 | 问题及已知证据 | 判断 | 后续工作与验收要求 |
|---|---|---|---|---|
| V01 | P0 / B | 债务分项不完整却优先于完整合计。NVDA、WMT、UNH 漏计已获财报支持；O 两个接口均需勾稽 | 已确认缺陷；各公司具体映射继续核查 | 建立借款、融资/经营租赁、合并/权益比例的统一字段契约；缺失不当零；分项与合计可解释勾稽，回归全部 33 家及新增样本 |
| V02 | P1 / A | Beta 零点附近跳变；11/33 因负本地 Beta 回退至 1，极小正数却直接采纳 | 已确认规则不连续；替代方法待选择 | 核查收益复权、基准、频率、窗口与覆盖；比较统一收缩/行业先验候选；零点连续、来源透明、跨时点稳定；同步记录无风险利率、ERP 和债务成本口径 |
| V03 | P1 / B | FCFF 转换与资产加回只覆盖简化关系；租赁本金、非经营收益、受限或业务必需现金、少数股东权益未形成完整桥表 | 简化实现已确认；每项金额影响待核实 | 从 CFO/资本开支/利息/税构建可追溯现金流；经营现金流与债务、租赁、非经营资产、权益归属匹配；不重复计入投资收益和资产价值；不能把 FCFE 直接用 WACC 折现 |
| V04 | P1 / B | 股数与日期的名称混用：NVDA、UNH 采用季度稀释加权平均数，却作为 outstanding 使用；NVDA 日期为供应商月末标签 | 口径标识问题已确认；估值影响逐项量化 | 区分时点股数、加权平均数、稀释估计、拆股调整和未来稀释；保留真实财报期末、标准化期间及报价日，市值与股数能解释差异 |
| V05 | P0 / C | 银行、保险、REIT 等未通过经济特征适用性判断就套用普通企业 FCFF；JPM 为市价约 6.86 倍，银行现金与利息被机械处理 | 金融业务的适用性缺口明确；REIT 方法待设计 | 根据业务特征选择权益/剩余收益、AFFO/NAV 等候选，或明确不可估；不能仅靠 sector 标签，覆盖 UNH 保险及 CAT 融资业务；合理性由资本和现金流约束验证 |
| V06 | P1 / C | 正 TTM FCFF 是现模型覆盖条件；AMZN、NEE 为负时无法表达投入期后转正 | 已确认覆盖边界，不是折现公式算错 | 明确区分缺数据、当前负现金流、预测仍不可支撑；如增加经营预测，须包含资本开支、营运资本、融资与转正路径；证据不足允许继续不可估 |
| V07 | P2 / D | FCF/收入历史增长直接混合，20% 上限及截尾会改变中心值和情景宽度；全样本仅 3 家触发上限 | 设计选择待评估，不能直接判错 | 锁定少量统一候选：历史情景、分析师逐年、收入/利润率/再投资预测；记录预测人数与更新时间；测试周期正常化、缺预测 fallback；禁止逐 ticker 拟合目标价 |
| V08 | P2 / D 延伸 | 第 5 年后立即转永续；终值、长期利润率/再投资与风险没有完整联动。Alpha Spread 混用退出倍数与永续终值 | 模型设计与解释任务，非已证实 bug | 比较统一预测期与渐变规则；显示终值现值占比，校验长期增长/再投资/回报率关系；退出倍数仅作明确标注的情景或交叉检查，不为复制 Alpha Spread 强行采用 |
| V09 | P1 / 新增共性 | 新旧估值接口并存：NVDA 旧接口 113.20、新接口 78.28；AMZN、NEE 旧接口输出 0，新接口是 unavailable | 已确认 API 语义与版本分歧；未发现主驾驶舱仍展示旧 DCF | 明确唯一默认引擎，或强制标明不同模型版本与假设；不可估不返回零价值；检查调用方及导出。区分内在价值、相对倍数、分析师目标价、上涨空间与折价分母 |
| V10 | P1 / 新增验证约束 | 跨平台未统一模型/日期/预测覆盖，第三方也存在异常；现有 33 家只是诊断样本 | 已确认本轮证据限制，不是新的生产算术 bug | 为比较值记录模型、来源和 as-of；先验证财报事实与公式，再另锁股票/历史时点做留出验证。使用当时可得数据，比较未来实现现金流、情景覆盖及稳定性；不以最贴近市价或第三方为评分目标 |

V03 中“投资收益与资产价值是否重复计量”仍是待审项目，不能由持有投资这一事实直接判定某家公司已重复计量。V04 也不否定稀释股数作为估值近似，要求明确它的定义和时点。

V05 的依据不仅是估值很高：银行负债、利息与投资资产的经营含义，本身不同于普通企业融资及剩余现金。[Damodaran 金融公司估值](https://pages.stern.nyu.edu/~adamodar/pdfiles/papers/finfirm09.pdf)、[SWS 模型说明](https://github.com/SimplyWallSt/Company-Analysis-Model/blob/master/MODEL.markdown#value)。

## 后续实施依赖与本轮已完成事项

恢复实施时建议顺序：V01 数据完整性与 V05 适用性 → V02–V04、V09 口径和输出 → V06–V08 预测候选。V10 的验证设计先锁定，最终发布前执行。每次只更改一个可归因的层级，保留旧快照；不要同时改债务、Beta、增长和终值后只观察价格是否更接近。

前轮已独立复算 31 个可计算输出，精度通过，未发现当前驾驶舱的基础折现算术错误。现有页面已有三情景、反向 DCF、WACC/终值敏感性和同业倍数分布；不将这些误列为缺失功能。此轮没有运行全套应用测试，因为没有生产代码变更；只验证新增证据文件及清单引用。

新增保存：11 家 Alpha Spread 可见输入摘要、4 家官方勾稽以及本清单。Alpha Spread 的逐年模型、相对估值权重，以及全部 33 家官方逐项勾稽尚未完成，已在证据边界和待办中明确保留，不声称完成全市场或历史有效性验证。

实现位置：债务/股数提取在 [decision_support.py](../../services/decision_support.py) 的 `_statement_point`；FCFF 折现与股权桥在同文件 `calculate_dcf_value`；资本成本与增长规则在 [valuation_assumptions.py](../../services/valuation_assumptions.py)；旧引擎在 [analyzer.py](../../services/analyzer.py)；现有展示在 [DecisionCockpit.tsx](../../frontend/src/components/DecisionCockpit.tsx)。
