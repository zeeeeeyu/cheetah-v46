# 优化器回退策略（Optimizer Fallback Policy）

> 文档编号：RISKFOLIO-FALLBACK-005
> 版本：v1.0
> 生成日期：2026-08-02
> 状态：**DEFERRED_BY_SCOPE (Wave 3)**
> 关联文档：`docs/RISKFOLIO_INTEGRATION.md`、`docs/PORTFOLIO_OPTIMIZATION_GOVERNANCE.md` §4、`docs/ARCHITECTURE_INFLUENCE_MATRIX.md` §6

---

## 0. 本文档的作用

本文档定义 `AllocationResult` 产生过程中**5 级回退链**的触发条件、每级的输入输出、记录要求，并**逐条展开**测试要求 12 条。

本文档是 `docs/PORTFOLIO_OPTIMIZATION_GOVERNANCE.md` §4 的细化。回退链的级别定义、硬性约束以该文档为准，本文档补充触发条件、输入输出契约、测试用例。

## 1. 当前状态

| 维度 | 现状 |
|---|---|
| 回退链 | **无实现**。当前分配器失败即整轮失败，无回退路径 |
| 求解失败处理 | **无**。当前 Champion 无求解器，不存在求解失败场景 |
| 空权重处理 | **无**。当前无空权重场景定义 |
| 商业求解器回退 | **无**。当前无商业求解器依赖 |
| 测试 12 条 | **无实现**。全部待实现 |

**结论：本 Wave 3 工作尚未启动，本文档为设计规格。**

## 2. 目标设计：5 级回退链定义

### 2.1 回退链总览

```
L0 原优化模型（正常）
 │ 失败/不可行/数值不稳定
 ▼
L1 原优化模型 + 参数放松
 │ 仍失败
 ▼
L2 更稳定的层次模型（HRP/HERC/NCO）
 │ 仍失败 / Riskfolio-Lib 不可用
 ▼
L3 现有逆下行波动率 Champion
 │ Champion 不可用 / 数据或治理不允许
 ▼
L4 保持原有目标（HOLD）
 │ 治理层显式拒绝 / 数据全面不可用
 ▼
L5 停止生成新计划（STOP）
```

### 2.2 每级触发条件、输入输出、记录要求

#### L0：原优化模型

| 维度 | 内容 |
|---|---|
| 触发条件 | 无（默认级别） |
| 输入 | 原始输入：`input_data_version`、`parameter_version`、`estimation_window`、约束配置 |
| 输出 | `AllocationResult`（`fallback_level=0`，`fallback_reason=None`） |
| 记录要求 | 12 项求解器元数据完整（见 `docs/PORTFOLIO_OPTIMIZATION_GOVERNANCE.md` §3） |
| 失败判定 | `solver_status ∈ {INFEASIBLE, OPTIMAL_INACCURATE, FAILED}` 或 `is_approximate=True` 且未被显式接受 |

#### L1：原优化模型 + 参数放松

| 维度 | 内容 |
|---|---|
| 触发条件 | L0 失败 |
| 输入 | L0 输入 + 放松参数（如：跟踪误差上限 +10%、换手上限 +20%、约束容差放宽） |
| 输出 | `AllocationResult`（`fallback_level=1`，`fallback_reason="relaxed_constraints: <具体放松项>"`） |
| 记录要求 | 放松项明细、放松前后参数对比、L0 失败原因 |
| 失败判定 | 同 L0 |
| 硬性约束 | 放松项不得违反经济先验簇的 `max_exposure`（簇约束不可放松） |

#### L2：更稳定的层次模型

| 维度 | 内容 |
|---|---|
| 触发条件 | L1 仍失败 |
| 输入 | L0 原始输入（不使用 L1 的放松参数） |
| 输出 | `AllocationResult`（`fallback_level=2`，`solver="hrp_analytic"` 或 `"herc_analytic"`，`fallback_reason="switched_to_hierarchical_model"`） |
| 记录要求 | 切换的层次模型类型、L0/L1 失败原因 |
| 失败判定 | 层次模型本身异常（如协方差矩阵奇异、输入数据不足） |
| 硬性约束 | 层次模型的统计聚类**不得**独立产生约束（见 `docs/RISK_CLUSTER_MODEL.md` §4） |

