# 基金三层分析模型（Fund Analysis Model: L1/L2/L3）

> 文档编号：ARCH-FUNDMODEL-001
> 版本：v1.0
> 生成日期：2026-08-01
> 对应需求：补充约束 Prompt 第十一节「场外基金必须分三层分析」
> 关联文档：`docs/V46_REQUIREMENTS_TRACEABILITY.md`、`docs/ASSET_PERMISSION_AND_RISK_CLUSTERS.md`、`docs/ARCHITECTURE_INFLUENCE_MATRIX.md`

---

## 0. 本文档的作用

场外基金不是一只「价格便宜了就买」的股票。对它的分析必须**同时**在三个层面进行：它自己涨了多少（L1）、它实际上持有什么（L2）、以及我想做的操作在申赎规则下能不能做、多久能做到（L3）。

把三层混为一谈会产生两类典型错误：**只看 L1 会把持仓高度重合的两只基金当成分散化**；**忽略 L3 会产出根本无法执行的调仓计划**。

本文档定义三层的边界与交互规则，并诚实评估三层各自的实现程度。核心结论：**L1 完整落地并在生产运行，L3 部分落地（关键的时序与费率阶梯建模缺失），L2 完全不存在（`NOT_FOUND`）。**

---

## 1. 目标设计：三层模型

### 1.1 层次定义

| 层 | 名称 | 回答的问题 | 输入 | 典型产出 |
|---|---|---|---|---|
| **L1** | 净值层 | 这只基金自己表现如何？ | NAV 序列 | 收益、回撤、波动、动量排名 |
| **L2** | 持仓/底层层 | 它实际上持有什么？ | 定期报告持仓、行业分布、集中度 | 真实风险暴露、基金间重合度 |
| **L3** | 交易机制层 | 我想做的操作能不能做、多久能做到？ | 申赎规则、费率、限额、日历 | 可执行性判定、执行时序、真实成本 |

### 1.2 L1 净值层：能力与固有局限

NAV 序列是**唯一稳定可得**的基金数据，L1 因此是三层中最容易做的一层。但它有三个**不可消除**的局限，任何 L1 结论都必须带着这三条一起读：

| 局限 | 说明 | 后果 |
|---|---|---|
| **T+1 滞后** | T 日的 NAV 在 T+1 才公布 | 所有 L1 信号天然滞后至少一天，不存在「盘中信号」 |
| **只有单点，无 OHLCV** | 每日一个净值，没有开高低收和成交量 | 任何需要日内波幅、成交量、跳空的指标都**不可计算**，不能用收盘价类模型硬套 |
| **净值口径混淆风险** | 单位净值 / 累计净值 / 复权净值，分红时会分叉 | 口径不一致会凭空造出一个「暴跌日」 |

**L1 硬规则**：
- L1 只能回答「表现如何」，**不能**回答「风险来自哪里」——后者是 L2 的职责；
- 把 NAV 当作 `close` 复用价格类算法是一种**兼容性妥协**，必须显式标注，不得让下游误以为拿到的是真实行情；
- 净值口径必须显式声明并校验，不允许推断。

### 1.3 L2 持仓/底层层：为什么它不可省略

**两只名字完全不同的基金，可能持有几乎相同的股票。** 只看 L1，它们的 NAV 相关性在震荡期可能只有 0.6，看起来"可以同时持有"；但它们的风险来源是同一批底层资产，压力期会同时下跌。

L2 必须产出的四类事实：

| 事实 | 用途 |
|---|---|
| **行业/主题分布** | 判定风险簇归属（见 `docs/ASSET_PERMISSION_AND_RISK_CLUSTERS.md` §2） |
| **持仓集中度** | 前十大占比过高 → 单票风险实质上是基金风险 |
| **基金间重合度** | 两只基金持仓 Jaccard/权重重合度 → 识别伪分散化 |
| **风格漂移** | 声明的投资方向与实际持仓不符 → 簇归属过期，需人工复核 |

**L2 硬规则**：
- L2 数据**天然低频且滞后**（季报持仓滞后一个季度），必须显式标注数据日期与陈旧度，**不得**当作实时事实；
- L2 缺失时的正确态度是「承认不知道」，即把该基金的簇归属标为 `UNKNOWN` 并**收紧**约束，而不是默认它没有集中风险；
- L2 是风险簇归属的**证据来源**，但不能自动改写簇归属（自动改写会让人工先验约束被数据噪声侵蚀）。

