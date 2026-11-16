# V4.6 需求追踪矩阵（Requirements Traceability Matrix）

> 文档编号：REQ-TRACE-001
> 版本：v1.5
> 生成日期：2026-08-01
> 对应需求：补充约束 Prompt 第一节「建立需求、代码、测试和验收结果之间的可追踪关系」
> 关联文档：`docs/ARCHITECTURE_INFLUENCE_MATRIX.md`、`docs/UNFROZEN_PARAMETERS.md`

---

## 0. 使用说明

### 0.1 本文档存在的唯一理由

防止「目录建好了、类名写好了、测试也过了，但真实运行时一行都没执行到」。

因此本矩阵的判定规则比常规严格：**代码存在 ≠ 已实现**，**测试通过 ≠ 已落地**。只有当**生产调用链**上真的会走到这段代码，才允许标 `TESTED`。

### 0.2 状态取值（只允许这 6 个）

| 状态 | 判定标准 |
|---|---|
| `NOT_FOUND` | 全仓库 grep 不到对应实现，能力不存在 |
| `PARTIAL` | 有部分实现，但关键约束缺失（如有类无校验、有校验无调用） |
| `IMPLEMENTED_UNTESTED` | 代码写了，无对应测试 |
| `TESTED` | 有测试且通过，**且生产调用链上确实会执行到** |
| `BLOCKED` | 被外部条件卡住（缺数据源、缺硬件、依赖未定） |
| `DEFERRED_BY_SCOPE` | 明确超出 V4.6 范围，登记备查 |

> **关键补充**：审计发现大量模块处于「有测试、但生产零调用方」的状态。这类**一律不得标 `TESTED`**，统一标 `PARTIAL`，并在「未完成原因」中写明 `生产零调用方`。这是本矩阵与上一版验收报告最大的差异，也是上一版 21/21 验收未能发现问题的根因。

### 0.3 轴标注（补充 Prompt 第四节要求）

| 标注 | 含义 |
|---|---|
| `ENG` | 工程轴 —— 账户事实、状态机、数据身份、守卫、可观测性 |
| `STR` | 策略轴 —— 选品、择时、配权等交易逻辑 |
| `BOTH` | 两轴都涉及 |

### 0.4 优先级（补充 Prompt 第二十六节）

`P0` 账户与数据事实 → `P1` 连续运行与市场感知 → `P2` 风险与组合 → `P3` 研究基础设施 → `P4` 未来扩展

---

## 1. 总览统计

| 状态 | 条数 | 占比 |
|---|---:|---:|
| `TESTED`（真正落地） | **17** | **27%** |
| `PARTIAL` | 25 | 40% |
| `NOT_FOUND` | 16 | 25% |
| `IMPLEMENTED_UNTESTED` | 2 | 3% |
| `DEFERRED_BY_SCOPE` | 3 | 5% |
| **合计** | **63** | 100% |

> **对外完成度 = 27%**（本表 `TESTED` 口径；由 12/63 ≈ 19% 升至 **17/63 ≈ 27%**）。这是 2026-08-02「全打接线」指令的直接成果——
> 此前 6 个治理模块「有实现、有测试、**生产零调用方**」，按 §0.2 判定规则只能标 `PARTIAL`；本轮把
> REQ-DATA-03 / REQ-DATA-04 / REQ-STATE-03 / REQ-RUN-03 / REQ-BT-01 共 5 条的生产调用点真正接上，落地率随之抬升。
> **关键方法论：能力长出来之后，只有「接线」才会抬升 TESTED；再写新模块不会。**
>
> 验收门机械覆盖率此刻为 **54/57 = 95%**（已实现的 54 条全绿），这是**内部工程卫生指标**且分母由我们自己定义，
> 绝不得作为完成度对外引用——裁定见 `docs/audit/V46_COMPLETION_METRIC_RULING.md`。
> 两个数字并列（95% vs 27%）本身就是提醒：**自检全绿离系统可用还很远。**

按优先级：P0 = 16 条，P1 = 17 条，P2 = 13 条，P3 = 11 条，P4 = 6 条。

> **诚实结论**：上一版宣称「21/21 验收通过、336 测试全绿」。按本矩阵口径重新核算，真正形成业务闭环的需求只有 **9/63 ≈ 14%**。差距来自两点：(1) 上一版验收条件本身只覆盖了工程轴的一部分；(2) 验收脚本检查的是「模块可导入、函数可调用」，而不是「生产路径会执行到」。

> **诚实是双向的**（2026-08-01 首次修订）：本表初版把 REQ-DATA-03、REQ-DATA-05 记为 `NOT_FOUND`，逐行复核后发现二者在**基金分支已有真实落地**，仅 ETF 分支缺失，故改判 `PARTIAL`。同时 REQ-DATA-02 的证据引用了一个**并不存在的文件** `quant/data_identity/models.py`。把已落地的能力漏记为缺失，与把未落地的说成已完成同样有害——前者会导致重复造轮子和错误排期。本表此后每一次状态变更均须在第 22 节留痕。
>
> 需要强调的是：**这次修订没有改变 `TESTED` 的 9 条**。落地率仍是 14%，被修正的是「缺口的形状」，不是「缺口的大小」。

> **第二次修订（2026-08-01，W2-1 落地后）**：`TESTED` 由 9 → 10（+1，REQ-PARAM-01），落地率 14% → **16%**。
> 这 +1 是真实的：`quant/provisional.py` 的参数登记表已接入 `app/inspect_system.py`，而后者是
> `app/run_daily.py:32-35` 的强制前置闸门——参数漂移会让每日运行直接终止，并有负向测试证明。
> 同时 REQ-PARAM-02 / REQ-PARAM-03 由 `NOT_FOUND` 升为 `PARTIAL`（机制已部分成立，但未完整）。
>
> **不要被 +2% 误导**：这只是 63 条中的 1 条。同期的生产接线审计
> （`docs/audit/V46_PRODUCTION_WIRING_AUDIT.md`）发现了更大的问题——订单生成、执行、
> 模型目标账、决策计划账**整条链在生产中不可达**，三本账只有第三本通电。缺口的真实规模没有变小。