#### L3：现有逆下行波动率 Champion

| 维度 | 内容 |
|---|---|
| 触发条件 | L2 失败 / Riskfolio-Lib 不可用（import 失败、求解器依赖缺失） |
| 输入 | L0 原始输入 |
| 输出 | `AllocationResult`（`fallback_level=3`，`solver="champion_downside_vol"`，`riskfolio_lib_version=None`，`cvxpy_version=None`，`fallback_reason="riskfolio_unavailable_or_l2_failed"`） |
| 记录要求 | Riskfolio-Lib 不可用的具体原因（import 错误 / 求解器缺失 / L2 异常） |
| 失败判定 | Champion 本身异常（数据不足、下行波动率计算失败） |
| 硬性约束 | L3 的 `AllocationResult.shadow_only` 仍为 `True`（Champion 回退不等于获得 LIVE 权限） |

#### L4：保持原有目标（HOLD）

| 维度 | 内容 |
|---|---|
| 触发条件 | L3 失败 / 数据或治理不允许生成新计划 |
| 输入 | 无新输入 |
| 输出 | **不产出 `AllocationResult`**。仅产出审计记录：`{action: "HOLD", reason: <原因>, timestamp, input_data_version, parameter_version}` |
| 记录要求 | L0-L3 全部失败原因、最终 HOLD 决策依据 |
| 失败判定 | 无（L4 是终止状态之一） |
| 硬性约束 | **不得**产出新的 `weights` 字段（测试 #3）。保持原有目标账户不变 |

#### L5：停止生成新计划（STOP）

| 维度 | 内容 |
|---|---|
| 触发条件 | 治理层显式拒绝 / 数据全面不可用 / 系统级故障 |
| 输入 | 无 |
| 输出 | 仅产出审计记录：`{action: "STOP", reason: <原因>, timestamp}` |
| 记录要求 | 触发 STOP 的治理层 Gate、拒绝原因 |
| 失败判定 | 无（L5 是终止状态） |
| 硬性约束 | **不得**产出任何计划。必须人工介入才能恢复 |

### 2.3 跳级回退规则

- **一般情况下不得跳级**：必须 L0 → L1 → L2 → L3 → L4 → L5 逐级尝试；
- **例外**：Riskfolio-Lib 不可用（import 失败）时，可直接跳 L3，但必须记录跳过 L1/L2 的原因（`fallback_reason="skipped_l1_l2: riskfolio_import_failed"`）；
- **禁止**：任何情况下不得跳过 L4 / L5 直接到「产出新计划」。

## 3. 目标设计：回退记录要求

每次回退（无论最终级别）必须记录以下信息，写入审计日志：

| # | 字段 | 说明 |
|---|---|---|
| 1 | `trace_id` | 本次回退链的唯一标识 |
| 2 | `timestamp` | 回退发生时间 |
| 3 | `input_data_version` | 输入数据版本 |
| 4 | `parameter_version` | 参数版本 |
| 5 | `l0_failure_reason` | L0 失败原因（如 L0 成功则填 `None`） |
| 6 | `l1_failure_reason` | L1 失败原因 |
| 7 | `l2_failure_reason` | L2 失败原因 |
| 8 | `l3_failure_reason` | L3 失败原因 |
| 9 | `final_level` | 最终稳定的级别 |
| 10 | `skipped_levels` | 跳过的级别列表及跳过原因 |
| 11 | `gate_rejections` | 治理层 Gate 拒绝记录（如有） |
| 12 | `allocation_result_snapshot` | 最终 `AllocationResult` 快照（L4/L5 为 `None`） |

## 4. 测试要求 12 条逐条展开

### 测试 #1：Riskfolio-Lib 不可用时生产系统正常回退