### 1.4 L3 交易机制层：约束本身就是策略的一部分

场外基金的申赎规则不是「执行细节」，它直接决定策略的可行域。

| 机制 | 内容 | 对策略的约束 |
|---|---|---|
| **T+1 未知价** | 提交时不知道成交净值 | **绝不能**用信号日净值反推份额；回测按当日收盘价成交是系统性乐观 |
| **确认 T+1 / 到账 T+2** | 净值 T+1 公布，份额 T+2 确认 | 「今天卖 A 买 B」实际上是跨 3-4 个工作日的过程，中间存在敞口空窗 |
| **赎回费阶梯** | 费率随持有期分档（如 <7 天 1.5%、7-30 天 0.5%、>30 天 0）| 高频调仓在惩罚性费率下必然亏损；换手必须与持有期一起决策 |
| **限购** | 单日/单账户申购上限 | 目标权重可能根本无法一次性达到，需要分批 |
| **QDII 额度** | 外汇额度耗尽时暂停申购 | 跨境标的可能**长期**无法建仓，不是延迟而是不可用 |

**L3 硬规则**：
- **未知即不可执行**：申赎状态、费率任一为 `UNKNOWN`，必须判定为不可执行，**不得**默认为「开放/零费率」；
- 执行计划必须携带**时序**，而不只是目标权重差；
- L3 约束必须在**生成计划之前**校验，而不是生成之后再解释为什么执行不了。

### 1.5 三层的交互规则

| # | 规则 | 违反后果 |
|---|---|---|
| **FM-1** | 任何调仓决策必须同时通过三层检查，缺任一层即降级为「研究参考」，不产出可执行计划 | 产出无法执行或风险错判的计划 |
| **FM-2** | **L1 的分散化结论必须由 L2 复核**：L1 低相关 + L2 高重合 = 伪分散化，以 L2 为准 | 把同一份风险买了两遍 |
| **FM-3** | **L3 是否决层，不是调整层**：L3 不可执行时，正确动作是**不生成计划**，而不是生成一个「大概能执行」的计划 | 用户按无效计划操作，产生非预期成交 |
| **FM-4** | 三层的数据陈旧度必须**分别**标注：L1 可能是昨天的，L2 可能是上季度的，L3 可能是今天核实的。混在一起报「数据已更新」是误导 | 用季度前的持仓判断今天的风险 |

---

## 2. 当前实现现状（诚实盘点）

### 2.1 L1 净值层

| 能力项 | 现状 | 证据(文件:行号) | 状态标签 |
|---|---|---|---|
| NAV 数据获取与仓储 | 自动刷新 + 仓储读取，生产在跑 | `quant/fund_pipeline.py:147-153`（`FundNAVRepository` + `auto_refresh_direct_fund_navs`）、`:159-161` | `TESTED_AND_WIRED` |
| NAV 质量闸门 | 校验最小历史、陈旧度、单日异常收益、净值口径、直连代码匹配 | `quant/fund_pipeline.py:162-180`（`fund_nav_quality_gate` 全部入参） | `TESTED_AND_WIRED` |
| 净值口径显式声明 | `nav_basis_required` 逐标的声明并强制校验，不推断 | `quant/fund_pipeline.py:173`；配置 `config/fund/instruments.json` 各条 `nav_basis_required: "cumulative_nav"` | `TESTED_AND_WIRED` |
| 数据失效分级隔离 | 基准失效 → 全局阻断；非基准失效 → 单标的隔离并冻结持仓，不据坏数据赎回 | `quant/fund_pipeline.py:38-59`（`classify_fund_quality_failures`）、`:91-123`（`freeze_quarantined_fund_holdings`） | `TESTED_AND_WIRED` |
| 多日历对齐 | 只对**可用**序列取共同信号日，隔离序列不参与交集 | `quant/fund_pipeline.py:62-88`（`align_fund_frames_to_common_signal_date`） | `TESTED_AND_WIRED` |
| 对齐策略的已知代价 | `common_date = min(latest_dates)` 仍会把全体截断到最慢者（QDII 拖慢国内基金） | `quant/fund_pipeline.py:84` | `PARTIAL`（对应 `ARCHITECTURE_INFLUENCE_MATRIX.md` §8.2 GAP-5） |
| 基于 NAV 的 regime 判定 | 生产使用 `fund_nav_regime` + HMM 影子对照 | `quant/fund_pipeline.py:196-197` | `TESTED_AND_WIRED` |
| 信号生成与信心评估 | 动量/趋势质量/MA120/流动性 → eligible；两日稳定性 + 排名领先幅度 → 信心 | `quant/fund_pipeline.py:200-202`；`quant/guard.py:47-77` | `TESTED_AND_WIRED` |
| 配权（下行波动倒数 / HRP 挑战者） | 双轨 champion/challenger | `quant/fund_pipeline.py:213`、`:217` | `TESTED_AND_WIRED` |
| **NAV 被当作 close 复用** | `nav_frames_as_close` 把 `nav` 列**重命名**为 `close` 供相关性/HRP 复用；函数 docstring 已诚实标注「仅含 NAV 派生值」 | `quant/fund_features.py:63-70`（`:69` `rename(columns={"nav": "close"})`），经 `quant/fund_pipeline.py:204` 调用 | `PARTIAL` |
| OHLCV 缺失的下游影响是否显式拦截 | **否**。改名为 `close` 后，下游无法区分「真实收盘价」与「NAV 冒充的收盘价」；没有任何守卫阻止有人对该列施加日内/成交量类指标 | `quant/fund_features.py:69` 只改列名，未加来源标记字段 | `PARTIAL` |
| T+1 滞后是否被显式建模 | **部分**。信号基于已公布 NAV（隐含 T+1），但代码中无显式的「信号滞后天数」字段供下游推理 | `quant/fund_pipeline.py:189`（`common_signal_date`）为最接近的记录；无 lag 字段 | `PARTIAL` |