---

## 2. 元需求：追踪与影响矩阵本身（§一、§二）

| ID | 需求说明 | 来源 | 状态 | 代码模块 | 测试 | 验收证据 | 轴 | P | 未完成原因 |
|---|---|---|---|---|---|---|---|---|---|
| REQ-META-01 | 建立需求追踪矩阵，覆盖全部补充约束条款 | §一 | `TESTED` | `docs/V46_REQUIREMENTS_TRACEABILITY.md` | 人工评审 | 本文档 | ENG | P0 | — |
| REQ-META-02 | 状态取值受限于 6 个枚举，禁止自造 | §一 | `TESTED` | 本文档 §0.2 | 人工评审 | 本文档 | ENG | P0 | — |
| REQ-META-03 | 追踪必须基于**真实调用路径**，不得凭同名类判定 | §一 | `TESTED` | 本文档 §0.2 补充规则 | 人工评审 | §8 逐条 grep 调用方 | ENG | P0 | — |
| REQ-META-04 | 建立架构影响矩阵，记录 5 个开源来源的继承关系 | §二 | `TESTED` | `docs/ARCHITECTURE_INFLUENCE_MATRIX.md` | 人工评审 | 该文档 §1-§5 | ENG | P0 | — |
| REQ-META-05 | Riskfolio-Lib 纳入为完整第 6 来源 | 用户裁定 | `TESTED` | `ARCHITECTURE_INFLUENCE_MATRIX.md` §6 | 人工评审 | 该文档 §6 | ENG | P0 | — |
| REQ-META-06 | 每条借鉴须含「明确不借鉴」清单与理由 | §二 | `TESTED` | 同上各节 `.3` 小节 | 人工评审 | 6 个来源均有 | ENG | P0 | — |

---

## 3. 系统身份与多资产（§三）

| ID | 需求说明 | 状态 | 代码模块 | 测试 | 轴 | P | 未完成原因 |
|---|---|---|---|---|---|---|---|
| REQ-ID-01 | 系统定位为「事件驱动 + 账户事实优先 + 策略可替换 + 人在回路」的长期治理系统 | `PARTIAL` | `docs/V46_ENGINEERING_SPEC.md` | — | ENG | P0 | 「策略可替换」无注册机制；「账户事实优先」被 3 处旁路破坏（见 REQ-ASSET-05） |
| REQ-ID-02 | 支持多资产类别（A 股 ETF / 场外基金 / 港股通 / QDII），各类别约束差异显式建模 | `PARTIAL` | `quant/domain/assets.py`、`config/` | `test_domain_models.py` | BOTH | P1 | 类别字段存在，但日历/确认周期/额度差异未建模，见 REQ-CAL-* |
| REQ-ID-03 | 模块化单体架构，禁止跨模块直接读写彼此私有状态 | `PARTIAL` | 全仓库 | — | ENG | P1 | `state/*.json` 被多处裸读写，无访问控制 |
| REQ-ID-04 | 默认 PAPER 模式，实盘需显式授权（Fail-Closed） | `TESTED` | `quant/observability/snapshot.py` `RunContext.mode` | `test_snapshot_run.py`、`test_safety_phase1.py` | ENG | P0 | — |
| REQ-ID-05 | 策略可替换：策略实现可通过注册表按名替换，引擎不改 | `NOT_FOUND` | — | — | STR | P3 | 无注册表机制（借鉴 TradeMaster Registry，见影响矩阵 T-1） |

---

## 4. 正交双轴（§四）

| ID | 需求说明 | 状态 | 代码模块 | 测试 | 轴 | P | 未完成原因 |
|---|---|---|---|---|---|---|---|
| REQ-AXIS-01 | 明确区分策略轴与工程轴，并形成文档 | `PARTIAL` | `docs/ORTHOGONAL_AXES.md` | — | ENG | P0 | 文档本轮补写中 |
| REQ-AXIS-02 | **验收矩阵每一条都必须标注所属轴** | `NOT_FOUND` | `app/v46_acceptance.py` | `test_v46_acceptance.py` | ENG | P0 | 现有 21 条验收条件无任何轴标注字段 |
| REQ-AXIS-03 | 工程轴升级不得以「策略表现变化」为验收依据，反之亦然 | `NOT_FOUND` | — | — | ENG | P1 | 无隔离规则 |

---

## 5. 三频率分离（§五）

| ID | 需求说明 | 状态 | 代码模块 | 测试 | 轴 | P | 未完成原因 |
|---|---|---|---|---|---|---|---|
| REQ-FREQ-01 | 区分三种运行频率：高频监控（盘中告警）/ 日频决策 / 低频再平衡 | `PARTIAL` | `app/run_*.py` 五个入口 | `test_run_daily_snapshot.py` | ENG | P1 | 入口分离了，但三者共用同一状态与同一套阈值，无频率语义 |
| REQ-FREQ-02 | 各频率有独立触发条件与独立产出，不得互相顶替 | `NOT_FOUND` | — | — | ENG | P1 | 无频率定义 |
| REQ-FREQ-03 | 低频再平衡不得被高频告警直接触发下单，须经人工确认 | `PARTIAL` | 人工确认流程存在于 runbook | — | ENG | P1 | 仅文档约定，无代码强制 |

---

## 6. 快慢双状态与状态迟滞（§六、§八、§九）