| 维度 | 内容 |
|---|---|
| 测试目标 | 验证 X-8 / 验收 #5：Riskfolio-Lib 不可用时，生产系统回退到 Champion 或更深级别，不崩溃 |
| 前置条件 | 生产系统已接入统一适配接口（验收 #1） |
| 测试步骤 | 1. 模拟 `import riskfolio` 失败（卸载包 / mock import error）<br>2. 触发一次完整决策周期<br>3. 观察回退链行为 |
| 预期结果 | 回退链跳到 L3（Champion）或 L4（HOLD），`fallback_level ≥ 3`，`fallback_reason` 含 `riskfolio_unavailable`，生产系统不抛未捕获异常，原有目标账户不变 |
| 通过判定 | 上述全部满足 |

### 测试 #2：优化不可行时不得更新目标账户

| 维度 | 内容 |
|---|---|
| 测试目标 | 验证 X-6 / 验收 #5：求解 `INFEASIBLE` 时，目标账户不被更新 |
| 前置条件 | L0 优化器配置为可触发 `INFEASIBLE`（如设置不可能满足的约束） |
| 测试步骤 | 1. 设置矛盾约束（如 `sum(weights)=1` 且 `weights ≤ 0`）<br>2. 触发决策周期<br>3. 观察目标账户 |
| 预期结果 | `solver_status=INFEASIBLE`，进入回退链 L1→L2→...，最终 `fallback_level ≥ 3` 或 HOLD，目标账户快照前后一致 |
| 通过判定 | 目标账户未更新 + 回退链完整记录 |

### 测试 #3：空权重不得自动解释为主动现金

| 维度 | 内容 |
|---|---|
| 测试目标 | 验证 X-7：优化失败产生的空权重不得被解释为「主动持有 100% 现金」的决策 |
| 前置条件 | 触发 L4 / L5（保持原有目标 / 停止） |
| 测试步骤 | 1. 触发回退至 L4<br>2. 检查输出是否含 `weights` 字段<br>3. 检查下游是否把空权重解释为现金 |
| 预期结果 | L4/L5 **不得**产出 `weights` 字段。下游若收到无 `weights` 的结果，必须保持原有目标，不得填充 `{CASH: 1.0}` |
| 通过判定 | 无 `weights` 产出 + 下游无现金解释 |

### 测试 #4：统计风险簇不得覆盖人工经济风险簇

| 维度 | 内容 |
|---|---|
| 测试目标 | 验证 X-3 / 验收 #3：统计聚类 S 不得独立产生约束，不得拆分经济先验 E 的约束桶 |
| 前置条件 | 三源合并算法已实现（见 `docs/RISK_CLUSTER_MODEL.md` §3） |
| 测试步骤 | 1. 构造场景：经济先验把资产 A、B 归入「半导体」簇（max_exposure=20%）<br>2. 统计聚类把 A、B 拆到不同簇<br>3. 运行三源合并 + Gate 2 校验 |
| 预期结果 | 合并后 A、B 仍在同一约束桶，max_exposure 仍为 20%。统计聚类的拆分被忽略或仅记录为 `statistical_hints` |
| 通过判定 | 约束桶未被拆分 + 统计聚类未独立产生约束 |

### 测试 #5：求解结果必须满足权重和约束检查

| 维度 | 内容 |
|---|---|
| 测试目标 | 验证 `AllocationResult` 的基本合法性 |
| 前置条件 | L0/L1/L2/L3 任一成功产出 `AllocationResult` |
| 测试步骤 | 1. 取 `AllocationResult.weights`<br>2. 校验：权重和 = 1.0（容差 1e-6）、所有权重 ≥ 0、`CASH` 权重 ≥ 0<br>3. 校验：`constraint_violations` 为空 |
| 预期结果 | 权重和约束检查全部通过 |
| 通过判定 | 任一检查失败则该 `AllocationResult` 不得进入 Gate 2 |

### 测试 #6：求解状态非成功时不得进入治理层