**L1 小结**：三层中唯一**完整落地且在生产运行**的一层。主要残留问题是 NAV→close 的类型信息丢失（`quant/fund_features.py:69`）与多日历对齐仍取最慢者（`quant/fund_pipeline.py:84`）。

### 2.2 L2 持仓/底层层

| 能力项 | 现状 | 证据(文件:行号) | 状态标签 |
|---|---|---|---|
| 基金持仓数据获取 | **完全不存在**。全仓库无任何持仓抓取/存储/解析代码 | grep `holdings_penetrat\|penetrat\|underlying_holding\|fund_holding\|top_ten\|重仓\|穿透` → 无任何实现命中（仅 `quant/assets/risk_clusters.py:149` 一句「非精确穿透计算」的**免责说明**，以及 `quant/fund_pipeline.py:91,224,227` 中与持仓穿透无关的 `freeze_quarantined_fund_holdings`） | `NOT_FOUND` |
| 底层资产穿透 | 不存在 | 同上 | `NOT_FOUND` |
| 行业/风格分布计算 | 不存在。行业标签是**人工声明**的，不是从持仓算出来的 | `config/fund/instruments.json` 各条仅有 `asset_class` / `group` 两个手填字段 | `NOT_FOUND` |
| 持仓集中度指标 | 不存在 | 无实现 | `NOT_FOUND` |
| 基金间持仓重合度 | 不存在 | 无实现 | `NOT_FOUND` |
| 风格漂移检测 | 不存在 | 无实现 | `NOT_FOUND` |
| 现有的「近似 L2」替代物 | 风险簇归属（人工声明，15 个簇），本身是正确的先验设计 | `config/risk/risk_clusters.json`；`quant/assets/risk_clusters.py:57-179` | `TESTED_BUT_NO_PROD_CALLER` |
| 该替代物是否在生产链上 | **否**。`quant/fund_pipeline.py:7-30` 的 import 清单不含 `quant.assets.risk_clusters` | `quant/fund_pipeline.py:7-30` | `TESTED_BUT_NO_PROD_CALLER` |
| 生产中实际的「分散化」依据 | **只有 L1 相关性**：选池阶段按 `max_pair_correlation` 剔除高相关标的 | `quant/fund_pipeline.py:205-211`（`select_assets(max_pair_corr=...)`） | `TESTED_AND_WIRED` |
| 上述依据是否足以防止伪分散化 | **否**。这正是 FM-2 要防的情形：两只持仓高度重合的基金，若 NAV 相关性低于阈值，会被判为「可同时持有」。系统**没有任何数据**可以发现它们其实是同一份风险 | `quant/fund_pipeline.py:210`；且无任何持仓数据源 | `BLOCKED` |
| 生产中的行业集中度约束 | 单一聚合桶：所有 `asset_class == "equity_sector"` 权重加总设一个上限 | `quant/portfolio.py:146-160`，经 `quant/fund_pipeline.py:215,219` 调用 | `TESTED_AND_WIRED` |
| 该约束是否为 L2 能力 | **否**。它基于**人工标签**而非实际持仓；且只有一个桶，无法区分不同主题 | `quant/portfolio.py:148-152` | `PARTIAL` |