| ID | 需求说明 | 状态 | 代码模块 | 测试 | 轴 | P | 未完成原因 |
|---|---|---|---|---|---|---|---|
| REQ-STATE-01 | 定义慢状态 `MarketRegime`（5 态） | `PARTIAL` | `quant/domain/enums.py:101-107` | `test_domain_models.py` | BOTH | P1 | 枚举存在，**生产零调用方**；生产用的是 `quant/strategies/tactical_etf/engine.py` 里的旧 `Regime` |
| REQ-STATE-02 | 定义快状态 `SentinelLevel`（告警等级） | `PARTIAL` | `quant/sentinel/levels.py:26-31` | `test_market_sentinel.py` | ENG | P1 | 有 GREEN/YELLOW/ORANGE/RED，**缺 `DATA_ERROR` 档**，导致数据故障被映射为绿灯 |
| REQ-STATE-03 | **MarketRegime 与 AlertLevel 必须解耦**，不得互相覆写 | `TESTED` | `quant/regimes/state_synthesis.py`（`synthesize()`）；调用点 `quant/strategies/tactical_etf/engine.py::synthesize_production_state` | `test_state_synthesis.py`（4 条）、验收 c39、验收 c52 | ENG | P1 | 〔2026-08-02 更新〕合成规则已实现；〔2026-08-02「全打接线」〕`run_tactical_etf` 调用 `synthesize_production_state`，合成结果 `is_risk_off` 时强制将 regime 降到 RISK_OFF（c52），两轴在生产中已真正解耦并取更保守者，验收 c52 锁定 |
| REQ-STATE-04 | 快状态可立即收紧、不得立即放松（防抖动） | `NOT_FOUND` | — | — | ENG | P1 | 无非对称响应逻辑 |
| REQ-STATE-05 | 慢状态切换须带迟滞（hysteresis）与最小停留期 | `NOT_FOUND` | — | — | BOTH | P1 | 无迟滞机制，regime 可逐日翻转 |
| REQ-STATE-06 | 状态切换须记录证据链（因哪些指标、什么值而切换） | `NOT_FOUND` | — | — | ENG | P1 | 无证据结构 |
| REQ-STATE-07 | 数据缺失时状态必须判为「不可判断」，禁止静默降级为中性 | `NOT_FOUND` | `quant/strategies/tactical_etf/engine.py:74-80` | — | ENG | **P0** | **反向实现**：取数失败返回空 DataFrame，regime 静默 fallback 到 `Regime.NEUTRAL` |
| REQ-STATE-08 | 订单状态机 11 态 + Fail-Closed 迁移校验 | `PARTIAL` | `quant/fund_orders/states.py:33-67`、`:78-120`、`:157-169` | `test_fund_order_state_machine.py` | ENG | **P0** | 实现质量高（优于 StockSharp 的 Fail-Open），但**仅内存态、生产零调用方**；真实订单走 `quant/fund_execution.py` 无状态校验 |

---

## 7. 三层市场观察（§七）

| ID | 需求说明 | 状态 | 代码模块 | 测试 | 轴 | P | 未完成原因 |
|---|---|---|---|---|---|---|---|
| REQ-MKT-01 | L1 事实层：原始行情/净值只记录不解释 | `PARTIAL` | `quant/data/`、`storage/` | — | ENG | P0 | 存储存在，但无「事实层」身份标记 |
| REQ-MKT-02 | L2 指标层：指标须可追溯到 L1 的哪批数据 | `NOT_FOUND` | — | — | ENG | P0 | 无 `BuildFrom` 式溯源字段（借鉴 StockSharp S-3） |
| REQ-MKT-03 | L3 判断层：判断须能列出依据的指标与取值 | `PARTIAL` | `quant/sentinel/sentinel.py` 有 `reasons` 列表 | `test_market_sentinel.py` | ENG | P1 | 有理由文本，但不结构化、不可机读、生产未使用 |
| REQ-MKT-04 | 禁止跳层：L3 不得直接读 L1 | `NOT_FOUND` | — | — | ENG | P1 | 无分层约束，`engine.py` 直接从 repository 读到判断 |
| REQ-MKT-05 | 任一层缺数据须沿层向上传播为「不可判断」 | `NOT_FOUND` | `quant/sentinel/sentinel.py:97` | — | ENG | **P0** | **反向实现**：`indicator_level = max(scores) if scores else 0` —— 全部指标缺失时判为 0 = GREEN 绿灯 |

---

## 8. 确认预算（§十）

| ID | 需求说明 | 状态 | 代码模块 | 测试 | 轴 | P | 未完成原因 |
|---|---|---|---|---|---|---|---|
| REQ-BUDGET-01 | 引入 `ConfirmationBudget` 概念，把「试探」作为有限资源管理 | `PARTIAL` | `quant/gov/confirmation_budget.py`（2026-08-02 新增） | `test_confirmation_budget.py`（3 条）、验收 c38 | BOTH | P2 | 〔2026-08-02 由 `NOT_FOUND` 升 `PARTIAL`〕三维预算已实现：次数 / 时间窗（滑出可回补）/ 额度，任一耗尽即 Fail-Closed。**生产零调用方**，试探路径尚未接入该预算 |
| REQ-BUDGET-02 | 时间预算：待确认判断超时即失效并回落保守态 | `NOT_FOUND` | — | — | BOTH | P2 | — |
| REQ-BUDGET-03 | 次数预算：同方向试探建仓有次数上限与冷却期 | `NOT_FOUND` | — | — | BOTH | P2 | `MarketRegime.RECOVERY_PROBE` 命名上暗示探仓，但无配套预算 |
| REQ-BUDGET-04 | 额度预算：试探性仓位占总资产上限 | `PARTIAL` | `quant/port/risk_budget.py:10-13` | `test_portfolio_decision.py` | STR | P2 | `RiskBudget` 只有额度维度，无时间/次数维度；且生产零调用方 |
| REQ-BUDGET-05 | 预算消耗须留痕（哪次运行、消耗多少、为何消耗） | `NOT_FOUND` | — | — | ENG | P2 | — |