| 维度 | 内容 |
|---|---|
| 测试目标 | 验证 Gate 2 的 `solver_status` 校验 |
| 前置条件 | 产出 `solver_status ∈ {INFEASIBLE, OPTIMAL_INACCURATE, FAILED}` 的 `AllocationResult` |
| 测试步骤 | 1. 构造求解失败的 `AllocationResult`<br>2. 尝试进入 Gate 1→Gate 2<br>3. 观察 Gate 2 行为 |
| 预期结果 | Gate 2 拒绝该 `AllocationResult`，不进入 Gate 3-5，触发回退链 |
| 通过判定 | Gate 2 拒绝 + 回退链触发 |

### 测试 #7：同一输入版本和求解器配置应可复现

| 维度 | 内容 |
|---|---|
| 测试目标 | 验证可复现性（验收 #4） |
| 前置条件 | 同一 `input_data_version` + 同一 `parameter_version` + 同一求解器配置 |
| 测试步骤 | 1. 固定上述三个版本<br>2. 运行两次 `PortfolioAllocator.allocate()`<br>3. 比较两次 `AllocationResult.weights` |
| 预期结果 | 权重逐分量绝对误差 ≤ 1e-6 |
| 通过判定 | 误差达标 |
| 注意 | 若求解器本身非确定性（如某些 MIP 求解器），必须在 `diagnostics` 中标记 `non_deterministic=True`，并固定随机种子 |

### 测试 #8：不同估计窗口必须写入审计

| 维度 | 内容 |
|---|---|
| 测试目标 | 验证 `estimation_window` 的审计完整性 |
| 前置条件 | 多次决策使用不同估计窗口 |
| 测试步骤 | 1. 运行三次决策，分别使用 252 天 / 504 天 / 756 天窗口<br>2. 查询审计日志 |
| 预期结果 | 每次决策的 `estimation_window` 完整记录，且与 `AllocationResult.estimation_window` 一致 |
| 通过判定 | 审计日志完整 + 字段一致 |

### 测试 #9：Research 权重不得写入正式计划

| 维度 | 内容 |
|---|---|
| 测试目标 | 验证 Shadow 物理隔离（验收 #6） |
| 前置条件 | `AllocationResult.shadow_only=True` |
| 测试步骤 | 1. 产出 `shadow_only=True` 的 `AllocationResult`<br>2. 尝试进入 Gate 5（Manual Approval）<br>3. 观察是否晋级为 `DecisionProposal` |
| 预期结果 | Gate 5 拒绝晋级，`shadow_only=True` 的结果永远不进入决策计划账 |
| 通过判定 | Gate 5 拒绝 + 决策计划账无 Shadow 权重 |

### 测试 #10：商业求解器不可用时必须有开源求解器或 Champion 回退

| 维度 | 内容 |
|---|---|
| 测试目标 | 验证 X-8 |
| 前置条件 | EVaR 等需商业求解器的度量已引入 |
| 测试步骤 | 1. 模拟 MOSEK 不可用（卸载 / mock）<br>2. 触发需 EVaR 的评估<br>3. 观察行为 |
| 预期结果 | 评估降级为开源求解器（CLARABEL）或标记 `PARTIAL`，优化路径回退到 L3（Champion）。生产主路径不崩溃 |
| 通过判定 | 无商业求解器依赖强制 + 回退正常 |

### 测试 #11：全历史协方差不得用于历史滚动决策

| 维度 | 内容 |
|---|---|
| 测试目标 | 验证 X-9：防止前视偏差 |
| 前置条件 | 滚动样本外实验已实现 |
| 测试步骤 | 1. 运行滚动样本外实验<br>2. 对每个历史决策日 t，检查 `estimation_window.end_date`<br>3. 确认 `end_date ≤ t` |
| 预期结果 | 所有历史决策的估计窗口结束日不晚于决策日 |
| 通过判定 | 无前视偏差 |

### 测试 #12：风险贡献之和与总风险应满足数值误差检查