**L2 小结**：**整层不存在。** 系统当前没有任何一个字节的基金实际持仓数据，因此**结构上无法**判断两只基金是否持有同一批底层资产。这是三层模型中最大的空洞。

### 2.3 L3 交易机制层

| 能力项 | 现状 | 证据(文件:行号) | 状态标签 |
|---|---|---|---|
| 申赎状态校验 | 申购/赎回状态逐标的检查；`UNKNOWN` 按不可执行处理（Fail-Closed） | `quant/fund_execution.py:60-62`（`_status_open`）、`:113-114`（申购）、`:121-122`（赎回） | `TESTED_AND_WIRED` |
| 费率核实强制要求 | `require_fee_verification` 下，未核实费率即判不可执行 | `quant/fund_execution.py:116-117`、`:124-125` | `TESTED_AND_WIRED` |
| 快照同日要求 | 申赎/费率快照日期必须等于决策日，否则报不匹配 | `quant/fund_execution.py:93-94` | `TESTED_AND_WIRED` |
| 未知价原则（不反推份额） | 基金订单按金额下单，`estimated_quantity` 恒为 0，当日 NAV 不参与份额估算 | `quant/exe/order_factory.py:40-49`（docstring 明述）、`:54`（注释）、`:59`（`estimated_quantity=0.0`） | `IMPLEMENTED_UNTESTED` |
| L3 是否为否决层（FM-3） | **是，已正确实现**。不可执行时进入 `BLOCKED_BY_TRADEABILITY` 并把决策降为 `HOLD_RECHECK`，**不生成计划** | `quant/fund_pipeline.py:328-335` | `TESTED_AND_WIRED` |
| 账户映射未确认时的拦截 | `mapping_confirmed = False` 时明确标注「仅研究参考，不生成真实申赎指令」 | `quant/fund_pipeline.py:294`、`:305-315` | `TESTED_AND_WIRED` |
| 复核节奏（受 regime/信心驱动） | 1 / 3 / 10 / 20 个新增 NAV 观测的分档 | `quant/fund_schedule.py:19-32`，经 `quant/fund_pipeline.py:237-239`、`:360-362` 调用 | `TESTED_AND_WIRED` |
| 换手容忍带 | 偏离未超容忍带则不申赎，避免为净值漂移交易 | `quant/fund_pipeline.py:288-289` | `TESTED_AND_WIRED` |
| 分批建仓（staging） | 超过单次最大换手时分批推进 | `quant/fund_pipeline.py:262-263`（`stage_target_weights`） | `TESTED_AND_WIRED` |
| **确认 T+1 / 到账 T+2 时序建模** | **生产链中不存在**。`quant/fund_pipeline.py` 产出的是目标权重与计划行，**没有任何状态跟踪跨日订单** | `quant/fund_pipeline.py:396-408` 只写出 `trade_plan`，无订单生命周期 | `NOT_FOUND` |
| 11 态订单状态机 | 定义完整：11 个状态语义精确建模「未知价 → T+1 净值 → T+2 份额确认」 | `quant/fund_orders/states.py:33-67`（状态定义）、`:130-137`（`UNKNOWN_NAV_STATES`）、`:140`（`POSITION_EFFECTIVE_STATE`） | `IMPLEMENTED_UNTESTED` |
| 状态迁移表 | 集中建表，禁止跳态；撤单只允许在净值截止前 | `quant/fund_orders/states.py:78-120` | `IMPLEMENTED_UNTESTED` |
| 迁移校验是否 Fail-Closed | **是，且无 Fail-Open 开关**。非法迁移直接抛 `FundOrderTransitionError`，无 `throwOnInvalid=False` 之类的旁路参数 | `quant/fund_orders/states.py:157-169`（`validate_transition`），对比 `ARCHITECTURE_INFLUENCE_MATRIX.md` §2.3 S-X1 | `IMPLEMENTED_UNTESTED` |
| **状态机是否接入生产** | **否**。全仓库对 `quant.fund_orders.*` 的引用只有 `app/v46_acceptance.py:111-158`（验收脚本）与 `tests/test_fund_order_state_machine.py:20`。`quant/fund_pipeline.py:7-30` 完全不引用 | grep `fund_orders` 结果 | `TESTED_BUT_NO_PROD_CALLER` |
| 状态是否持久化 | **否**。状态机只有枚举与纯函数，无任何存储；生产的 `state/*.json` 中也无订单状态字段 | `quant/fund_orders/states.py` 全文无 I/O；`state/fund_portfolio_state.json` 无订单段 | `NOT_FOUND` |
| **赎回费阶梯（按持有期分档）** | **不存在**。执行校验只读**单一** `redemption_fee_rate` 标量，无任何持有期分档逻辑 | `quant/fund_execution.py:123`（`_rate(snapshot.get("redemption_fee_rate"))`）——单值，无 ladder | `NOT_FOUND` |
| 持有期数据 | 字段存在（`holding_days_by_symbol`）但生产状态文件中为**空对象** | `quant/fund_account_setup.py:67,107,147`（计算逻辑）；`state/fund_portfolio_state.json:9`（`"holding_days_by_symbol": {}`） | `PARTIAL` |
| 持有期是否参与费率计算 | **否，且是刻意的**：配置明确声明「不自动猜测赎回费，只生成计划并提示人工核对」 | `config/fund/settings.json:53`（`c_class_holding_days_note`） | `PARTIAL`（保守但缺失） |
| **限购/申购额度** | **不存在**。快照 schema 中无任何单日或单账户申购上限字段 | `quant/fund_execution.py:107-127` 读取的字段仅 `purchase_status` / `redemption_status` / `subscription_fee_rate` / `redemption_fee_rate` / `fee_verified` | `NOT_FOUND` |
| **QDII 外汇额度** | **不存在**。全仓库 `qdii` 仅出现在资产名称/类型字符串中（`config/fund/instruments.json:58,69`、`config/assets/asset_master.json:76,88,90`），无任何额度逻辑 | grep `qdii\|quota\|限购\|purchase_limit\|daily_limit` → 无实现命中 | `NOT_FOUND` |
| 申赎快照的实际内容 | **空模板**：`as_of_date: null`、`funds: {}`。即当前**没有任何标的**具备可执行性数据 | `state/fund_tradeability_snapshot.json`（`"as_of_date": null`、`"funds": {}`） | `BLOCKED` |
| 上述空快照的后果 | 任一标的一旦进入调仓候选，`quant/fund_execution.py:108-110` 立即报「缺少当日申赎状态与费率快照」→ 系统进入 `HOLD_RECHECK`。**这是安全的失败方式，但意味着 L3 目前实质上处于「永远不可执行」状态** | `quant/fund_execution.py:108-110`；`quant/fund_pipeline.py:329-335` | `BLOCKED` |