---

## 9. 资产权限与风险簇（§十一）

| ID | 需求说明 | 状态 | 代码模块 | 测试 | 轴 | P | 未完成原因 |
|---|---|---|---|---|---|---|---|
| REQ-ASSET-01 | 资产权限默认拒绝（Fail-Closed） | `PARTIAL` | `quant/data_identity/asset_master.py:39-41`（正确，默认 `False/False/OBSERVATION_ONLY`） | `test_data_identity.py` | ENG | **P0** | **生产用的是错误的那个**：`quant/domain/assets.py:53,56` 默认 `strategy_eligible=True`、`execution_eligible=True` |
| REQ-ASSET-02 | 下单前必须校验 `execution_eligible` | `TESTED` | `quant/exe/order_factory.py:51-59` | — | ENG | **P0** | 下单前经 `permission_gate.check_execution_permission` 校验 `execution_eligible` 与资金权限（W2-4 已接入生产；c22/c23/c24 验证闸门 Fail-Closed）。原判定 `NOT_FOUND` 系陈旧——Wave 1 早已把闸门接入 `order_factory`，此处据代码核实订正为 `TESTED` |
| REQ-ASSET-03 | 仅观察类资产（OBSERVATION_ONLY）必须被否决 | `PARTIAL` | `quant/decision/risk_veto.py:83` `check_observation_only` | `test_asset_risk.py` | ENG | **P0** | 函数写了、测了，但 **grep 零调用方**，未接入 `run_all_checks` |
| REQ-ASSET-04 | 风险簇为**先验经济约束**，不得由统计相关性聚类生成 | `PARTIAL` | `quant/assets/risk_clusters.py:57-179`、`config/risk/risk_clusters.json`（人工 15 簇） | `test_asset_risk.py` | STR | P2 | 定义正确（明确拒绝 Riskfolio HRP/HERC），但**未接入 `quant/fund_pipeline.py`**，只有测试调 DecisionEngine |
| REQ-ASSET-05 | 真实持仓 `actual_positions` 只能由 `RECONCILIATION` 来源写入 | `PARTIAL` | `quant/ledgers/guards.py:18-25` | `test_ledger_isolation.py` | ENG | **P0** | 白名单正确，但存在 **3 处旁路**：`app/reconcile_account.py:335` 裸 SQL、`quant/fund_execution.py:30` `save_fund_state()` 零校验、`quant/exe/execution_service.py:70` 硬编码来源 |
| REQ-ASSET-06 | 三账本隔离（计划/执行/实际） | `TESTED` | `quant/ledgers/__init__.py:28-30`、`quant/ledgers/recorder.py`（W2-2 新增） | `test_ledger_isolation.py`、`test_account_ledger.py`、`test_model_target_recording.py`、验收 c34 | ENG | **P0** | **2026-08-01 全部通电**：第一本账（`model_targets`）由 `_record_model_targets` 写入；第二本账（`decision_plans`）由 W2-3 `_record_decision_plans` 写入（`storage/ledgers/decision_plans.db` 已有真实提案，血缘 `source_portfolio_id` 回溯第一本账）；订单链 W2-4 经 `order_factory` + `permission_gate` 接入生产（PAPER 仅生成 DRAFT 意图、不提交）。三本账隔离 + 订单生成链均在生产中真实执行，故由 `PARTIAL` 升 `TESTED`。仅 `execution_service`（提交环节）仍为零生产调用方，属 PAPER 零真实单预期边界 |
| REQ-ASSET-07 | 风险簇暴露上限须在下单前校验 | `NOT_FOUND` | — | — | STR | P2 | 无下单前簇暴露校验 |

---

## 10. 基金三层分析（§十二）

| ID | 需求说明 | 状态 | 代码模块 | 测试 | 轴 | P | 未完成原因 |
|---|---|---|---|---|---|---|---|
| REQ-FUND-01 | L1 净值层分析（收益/回撤/波动） | `PARTIAL` | `quant/fund_pipeline.py` | `test_core_fund_engine.py` | STR | P1 | 有基础指标，无 NAV 单点局限性的显式建模 |
| REQ-FUND-02 | L2 底层持仓层分析（行业/风格/集中度/底层重合度） | `NOT_FOUND` | — | — | STR | P2 | 无持仓穿透数据源与分析；导致「持仓高度重合的两只基金」被当成分散化 |
| REQ-FUND-03 | L3 交易机制层（T+1 未知价、T+1 确认、T+2 到账、赎回费阶梯、限购、QDII 额度） | `PARTIAL` | `quant/exe/order_factory.py`（未知价原则已实现）、`quant/fund_orders/states.py` | `test_execution.py`、`test_fund_order_state_machine.py` | ENG | P1 | 未知价与 11 态已建模；**限购、赎回费阶梯、QDII 额度未建模** |
| REQ-FUND-04 | 三层必须同时参与决策 | `NOT_FOUND` | — | — | STR | P2 | L2 缺失，无法三层合成 |

---

## 11. 数据身份、失效分级、三时钟（§十三、§十四、§十五）

