# 组合优化治理流程（Portfolio Optimization Governance）

> 文档编号：RISKFOLIO-GOV-002
> 版本：v1.0
> 生成日期：2026-08-02
> 状态：**DEFERRED_BY_SCOPE (Wave 3)**
> 关联文档：`docs/RISKFOLIO_INTEGRATION.md`、`docs/OPTIMIZER_FALLBACK_POLICY.md`、`docs/RISK_CLUSTER_MODEL.md`、`docs/ASSET_PERMISSION_AND_RISK_CLUSTERS.md`、`docs/RESEARCH_EVALUATION_FRAMEWORK.md`

---

## 0. 本文档的作用

本文档定义 `AllocationResult`（优化器输出）到 `DecisionProposal`（可执行决策计划）之间的**完整治理管线**，以及求解器在产生 `AllocationResult` 时必须满足的**元数据记录要求**和**回退链定义**。

本文档不定义优化算法本身，也不定义 `DecisionProposal` 之后的执行链路（执行链路属于 `quant/exe/` 与 `quant/fund_orders/` 的职责）。

## 1. 当前状态

| 维度 | 现状 |
|---|---|
| 治理管线 | **不存在**。当前 `quant/fund_pipeline.py` 直接从分配器输出进入订单生成，无中间治理 |
| 求解器元数据 | **无记录**。当前 Champion（逆下行波动率）无求解器，无元数据 |
| 回退链 | **无实现**。当前无回退路径，分配器失败即整轮失败 |
| AllocationResult 对象 | **未定义**（见 `docs/RISKFOLIO_INTEGRATION.md` §4.2） |
| DecisionProposal 对象 | **未定义**。当前生产路径无此中间层 |
| 三本账隔离 | 见 `docs/ARCHITECTURE_INFLUENCE_MATRIX.md` §8「两个平行世界」——干净架构侧有雏形，生产侧无 |

**结论：本 Wave 3 工作尚未启动，本文档为设计规格。**

## 2. 目标设计：AllocationResult → DecisionProposal 完整管线

`AllocationResult` 必须依次通过以下 5 个 Gate 才能成为 `DecisionProposal`。**任何 Gate 拒绝即终止本轮晋级**，`AllocationResult` 留在 Shadow 通道，不得进入决策计划账。

```
AllocationResult (shadow_only=True)
   │
   ├─ Gate 1: Timing (择时门)
   │     - 校验：AllocationResult 的 estimation_window 是否与当前 Timing 信号版本对齐
   │     - 校验：信号年龄（docs/DATA_FITNESS_AND_SIGNAL_AGE.md）是否超阈值
   │     - 拒绝条件：信号过期 / 估计窗口与 Timing 不一致
   │
   ├─ Gate 2: Risk Governance (风险治理门)
   │     - 校验：solver_status == OPTIMAL（测试 #6）
   │     - 校验：constraint_violations 为空（测试 #5）
   │     - 校验：is_approximate == False 或被显式接受
   │     - 校验：fallback_level ≤ 阈值（默认 ≤ 2，见 §4）
   │     - 校验：风险簇暴露（三源合并后）未突破 max_exposure
   │     - 拒绝条件：求解失败 / 约束违反 / 回退过深 / 簇暴露超限
   │
   ├─ Gate 3: Tradeability Gate (可交易性门)
   │     - 校验：资产权限（tradable / observable / real_only，见 docs/ASSET_PERMISSION_AND_RISK_CLUSTERS.md）
   │     - 校验：停牌 / 涨跌停 / 申赎开放日（基金）
   │     - 校验：lot_size 整数化后权重偏差 ≤ 容差
   │     - 拒绝条件：含不可交易标的 / 整数化破坏权重
   │
   ├─ Gate 4: Fee and Holding Period Gate (费用与持有期门)
   │     - 校验：换手率 ≤ max_turnover
   │     - 校验：跟踪误差 ≤ 跟踪误差上限
   │     - 校验：赎回费 / 惩罚期是否触发（持有期 < 7 天 / 30 天）
   │     - 拒绝条件：换手过高 / 费用侵蚀后收益为负 / 违反持有期约束
   │
   └─ Gate 5: Manual Approval (人工批准门)
         - 校验：Research 权限的 AllocationResult 不得自动晋级（测试 #9）
         - 校验：fallback_level ≥ 3 必须人工确认
         - 校验：首次替换 Champion 必须人工确认（验收 #8）
         - 拒绝条件：未获人工批准 / 自动化策略未显式开启
   │
   ▼
DecisionProposal (进入决策计划账)
```