**L3 小结**：**判定逻辑（能不能做）已正确实现且 Fail-Closed，时序逻辑（多久能做到）完全缺失。** 精心设计的 11 态状态机（`quant/fund_orders/states.py:33-169`）只服务于验收脚本和测试，生产链一次都没调用它。

### 2.4 三层交互规则的落地情况

| 规则 | 现状 | 证据 | 状态标签 |
|---|---|---|---|
| FM-1 三层齐备才产出可执行计划 | 只有 L1+L3 参与；L2 缺席但**不触发降级**——系统不知道自己少了一层 | `quant/fund_pipeline.py:295-337` 的 gate 链中无 L2 检查项 | `PARTIAL` |
| FM-2 L2 复核 L1 的分散化结论 | 无法执行，L2 不存在 | §2.2 全表 | `NOT_FOUND` |
| FM-3 L3 作为否决层 | **已正确实现** | `quant/fund_pipeline.py:328-335` | `TESTED_AND_WIRED` |
| FM-4 三层陈旧度分别标注 | L1 有（`common_signal_date`、质量闸门陈旧度）；L3 有（`tradeability_snapshot_date`）；L2 无（不存在） | `quant/fund_pipeline.py:420`（`tradeability_snapshot_date`）、`:435`（`common_signal_date`） | `PARTIAL` |

---

## 3. 差距与落地计划