| ID | 需求说明 | 状态 | 代码模块 | 测试 | 轴 | P | 未完成原因 |
|---|---|---|---|---|---|---|---|
| REQ-DATA-01 | 数据来源分离并标明身份（账户事实/行情/派生/研究） | `PARTIAL` | `quant/data_identity/` 整包 | `test_data_identity.py` | ENG | **P0** | 实现完整、测试通过，但**生产零调用方** |
| REQ-DATA-02 | **消除两个互不相通的 `DataStatus` 枚举** | `NOT_FOUND` | `quant/domain/enums.py:36-41` 与 `quant/models.py:8-11` | — | ENG | **P0** | 写入路径用 `quant/models.py` 的，读取/判断路径用 `domain/enums.py` 的，两者无转换，事实上断链。<br>〔2026-08-01 更正〕初版证据误写为 `quant/data_identity/models.py:8`，**该文件不存在**（该包仅有 `__init__.py`/`asset_master.py`/`lineage.py`，且 `lineage.py:31` import 的是 domain 版本）。枚举分裂这一结论本身成立，仅证据路径更正 |
| REQ-DATA-03 | 数据失效分级（正常/陈旧/部分缺失/完全失效），不同级别不同隔离动作 | `TESTED` | 基金分支：`quant/fund_datahub.py:281-284`、`quant/fund_pipeline.py:38-59`；ETF 分支：`quant/strategies/tactical_etf/engine.py::assess_data_health` | 验收 c50 | ENG | **P0** | 〔2026-08-01 更正，原记 `NOT_FOUND`〕基金分支已有两级真实隔离；〔2026-08-02「全打接线」〕ETF 分支已接入四档分级（c50）：`run_tactical_etf` 调用 `assess_data_health`，非 NORMAL 标的移出配权池（研究/合格/实盘三池均过滤），原「ETF 仅布尔式 + Fail-Open」已不成立，验收 c50 锁定调用点与隔离行为 |
| REQ-DATA-04 | 三时钟分离：数据时钟 / 决策时钟 / 执行时钟 | `TESTED` | `quant/gov/clock_consistency.py`（`check_three_clocks()`）；调用点 `app/run_daily.py::_check_decision_clocks` | `test_clock_consistency.py`（5 条）、验收 c40、验收 c51 | ENG | P0 | 〔2026-08-02 更新〕统一模型已补齐；〔2026-08-02「全打接线」〕`run_daily` 已在记录决策计划账前调用 `_check_decision_clocks` 作 Fail-Closed 前置闸门（c51），决策早于数据 / 无数据时钟均被拦截，验收 c51 锁定调用点与行为 |
| REQ-DATA-05 | 显式计算「信号年龄」= 决策时钟 − 数据时钟，超龄拒绝出信号 | `PARTIAL` | `quant/feat/time_decay.py:6,16,19`（零生产调用方）；基金分支已接线 `quant/fund_datahub.py:277` + `quant/fund_pipeline.py:167-171` | — | ENG | **P0** | 〔2026-08-01 更正，原记 `NOT_FOUND`「grep 无命中」——该说法有误〕分三种状态：①`signal_age()`/`signal_expiration()` **已实现且有单测，但生产零调用方**（典型「两个平行世界」）；②基金分支 `max_staleness_calendar_days` **已接线并生效**；③**ETF 分支 `BLOCKED`**——`config/settings.json` 声明了 `max_staleness_trading_days: 0` 但 `engine.py` 从不读取。**实测后果仅适用于 ETF 分支：2026-07-03 的陈旧数据在 2026-08-01（29 天后）仍产出 RISK_ON，并建议 515000 占比 45%** |
| REQ-DATA-06 | 派生数据须记录其来源批次（`BuildFrom` 式溯源） | `NOT_FOUND` | — | — | ENG | P0 | 借鉴 StockSharp S-3，未实现 |

---

## 12. 交易日历分组（§十六）

| ID | 需求说明 | 状态 | 代码模块 | 测试 | 轴 | P | 未完成原因 |
|---|---|---|---|---|---|---|---|
| REQ-CAL-01 | 划分 CN / HK / QDII 三个日历组，各资产归属其一 | `NOT_FOUND` | — | — | ENG | **P0** | 无日历组概念 |
| REQ-CAL-02 | 「是否交易日」「数据是否最新」按组分别判断 | `NOT_FOUND` | — | — | ENG | P0 | — |
| REQ-CAL-03 | **某组数据未到不得阻塞其他组决策** | `NOT_FOUND` | `quant/fund_pipeline.py:83-87` | — | ENG | **P0** | **反向实现**：`common_date = min(latest_dates)` 后截断全部序列 —— QDII 滞后 1-2 天会把 A 股基金的最新数据一起丢掉，全系统退化到最慢一档 |
| REQ-CAL-04 | 跨组比较须明确对齐口径及其代价 | `NOT_FOUND` | — | — | STR | P1 | — |
| REQ-CAL-05 | 各组确认周期差异建模（CN T+1 / QDII 常见 T+2） | `PARTIAL` | `_next_trading_day()` 简化实现 | `test_execution.py` | ENG | P1 | 用统一简化日历，未区分组 |

---

## 13. 现金与防御资产（§十七）

| ID | 需求说明 | 状态 | 代码模块 | 测试 | 轴 | P | 未完成原因 |
|---|---|---|---|---|---|---|---|
| REQ-CASH-01 | 现金为一等公民，`cash_weight` 显式计算并参与校验 | `PARTIAL` | `quant/exe/order_factory.py:52` 有 `if symbol == "CASH": continue` | `test_execution.py` | STR | P2 | CASH 被当特例跳过，**不是显式建模的权重项**；无 `PortfolioWeights.validate()` 式自校验（借鉴 FinRL F-2/F-3） |
| REQ-CASH-02 | 防御资产的来源必须显式（是主动配置还是被动剩余） | `NOT_FOUND` | — | — | STR | P2 | 无来源标记 |
| REQ-CASH-03 | 权重和为 1、非负、现金非负的统一校验 | `PARTIAL` | `quant/portfolio.py::cap_single_weights`、`quant/strategies/tactical_etf/trend.py:90-95` | 验收 c43/c44（策略轴）、`test_strategy_axis.py` | STR | P2 | 〔2026-08-02 由 `NOT_FOUND` 升 `PARTIAL`〕权重和为 1 / 非负 / CASH 显式在册，已被验收 c43 锁定，且断言对象是**生产可达**的 `run_tactical_trend`（`run_daily:136 → engine:174`）。**缺口**：仍无生产期运行时校验器（`PortfolioWeights.validate()` 式），违规只在验收阶段暴露而非下单前阻断 |