| 维度 | 内容 |
|---|---|
| 测试目标 | 验证 `RiskContributionAnalyzer` 的数值一致性 |
| 前置条件 | `AllocationResult.risk_contribution` 非空 |
| 测试步骤 | 1. 取 `risk_contribution`（资产 → 贡献占比）<br>2. 求和 S = Σ 贡献<br>3. 比较 S 与 1.0 |
| 预期结果 | `abs(S - 1.0) ≤ 1e-4`（相对误差） |
| 通过判定 | 误差达标 |
| 注意 | 若使用 CVaR 等非凸风险度量，容差可放宽至 1e-3，但必须在 `diagnostics` 中记录 |

## 5. 测试矩阵

| 测试 # | 对应回退级别 | 对应禁止项 | 对应验收条件 |
|---|---|---|---|
| #1 | L0→L3 跳级 | X-8 | #5 |
| #2 | L0→L1→... | X-6 | #5 |
| #3 | L4/L5 | X-7 | #5 |
| #4 | 不涉及回退（合并层） | X-3 | #3 |
| #5 | L0-L3 任一 | — | #5 |
| #6 | Gate 2 | — | #5 |
| #7 | L0 | — | #4 |
| #8 | L0 | — | #4 |
| #9 | Gate 5 | — | #6 |
| #10 | L0→L3 | X-8 | #5 |
| #11 | L0（历史回填） | X-9 | #7 |
| #12 | L0（输出校验） | — | #9 |

## 6. 差距分析

| 目标设计 | 当前状态 | 差距 |
|---|---|---|
| 5 级回退链实现 | 无 | 全部待实现 |
| 跳级回退规则 | 无 | 待实现 |
| 回退记录 12 字段 | 无 | 待实现 |
| 测试 #1-#12 | 无 | 全部待实现 |
| 与治理管线 Gate 对接 | 无 | 待实现 Gate 2 / Gate 5 与回退链的联动 |

## 7. 诚实标记

本文档描述的 5 级回退链、跳级规则、12 字段审计记录、12 条测试在 V4.6 首版中**均未实现**，状态为 `DEFERRED_BY_SCOPE (Wave 3)`。

在以下条件全部满足前，本文档任何条目不得被表述为「已实现」：

1. 5 级回退链有实现且通过测试 #1-#3、#10；
2. 12 字段审计记录完整写入；
3. 12 条测试全部通过；
4. 回退链在治理管线中真实联动（Gate 2 拒绝触发回退，Gate 5 校验 `shadow_only`）。

## 8. 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-08-02 | v1.0 | 初版。定义 5 级回退链触发条件 / 输入输出 / 记录要求，逐条展开 12 条测试要求。状态 DEFERRED_BY_SCOPE (Wave 3)。 |

---

## 对应验收条件

- **#5 求解失败具有安全回退** —— §2 5 级回退链定义；§4 测试 #1、#2、#3、#5、#6、#10 直接覆盖。
- **#1 通过统一适配器调用** —— §2 回退链作用于统一适配器产出的 `AllocationResult`，不直接耦合 Riskfolio-Lib。
- **#2 不成为账户和执行的直接依赖** —— §2 L4/L5 不产出 `weights`（测试 #3）；回退不直接更新账户。
- **#3 经济 / 持仓 / 统计风险簇已明确分离** —— §4 测试 #4 验证统计聚类不得覆盖经济先验。
- **#4 求解器和数据输入可审计** —— §3 12 字段审计记录；§4 测试 #7、#8。
- **#6 Shadow 结果与正式结果物理隔离** —— §4 测试 #9 验证 Research 权重不得写入正式计划。
- **#7 已完成至少一个滚动样本外基准实验** —— §4 测试 #11 验证无前视偏差。
- **#9 风险评价不只停留在图表输出而能进入可测试的评价对象** —— §4 测试 #12 验证风险贡献数值一致性。
- **#10 所有未完成能力诚实标记为 PARTIAL 或 DEFERRED_BY_SCOPE** —— §7 诚实标记段落。