| 优先级 | 任务 | 层 | 依赖 | 交付物 |
|---|---|---|---|---|
| **P0** | **填充申赎快照**：`state/fund_tradeability_snapshot.json` 当前 `funds: {}`，须建立人工核实 runbook 并落地至少 6 只在册基金的状态与费率 | L3 | 无 | 可执行性数据存在 |
| **P0** | **NAV→close 加来源标记**：`quant/fund_features.py:63-70` 输出增加 `price_source: "NAV_DERIVED"` 列或包装类型，使下游无法把它误当真实行情 | L1 | 无 | 类型信息不丢失 |
| **P0** | **显式记录信号滞后**：把 L1 信号的 `nav_lag_days` 写入 run 快照，供 L3 计算真实执行时点 | L1 | 无 | 滞后可推理 |
| **P1** | **接通 11 态状态机**：让 `quant/fund_pipeline.py:396-408` 产出的计划进入 `quant/fund_orders` 生命周期，而不是止于一张 CSV | L3 | P0 | 跨日订单可跟踪 |
| **P1** | **订单状态持久化**：为 11 态状态机增加存储（`state/fund_orders.json` 或 SQLite），使 `validate_transition`（`quant/fund_orders/states.py:157-169`）的 Fail-Closed 真正生效于跨运行场景 | L3 | 上一条 | 状态跨运行存活 |
| **P1** | **建模确认时序**：把 T+1 净值、T+2 份额确认写成显式日期字段，计划中给出「预计确认日 / 预计到账日」 | L3 | P1 | 计划带时序 |
| **P2** | **赎回费阶梯**：把 `quant/fund_execution.py:123` 的单值费率扩展为按持有期分档的 ladder，输入取 `holding_days_by_symbol`（`state/fund_portfolio_state.json:9`，需先填充） | L3 | P0 | 真实成本可算 |
| **P2** | **限购与 QDII 额度字段**：申赎快照 schema 增加 `daily_purchase_limit` / `quota_available`，缺失时按不可执行处理 | L3 | P0 | 额度约束生效 |
| **P2** | **引入基金持仓数据源**（L2 的地基）：定期报告持仓抓取 + 存储 + 陈旧度标注。**必须**显式标注数据日期，季报滞后不得隐藏 | L2 | 无 | L2 有数据 |
| **P2** | **基金间重合度计算**：基于持仓计算重合度，接入选池，作为 `max_pair_correlation` 的**补充否决**（不是替代） | L2 | 上一条 | 伪分散化可识别 |
| **P3** | **持仓集中度与风格漂移检测**：前十大占比告警；声明簇归属与实际持仓不符时产出人工复核工单（**不自动改写**簇归属） | L2 | P2 | 漂移可发现 |
| **P3** | **FM-1 降级机制**：L2 数据缺失或过期时，把该标的簇归属标为 `UNKNOWN` 并**收紧**约束，同时在决策报告显式声明「本次决策缺少 L2 证据」 | L1+L2+L3 | P2 | 缺层不再静默 |
| **P4** | **回测的三层真实性**：回测必须同时建模 T+1 未知价、T+2 确认、费率阶梯（对应 `ARCHITECTURE_INFLUENCE_MATRIX.md` §3.3 T-X2、§4.3 Q-X2） | 全部 | P1+P2 | 回测不再系统性乐观 |

---

## 4. 验收方式

### 4.1 L1 净值层

- 给定某标的最新 NAV 日期距决策日超过 `max_staleness_calendar_days`，当运行 `run_fund_daily`，则该标的被隔离、不进入排名、不新增申购，且**不因坏数据被赎回**（持仓冻结）。
- 给定基准标的数据失效，当运行流程，则**全局**阻断，regime 记为不可用，不强行产出市场状态。
- 给定 L1 输出的价格序列，当下游检查其来源标记，则能识别出这是 NAV 派生值而非真实收盘价；**当前状态：识别不出**（`quant/fund_features.py:69` 仅重命名列）。
- 给定任意 L1 信号，当读取 run 快照，则能查到该信号对应的 NAV 观测日与滞后天数。

### 4.2 L2 持仓/底层层