---

## 14. 历史运行档案（§十八）

| ID | 需求说明 | 状态 | 代码模块 | 测试 | 轴 | P | 未完成原因 |
|---|---|---|---|---|---|---|---|
| REQ-RUN-01 | 每次运行生成 run 快照（ID/时刻/模式/输入/输出） | `TESTED` | `quant/observability/snapshot.py` `RunContext` | `test_observability.py`、`test_snapshot_run.py`、`test_run_daily_snapshot.py` | ENG | P0 | — （已接入全部 5 个 `run_*` 入口，是当前落地质量最好的模块） |
| REQ-RUN-02 | 建立 `historical_run_registry` 历史运行档案库 | `NOT_FOUND` | — | — | ENG | P1 | grep 无命中；快照目前是散落文件，无检索层 |
| REQ-RUN-03 | 历史状态可信度四档：`ACTUAL_CONFIRMED`/`PLAN_INFERRED`/`MODEL_INFERRED`/`UNKNOWN_LEGACY_STATE` | `TESTED` | `quant/run_archive/credibility.py`；调用点 `app/run_daily.py::_resolve_run_credibility` + `_write_run_snapshot`（记录 `run_credibility` 指标） | `test_run_credibility.py`（5 条）、验收 c41、验收 c53 | ENG | P1 | 〔2026-08-02 由 `NOT_FOUND` 升 `PARTIAL`〕四档已实现；〔2026-08-02「全打接线」〕`run_daily` 在决策后计算四档可信度（第二本账→PLAN_INFERRED，双账失败→UNKNOWN，error 形态被排除）并写入运行快照（c54），验收 c54 锁定调用点与四档判定 |
| REQ-RUN-04 | 档案只追加不修改，更正须追加记录 | `PARTIAL` | 快照文件按 run 落盘 | — | ENG | P1 | 事实上可被覆写，`state/*.json` 是裸文件无保护 |
| REQ-RUN-05 | 快照须含代码版本（git commit）与配置哈希 | `PARTIAL` | `snapshot.py` | `test_observability.py` | ENG | P1 | 需核对是否含 commit/配置哈希字段；若缺须补 |

---

## 15. 评估框架与回测真实性（§十九、§二十）

| ID | 需求说明 | 状态 | 代码模块 | 测试 | 轴 | P | 未完成原因 |
|---|---|---|---|---|---|---|---|
| REQ-EVAL-01 | 评估须超越收益：含尾部/回撤类度量（CVaR/MDD/CDaR/UCI） | `NOT_FOUND` | — | — | STR | P3 | 借鉴 Riskfolio R-1（仅借定义不引依赖） |
| REQ-EVAL-02 | 泛化性评估：样本外 + walk-forward + **按 regime 分段** | `NOT_FOUND` | — | — | STR | P3 | 借鉴 TradeMaster T-4 |
| REQ-EVAL-03 | 可解释性：每个决策可列出输入与取值 | `PARTIAL` | 各处 `reasons` 文本 | — | BOTH | P3 | 非结构化 |
| REQ-EVAL-04 | 失效模式：显式回答何时失效、失效形态、如何检测 | `NOT_FOUND` | — | — | STR | P3 | — |
| REQ-EVAL-05 | 风险贡献分解（看清风险来自哪个持仓） | `NOT_FOUND` | — | — | STR | P3 | 借鉴 Riskfolio R-2 |
| REQ-BT-01 | 回测必须建模 T+1 未知价申赎 | `TESTED` | `quant/research/backtest.py::backtest_unknown_price`；两个引擎 `quant/backtest.py::run_event_backtest`、`quant/fund_backtest.py::run_fund_event_backtest` 均已在汇总前调用 `verify_no_signal_day_fill` 作硬后置条件 | `test_backtest_realism.py`（3 条）、验收 c42、验收 c54 | BOTH | P3 | 〔2026-08-02 由 `NOT_FOUND` 升 `PARTIAL`〕已建模；〔2026-08-02「全打接线」〕两引擎本就实现 T+1 执行，但「信号日绝不成交」此前仅存于注释意图、无代码强制——c55 将 `verify_no_signal_day_fill` 接入两引擎作返回前硬后置条件（成交日≤信号日即 BLOCKED），并把该不变量固化为验收 c55，杜绝为「提高回测收益」改回信号日成交的回归风险。注：c55 此前登记「现网回测建立在收盘价即时成交假设」系误判，经核实两引擎本就 T+1，本轮接线实为补强制后置条件 |
| REQ-BT-02 | 回测必须含费率、确认延迟、到账延迟 | `NOT_FOUND` | — | — | BOTH | P3 | 明确拒绝 quant-trading 零摩擦回测（Q-X2） |
| REQ-BT-03 | **未来函数扫描**：决策特征不得引用 t 之后数据 | `NOT_FOUND` | — | — | ENG | P3 | 针对 quant-trading `shift(-1)` 类缺陷（Q-X1）的防御，未实现 |
| REQ-BT-04 | 回测与实盘走同一代码路径，mode 仅元数据 | `TESTED` | `RunContext.mode` | `test_snapshot_run.py` | ENG | P0 | —（借鉴 FinRL F-4） |