### 2.1 Gate 拒绝后的处理

- **不得**把被拒绝的 `AllocationResult` 改写后重试同一管线（防止污染审计）；
- **必须**记录拒绝的 Gate、拒绝原因、原始 `AllocationResult` 快照；
- **必须**触发回退链（见 §4）或保持原有目标（回退链第 4 级）；
- **不得**把被拒绝的权重解释为「主动现金」（测试 #3）。

## 3. 目标设计：求解器治理 —— 必须记录的 12 项元数据

每次产生 `AllocationResult` 时，以下 12 项元数据**必须完整记录**，缺一项视为审计违约。记录位置为 `AllocationResult` 对应字段（见 `docs/RISKFOLIO_INTEGRATION.md` §4.2）与审计日志双写。

| # | 元数据 | AllocationResult 字段 | 说明 |
|---|---|---|---|
| 1 | Riskfolio-Lib 版本 | `riskfolio_lib_version` | 非 Riskfolio 实现填 `None` |
| 2 | CVXPY 版本 | `cvxpy_version` | 非 CVXPY 实现填 `None` |
| 3 | 求解器与版本 | `solver` + `solver_version` | 如 `CLARABEL 0.9.0` |
| 4 | 求解状态 | `solver_status` | `OPTIMAL` / `INFEASIBLE` / 等 |
| 5 | 是否最优 | `is_optimal` | 布尔 |
| 6 | 是否近似 | `is_approximate` | 数值不稳定 / 提前停止 |
| 7 | 运行时间 | `solve_time_ms` | 毫秒 |
| 8 | 输入窗口 | `estimation_window` | `(start_date, end_date)` |
| 9 | 协方差估计方法 | `covariance_method` | `sample` / `ledoit_wolf` / `oas` |
| 10 | 预期收益估计方法 | `expected_return_method` | 不依赖 μ 的模型填 `None` |
| 11 | 风险度量 | `risk_measure` | `CVaR_95` / `downside_vol` / `None` |
| 12 | 约束版本 | `parameter_version` | 约束参数集哈希 |

### 3.1 元数据校验规则

- `solver_status != OPTIMAL` 时，`AllocationResult` **不得**进入 Gate 2（测试 #6）；
- `is_approximate == True` 且未显式接受的，**不得**进入 Gate 2；
- `estimation_window` 的 `end_date` 不得晚于当前决策日（防止前视偏差，测试 #11）；
- `parameter_version` 必须与当前生效的约束配置版本一致，不一致视为配置漂移。

### 3.2 可复现性要求（测试 #7）

同一 `input_data_version` + 同一 `parameter_version` + 同一求解器配置（`solver` + `solver_version` + `riskfolio_lib_version` + `cvxpy_version`）必须产出**可复现**的 `AllocationResult`。复现判定标准：权重逐分量绝对误差 ≤ 1e-6。

## 4. 目标设计：回退链 5 级定义

当 Gate 1-5 任一拒绝、或优化器本身失败时，按以下 5 级回退链处理。**任何回退必须记录 `fallback_level` 与 `fallback_reason`**（见 `AllocationResult` 字段）。

| 级别 | 回退目标 | 触发条件 | 输入 | 输出 |
|---|---|---|---|---|
| **L0** | 原优化模型 | 正常求解成功 | 原始输入 | `AllocationResult` (fallback_level=0) |
| **L1** | 原优化模型 + 参数放松 | 求解 `INFEASIBLE` / `OPTIMAL_INACCURATE` | 放松约束（如放宽跟踪误差上限 10%） | `AllocationResult` (fallback_level=1, fallback_reason 记录放松项) |
| **L2** | 更稳定的层次模型 | L1 仍失败 / 数值不稳定 | 同输入，切换到 HRP/HERC 等非凸优化模型 | `AllocationResult` (fallback_level=2) |
| **L3** | 现有逆下行波动率 Champion | L2 失败 / Riskfolio-Lib 不可用 | 同输入，切换到当前 Champion | `AllocationResult` (fallback_level=3, solver="champion_downside_vol") |
| **L4** | 保持原有目标 | L3 失败 / 数据或治理不允许 | 不生成新 `AllocationResult` | 标记 `HOLD`，记录原因 |
| **L5** | 停止生成新计划 | L4 仍无法满足 / 治理层显式拒绝 | 无 | 不产出任何计划，仅产出审计记录 |

### 4.1 回退链的硬性约束