- 给定两只 NAV 相关性低于 `max_pair_correlation` 但底层持仓重合度 > 70% 的基金，当运行选池，则**不得**同时纳入；且拒绝原因明确写「持仓重合，非真实分散」。**当前状态：必然失败**——系统无持仓数据，二者会被同时纳入。
- 给定某基金的持仓数据日期早于阈值（如超过两个季度），当计算其风险暴露，则标注为 `STALE` 并**收紧**其可用权重上限，而非按无风险处理。
- 给定某基金实际持仓与其声明的风险簇归属显著不符，当运行漂移检测，则产出人工复核工单，且**不自动修改**簇归属。
- 给定任意一次决策，当读取决策报告，则能查到本次决策使用的 L2 数据日期；若无 L2 数据，则报告中显式出现「缺少 L2 证据」声明。

### 4.3 L3 交易机制层

- 给定某标的申赎状态为 `UNKNOWN`，当校验执行约束，则判定为**不可执行**（不得默认为开放），决策降级为 `HOLD_RECHECK`，且**不写出**交易计划文件。
- 给定申赎/费率快照日期不等于决策日，当校验执行约束，则报快照日期不匹配并阻断。
- 给定一笔基金订单，当其状态为 `DRAFT`，则**不能**直接迁移到 `CONFIRMED`；必须依次经过 `PENDING_REVIEW → APPROVED → SUBMITTED_MANUALLY → WAITING_NAV → WAITING_SHARE_CONFIRMATION`，任何跳态抛出 `FundOrderTransitionError`。
- 给定状态机的任意校验入口，当扫描其签名，则**不存在**任何形如 `throw_on_invalid=False` 的 Fail-Open 旁路参数（对照 `ARCHITECTURE_INFLUENCE_MATRIX.md` §2.3 S-X1 反面教材）。
- 给定进程在订单处于 `WAITING_NAV` 时重启，当重启后读取该订单，则状态仍为 `WAITING_NAV`。**当前状态：必然失败**——状态未持久化。
- 给定某持仓持有期为 5 天且该基金 7 日内赎回费为 1.5%，当估算赎回成本，则使用 1.5% 而非默认档。**当前状态：必然失败**——无费率阶梯（`quant/fund_execution.py:123` 为单值）。
- 给定某 QDII 标的外汇额度不可用，当生成申购计划，则被拒绝并说明是额度限制而非价格/信号问题。**当前状态：必然失败**——无额度字段。

### 4.4 三层交互

- 给定一次完整运行，当读取决策报告，则 L1 / L2 / L3 的数据日期**分别**列出，不合并为单一「数据已更新」表述。
- 给定 L2 数据完全缺失，当产出调仓建议，则该建议被标记为「研究参考」而**不是**可执行计划（FM-1）。**当前状态：必然失败**——L2 缺席不触发任何降级。
- 给定 L3 判定不可执行，当检查输出目录，则**不存在**对应日期的交易计划文件（FM-3）。

---

## 5. 总体结论

| 层 | 落地程度 | 一句话结论 |
|---|---|---|
| **L1 净值层** | ~85% | 三层中唯一完整跑在生产上的；残留问题是 NAV 伪装成 close 后类型信息丢失 |
| **L2 持仓/底层层** | **0%** | **整层不存在**。系统结构上无法识别两只基金是否持有同一批底层资产 |
| **L3 交易机制层** | ~45% | 「能不能做」的判定正确且 Fail-Closed；「多久能做到」完全缺失；11 态状态机写得很好但生产一次都没调用 |

**最严重的单点缺陷：L2 整层缺失，导致伪分散化在结构上不可检测。** 系统会把两只持仓高度重合的基金当作分散化配置，而唯一的判据（NAV 相关性，`quant/fund_pipeline.py:210`）恰好会在压力期失效。

---

## 6. 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-08-01 | v1.0 | 初版。定义 L1/L2/L3 三层边界与 FM-1~FM-4 交互规则；核实 L1 在生产完整运行（`quant/fund_pipeline.py:147-220`）但 NAV→close 类型信息丢失（`quant/fund_features.py:63-70`）；核实 L2 全层 `NOT_FOUND`（无任何持仓数据源）；核实 L3 判定层 Fail-Closed 已生效（`quant/fund_execution.py:60-131`、`quant/fund_pipeline.py:328-335`）但 11 态状态机（`quant/fund_orders/states.py:33-169`）仅被 `app/v46_acceptance.py:111-158` 与测试调用、状态未持久化、赎回费阶梯/限购/QDII 额度均 `NOT_FOUND`；核实生产申赎快照 `state/fund_tradeability_snapshot.json` 为空模板 |