---

## 16. 研究模型隔离（§十九 · Kronos）

| ID | 需求说明 | 状态 | 代码模块 | 测试 | 轴 | P | 未完成原因 |
|---|---|---|---|---|---|---|---|
| REQ-RES-01 | 研究层与决策层物理隔离，模型输出不得直接下单 | `TESTED` | `quant/research_models/` | `test_research_models.py`、`test_research.py` | ENG | P0 | — |
| REQ-RES-02 | Kronos 保持零信任冻结 | `TESTED` | `quant/research_models/` | `test_research_models.py` | ENG | P0 | —（四条阻断理由见影响矩阵 §5.3） |
| REQ-RES-03 | 解冻须先通过确定性/可解释性/失效模式检验 | `NOT_FOUND` | — | — | ENG | P3 | 检验框架未定义（依赖 REQ-EVAL-*） |

---

## 17. 遗留系统角色（§二十一）

| ID | 需求说明 | 状态 | 代码模块 | 测试 | 轴 | P | 未完成原因 |
|---|---|---|---|---|---|---|---|
| REQ-LEGACY-01 | 明确 V4.2 / V4.5.1 的定位（参考实现 or 待退役） | `PARTIAL` | `docs/V42_TO_V46_MIGRATION.md` | — | ENG | P1 | 迁移文档存在，但未声明退役条件与时间点 |
| REQ-LEGACY-02 | 遗留代码不得成为生产主路径 | `NOT_FOUND` | `quant/fund_pipeline.py`、`quant/strategies/tactical_etf/engine.py` | — | ENG | **P0** | **当前恰好相反**：生产主路径就是遗留代码，新架构模块反而闲置 |
| REQ-LEGACY-03 | 清理死代码 | `NOT_FOUND` | `quant/pipeline.py:102` | — | ENG | **P0** | 在 `:110` 赋值之前引用 `actionable_turnover` → 该分支必然 `UnboundLocalError`；说明该分支从未被执行过 |

---

## 18. 未冻结参数治理（§二十二）

| ID | 需求说明 | 状态 | 代码模块 | 测试 | 轴 | P | 未完成原因 |
|---|---|---|---|---|---|---|---|
| REQ-PARAM-01 | 建立未冻结参数登记表 | `TESTED` | `quant/provisional.py`（登记表 4 项）、`docs/UNFROZEN_PARAMETERS.md` | `test_provisional_params.py`（12 条）、验收 c33 | BOTH | P0 | **2026-08-01 W2-1 落地**：登记表已接入 `app/inspect_system.py` → `run_daily.py:32-35` 生产前置闸门，漂移即 Fail-Closed |
| REQ-PARAM-02 | 参数分类：历史遗留值 / 临时默认值(PROVISIONAL) / 已回测值 / 已冻结值 | `PARTIAL` | `quant/provisional.py`（仅 PROVISIONAL 一类） | `test_provisional_params.py` | BOTH | P0 | 四分类只落地 1 类；「已回测值」需 Wave 3 回测基础设施，「已冻结值 / 历史遗留值」尚无标记机制 |
| REQ-PARAM-03 | **Coding Agent 不得自行决定未冻结参数取值** | `PARTIAL` | `app/inspect_system.py`（PROVISIONAL 生效值一致性闸门） | `test_provisional_params.py::test_provisional_drift_fails_closed` | BOTH | P0 | 已能**检测**私改：改 config 而不同步登记表 → 运行终止。但同时改两处仍可绕过，非强约束；且仅覆盖登记在册的 4 项 |
| REQ-PARAM-04 | 区分「已冻结的系统原则」与「仍需回测验证的参数」 | `PARTIAL` | 分散于各文档 | — | BOTH | P0 | 无统一清单 |

---

## 19. 新增测试与验收（§二十三、§二十五）

| ID | 需求说明 | 状态 | 轴 | P | 未完成原因 |
|---|---|---|---|---|---|
| REQ-TEST-01 | 新增 20 项测试覆盖上述新机制 | `NOT_FOUND` | ENG | P0-P3 | 本轮待实现；须随对应能力一起落地，不得先写空壳测试 |
| REQ-ACC-01 | 新增 15 条验收条件 | `NOT_FOUND` | ENG | P0-P3 | 同上 |
| REQ-ACC-02 | 验收条件须标注轴（见 REQ-AXIS-02） | `NOT_FOUND` | ENG | P0 | — |
| REQ-DOC-01 | 新增 13 份设计文档 | `PARTIAL` | ENG | P0 | 已完成 `ARCHITECTURE_INFLUENCE_MATRIX.md`、本文档；其余 11 份本轮补写中 |

---

## 20. 超出范围（登记备查）

| ID | 需求说明 | 状态 | 原因 |
|---|---|---|---|
| REQ-SCOPE-01 | 强化学习决策 agent | `DEFERRED_BY_SCOPE` | V4.6 决策链保持可解释规则；RL 仅可进研究层 |
| REQ-SCOPE-02 | 自动实盘直连下单 | `DEFERRED_BY_SCOPE` | V4.6 实盘为 Fail-Closed + 人在回路，不做自动执行 |
| REQ-SCOPE-03 | 做空 / 杠杆工具 | `DEFERRED_BY_SCOPE` | 当前资产范围内无对应工具（明确拒绝 FinRL `allow_short`） |

---

## 21. 本轮必须优先修复的 6 个断点

按补充 Prompt §二十六 的 P0 定义（账户与数据事实），下列缺陷全部落在 P0/P1，且都有可复现的生产影响：