- **不得跳级回退**：必须从 L0 开始逐级尝试，除非当前级别明确不可用（如 Riskfolio-Lib 不可用时可直接跳 L3，但必须记录跳过 L1/L2 的原因）；
- **L3 的特殊性**：L3 回退到 Champion 时，`AllocationResult` 的 `solver` 字段填 `champion_downside_vol`，`riskfolio_lib_version` / `cvxpy_version` 填 `None`；
- **L4 / L5 不得产出权重**：L4 保持原有目标账户不变，L5 完全停止。两者都**不得**产出新的 `weights` 字段（测试 #3）；
- **回退深度阈值**：Gate 2 默认拒绝 `fallback_level ≥ 3` 的自动晋级（需 Manual Approval，见 Gate 5）。

### 4.2 回退记录要求

每次回退必须记录：

1. 触发回退的原始失败原因（求解器状态 / Gate 拒绝原因）；
2. 尝试过的每一级（L0 → L1 → L2 → ...）及其失败原因；
3. 最终稳定的级别；
4. 跳过的级别及跳过原因（如有）；
5. 时间戳、`input_data_version`、`parameter_version`。

详细触发条件与测试要求见 `docs/OPTIMIZER_FALLBACK_POLICY.md`。

## 5. 差距分析

| 目标设计 | 当前状态 | 差距 |
|---|---|---|
| 5 个 Gate | 生产路径无任何 Gate；`quant/fund_pipeline.py` 直接进入订单 | 全部待实现 |
| 12 项元数据记录 | 无求解器、无元数据 | 全部待实现 |
| 5 级回退链 | 无；分配器失败即整轮失败 | 全部待实现 |
| AllocationResult → DecisionProposal 转换 | 无 DecisionProposal 对象 | 待定义对象 + 转换逻辑 |
| 可复现性校验（测试 #7） | 无 | 待实现确定性校验 |
| 不同估计窗口审计（测试 #8） | 无 | 待实现窗口版本审计 |
| Research 权重不得写入正式计划（测试 #9） | 无 Shadow 通道，无 `shadow_only` 校验 | 待实现 |

## 6. 与现有架构的对接点

| 现有模块 | 对接方式 |
|---|---|
| `quant/decision/` | `DecisionProposal` 的归属模块；治理管线在此处串联 5 Gate |
| `quant/decision/risk_veto.py` | Gate 2 的风险簇暴露校验复用 `RiskClusterCalculator` |
| `quant/sentinel/` | Gate 1 的 Timing 信号年龄校验复用 Sentinel 输出 |
| `quant/assets/` | Gate 3 的资产权限校验复用资产目录 |
| `quant/ledgers/` | Gate 2 的持仓暴露输入来自账本事实 |
| `quant/fund_pipeline.py` | **生产侧需改造**：当前直接订单生成的路径必须插入治理管线（见 `docs/ARCHITECTURE_INFLUENCE_MATRIX.md` §8 GAP） |
| `quant/port/` | Champion 分配器位于此处，作为 L3 回退目标 |

## 7. 诚实标记

本文档描述的治理管线、元数据记录、回退链在 V4.6 首版中**均未实现**，状态为 `DEFERRED_BY_SCOPE (Wave 3)`。在以下条件全部满足前，本文档任何条目不得被表述为「已实现」：

1. 5 个 Gate 均有实现且有测试；
2. 12 项元数据在 `AllocationResult` 中完整填充；
3. 5 级回退链有实现且通过 `docs/OPTIMIZER_FALLBACK_POLICY.md` 的 12 条测试；
4. `AllocationResult` → `DecisionProposal` 的转换在生产调用链上真实执行（非仅测试调用）。

## 8. 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-08-02 | v1.0 | 初版。定义 5 Gate 治理管线、12 项求解器元数据、5 级回退链。状态 DEFERRED_BY_SCOPE (Wave 3)。 |

---

## 对应验收条件

- **#1 通过统一适配器调用** —— §2 治理管线的输入是统一适配器产出的 `AllocationResult`，不直接耦合 Riskfolio-Lib。
- **#2 不成为账户和执行的直接依赖** —— §2 5 Gate 管线确保 `AllocationResult` 不得直接成为账户或执行输入。
- **#4 求解器和数据输入可审计** —— §3 定义 12 项必须记录的元数据；§3.2 可复现性要求。
- **#5 求解失败具有安全回退** —— §4 定义 5 级回退链与硬性约束。
- **#6 Shadow 结果与正式结果物理隔离** —— §2 Gate 5 校验 `shadow_only`；Gate 拒绝后不得改写重试。
- **#8 未经批准的优化模型不能替换 Champion** —— §2 Gate 5 首次替换 Champion 必须人工确认。