| 编号 | 缺陷 | 关联需求 | 生产影响 |
|---|---|---|---|
| GAP-1 | 下单不校验资产权限 + 默认 Fail-Open | REQ-ASSET-01/02/03 | **改配置即获得下单能力**，配置文件事实上等同于交易授权 |
| GAP-2 | 陈旧数据仍出信号 | REQ-STATE-07、REQ-DATA-05 | 29 天前的数据产出 RISK_ON + 45% 建仓建议 |
| GAP-3 | 哨兵无数据判绿灯 | REQ-MKT-05、REQ-STATE-02 | 数据源全挂时系统显示「一切正常」 |
| GAP-4 | 账本白名单 3 处旁路 | REQ-ASSET-05 | 「账户事实优先」原则被架空 |
| GAP-5 | 多市场日历对齐到最慢者 | REQ-CAL-03 | QDII 滞后拖累全系统决策时效 |
| GAP-6 | `pipeline.py:102` 必然 UnboundLocalError | REQ-LEGACY-03 | 死分支，说明零换手容忍带检查从未生效 |

---

## 22. 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-08-01 | v1.0 | 初版。63 条需求全量登记；引入「生产零调用方一律不得标 TESTED」判定规则；重新核算真实落地率 9/63 ≈ 14%；登记 6 个 P0/P1 断点 |
| 2026-08-01 | v1.1 | 双向诚实修订：REQ-DATA-03 / REQ-DATA-05 由 `NOT_FOUND` 改判 `PARTIAL`（基金分支已落地）；修正 REQ-DATA-02 引用了不存在文件的错误。`TESTED` 不变，仍 9/63 |
| 2026-08-01 | v1.2 | **W2-1 落地**：REQ-PARAM-01 `PARTIAL` → `TESTED`（参数登记表接入 `inspect_system` 生产前置闸门 + 3 条 Fail-Closed 负向测试 + 验收 c33）；REQ-PARAM-02 / REQ-PARAM-03 `NOT_FOUND` → `PARTIAL`。落地率 9/63 → **10/63 ≈ 16%**。同期产出 `V46_PRODUCTION_WIRING_AUDIT.md`（发现订单链与前两本账生产不可达）与 `V46_COMPLETION_METRIC_RULING.md`（裁定对外口径以本表为准） |
| 2026-08-01 | v1.3 | **W2-2/W2-3/W2-4 落地**：三本账全部通电——第一本账（`model_targets`，W2-2）、第二本账（`decision_plans`，W2-3，含 `source_portfolio_id` 血缘回溯）、订单生成链（`order_factory`+`permission_gate`，W2-4，PAPER 仅生成 DRAFT 意图）。REQ-ASSET-06 `PARTIAL`→`TESTED`、REQ-ASSET-02 `NOT_FOUND`→`TESTED`（订正陈旧判定）。落地率由 **10/63 ≈ 16%** 升至 **12/63 ≈ 19%**。注意：+3% 仅反映「接线」闭环，不意味着策略成熟；验收门 36/42=86% 仍为内部卫生指标 |
| 2026-08-02 | v1.4 | **c37-c49 + Wave3 落地**：新增 6 个治理模块（`data_health` / `confirmation_budget` / `state_synthesis` / `clock_consistency` / `run_archive.credibility` / `backtest_unknown_price`）+ Riskfolio 适配层 5 个实现（全部 `shadow_only=True`）+ PROVISIONAL 校准台架（默认维持原值、LOW 置信度）。**策略轴由 1 条补厚至 5 条**（c43 权重归一 / c44 集中度上限 / c45 Risk-Off 清仓 / c46 选品子集），断言对象为生产可达的 `run_tactical_trend`。REQ-BUDGET-01 / REQ-RUN-03 / REQ-BT-01 / REQ-CASH-03 由 `NOT_FOUND` 升 `PARTIAL`；REQ-STATE-03 / REQ-DATA-04 证据更新。**`TESTED` 保持 12/63 ≈ 19% 不变**——新模块生产零调用方，按判定规则不得升级。同期修复生产 Bug：`trend.py` 原先「先截断后归一」导致 `max_single_weight` 归一后被重新放大、集中度约束长期形同虚设，已改为 `cap_single_weights`（归一后仍受限），由 c44 锁定 |
| 2026-08-02 | v1.5 | **「全打接线」收口（c50–c54 已接线，c55 受阻挂起）**：把 6 个治理模块中公认、生产入口已存在的调用点真正接上，不再造新能力。**5 条需求由 `PARTIAL` → `TESTED`**：REQ-DATA-03（ETF 分支接入四档分级，c50）、REQ-DATA-04（`run_daily` 决策前三时钟 Fail-Closed 闸门，c51）、REQ-STATE-03（`run_tactical_etf` 接入状态合成并强制 RISK_OFF，c52）、REQ-RUN-03（`run_daily` 计算并写入四档可信度，c53）、REQ-BT-01（两回测引擎强制 T+1 不变量 `verify_no_signal_day_fill` 作硬后置条件，c54）。**落地率由 12/63 ≈ 19% 升至 17/63 ≈ 27%**。`PARTIAL` 30 → 25。剩余 `PARTIAL`/`NOT_FOUND` 为真实缺口（如 REQ-BUDGET-01 的 c55 因生产无试探路径如实挂起、c56 PROVISIONAL 参数须人工复核转正、c57 生产期权重运行时硬闸门本轮不造）。**诚实性修正**：c54（回测 T+1）此前登记「现网回测建立在收盘价即时成交假设」系误判，经核实两引擎本就 T+1 执行，本轮接线实为补强制后置条件而非切换执行模型；验收门机械覆盖率同步由 49/57=86% 升至 54/57=95%（c50–c54 升入 CONDITIONS，c55/c56/c57 仍如实挂起） |
