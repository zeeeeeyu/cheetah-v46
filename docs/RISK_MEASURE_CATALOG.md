# 风险度量目录（Risk Measure Catalog）

> 文档编号：RISKFOLIO-RISKMEASURE-004
> 版本：v1.0
> 生成日期：2026-08-02
> 状态：**DEFERRED_BY_SCOPE (Wave 3)**
> 关联文档：`docs/RISKFOLIO_INTEGRATION.md`、`docs/RESEARCH_EVALUATION_FRAMEWORK.md`、`docs/ARCHITECTURE_INFLUENCE_MATRIX.md` §6 R-1、`docs/PORTFOLIO_OPTIMIZATION_GOVERNANCE.md`

---

## 0. 本文档的作用

本文档定义 V4.6 中从 Riskfolio-Lib 借鉴的**尾部与回撤类风险度量**清单，包括每个度量的公式、适用场景、参数、在 V4.6 中的落地位置，以及与现有「下行波动率」的关系。

本文档是 `docs/ARCHITECTURE_INFLUENCE_MATRIX.md` §6 `R-1`（风险度量函数库作为评估层 L4）的细化。本文档**不**定义优化目标函数本身——优化目标属于 `PortfolioAllocator` 的实现职责。

## 1. 当前状态

| 维度 | 现状 |
|---|---|
| 生产风险度量 | **仅下行波动率**。Champion 分配器使用逆下行波动率，位于 `quant/port/` |
| CVaR / EVaR / CDaR / MDD / UCI | **未实现**。仅在 `docs/RESEARCH_EVALUATION_FRAMEWORK.md` 列出 |
| Riskfolio-Lib 风险函数 | **未引入**。`riskfolio/src/RiskFunctions.py` 未被调用 |
| 评估层 L4 | 文档层有框架，代码层未实现 |
| 关系定义 | 下行波动率与其他度量的换算 / 互补关系未定义 |

**结论：本 Wave 3 工作尚未启动，本文档为设计规格。**

## 2. 目标设计：风险度量目录

### 2.1 度量清单总览

| 度量 | 类别 | 关注点 | 在 V4.6 中的角色 |
|---|---|---|---|
| 下行波动率（Downside Volatility） | 波动类 | 下行半方差 | **现有 Champion**，生产主路径 |
| CVaR（Conditional Value at Risk） | 尾部类 | 最坏 5% 损益的期望 | L4 评估 + `RiskfolioMinCVaRAllocator` 的优化目标 |
| EVaR（Entropic Value at Risk） | 尾部类 | 风险熵上界 | L4 评估（更保守的 VaR 上界） |
| CDaR（Conditional Drawdown at Risk） | 回撤类 | 最坏 5% 回撤的期望 | L4 评估 |
| MDD（Maximum Drawdown） | 回撤类 | 历史最大峰谷回撤 | L4 评估 |
| UCI（Ulcer Index） | 回撤类 | 回撤深度与持续时间的平方和 | L4 评估 |

### 2.2 与下行波动率的关系

| 关系维度 | 说明 |
|---|---|
| **不是替代** | CVaR/CDaR 等不替代下行波动率，而是**补充**其盲区 |
| **下行波动率的盲区** | 下行波动率只关注收益的下行离散度，不刻画**尾部厚度**与**回撤路径** |
| **互补定位** | 下行波动率回答「下行有多分散」；CVaR 回答「最坏 5% 有多坏」；CDaR 回答「最坏 5% 回撤有多深」；MDD 回答「历史最惨的一次跌多少」 |
| **Champion 不变** | V4.6 首版 Champion 仍是逆下行波动率（见 `docs/RISKFOLIO_INTEGRATION.md` §2）。其他度量仅用于 L4 评估与 Shadow 比较 |
| **升级条件** | 任何度量要进入生产主路径，必须先通过基准实验（见 `docs/RISKFOLIO_INTEGRATION.md` §7）与治理管线 |

## 3. 各度量详细定义

### 3.1 CVaR（Conditional Value at Risk，条件风险价值）

**公式**（历史法，对应 `RiskFunctions.py:356` `CVaR_Hist`）：

```
CVaR_α = -E[r | r ≤ VaR_α]

其中：
  r = 组合收益率序列
  VaR_α = 收益分布的 α 分位数（如 α=0.05 表示最坏 5%）
  CVaR_α = 最坏 α 比例损失的期望值
```

**参数**：
- `alpha`：尾部水平，默认 0.05（95% 置信）；V4.6 评估默认 0.05
- 估计窗口：与 `AllocationResult.estimation_window` 一致

**适用场景**：
- 刻画尾部厚度，比 VaR 更保守（CVaR ≥ VaR）；
- 作为 `RiskfolioMinCVaRAllocator` 的优化目标；
- L4 评估层比较组合的尾部风险。

**落地位置**：
- 评估：`PortfolioEvaluationEngine` 输出的评价对象字段 `cvar_95`
- 优化：`RiskfolioMinCVaRAllocator`（仅 Shadow）
- 治理：`AllocationResult.risk_measure = "CVaR_95"`

**与下行波动率的关系**：CVaR 关注尾部的**期望损失**，下行波动率关注下行的**离散度**。两者互补，不替代。

### 3.2 EVaR（Entropic Value at Risk，熵风险价值）

**公式**（对应 `RiskFunctions.py:556` `EVaR_Hist`）：

```
EVaR_α = inf_{z>0} (1/z) * ln(E[exp(-z * r)] / α)

其中：
  r = 组合收益率序列
  α = 尾部水平
  EVaR 是 CVaR 的熵上界，EVaR ≥ CVaR ≥ VaR
```

**参数**：
- `alpha`：默认 0.05
- 求解：需要对 z 做数值优化，**可能依赖商业求解器**（与 `R-X2` 一致）

**适用场景**：
- 比 CVaR 更保守，适用于极端尾部风险厌恶场景；
- **V4.6 中仅评估**，不作为优化目标（避免商业求解器依赖，见 X-8）。

**落地位置**：
- 评估：`PortfolioEvaluationEngine` 输出的评价对象字段 `evar_95`
- 优化：**不使用**（V4.6 首版范围外）

**与下行波动率的关系**：EVaR 是最保守的尾部度量，在压力期比 CVaR 更早发出预警，但日常噪声更大。

### 3.3 CDaR（Conditional Drawdown at Risk，条件回撤风险）

**公式**（对应 `RiskFunctions.py:943` `CDaR_Abs`）：

```
CDaR_α = -E[D_t | D_t ≤ DaR_α]

其中：
  D_t = t 时刻的回撤（绝对或相对）
  DaR_α = 回撤分布的 α 分位数
  CDaR_α = 最坏 α 比例回撤的期望
```

**参数**：
- `alpha`：默认 0.05
- 回撤类型：`abs`（绝对回撤）或 `rel`（相对回撤）；V4.6 默认 `rel`
- 估计窗口：与 `AllocationResult.estimation_window` 一致

**适用场景**：
- 刻画回撤路径的尾部，比 MDD 更稳定（MDD 只看单点最大值）；
- L4 评估层比较组合的回撤风险。

**落地位置**：
- 评估：`PortfolioEvaluationEngine` 输出的评价对象字段 `cdar_95`
- 优化：**V4.6 首版不作为优化目标**（CDaR 优化需凸优化，暂不引入）

**与下行波动率的关系**：CDaR 关注**回撤路径**的尾部，下行波动率关注**收益**的下行离散度。CDaR 更贴近「我会在回撤中待多久」的体感。

### 3.4 MDD（Maximum Drawdown，最大回撤）

**公式**（对应 `RiskFunctions.py:787` `MDD_Abs`）：

```
MDD = max_t (Peak_t - Value_t) / Peak_t

其中：
  Peak_t = t 时刻前的历史最高净值
  Value_t = t 时刻净值
  MDD = 历史最大峰谷回撤
```

**参数**：
- 回撤类型：`abs` 或 `rel`；V4.6 默认 `rel`
- 估计窗口：与 `AllocationResult.estimation_window` 一致

**适用场景**：
- 单一数字刻画「最惨的一次」；
- Calmar 比率的分母（年化收益 / MDD）；
- L4 评估层必报指标。

**落地位置**：
- 评估：`PortfolioEvaluationEngine` 输出的评价对象字段 `mdd`
- 基准实验：`docs/RISKFOLIO_INTEGRATION.md` §7.2 必评维度

**与下行波动率的关系**：MDD 是**路径终点**的回撤，下行波动率是**收益分布**的下行离散度。MDD 对样本极端敏感，下行波动率更稳健。

### 3.5 UCI（Ulcer Index，溃疡指数）

**公式**（对应 `RiskFunctions.py:1111` `UCI_Abs`）：

```
UCI = sqrt( (1/T) * Σ_t D_t² )

其中：
  D_t = t 时刻的回撤
  UCI = 回撤深度的均方根
```

**参数**：
- 回撤类型：`abs` 或 `rel`；V4.6 默认 `rel`
- 估计窗口：与 `AllocationResult.estimation_window` 一致

**适用场景**：
- 刻画回撤的**深度与持续时间**（D_t² 对深回撤和长回撤都惩罚）；
- 比 MDD 更全面，比 CDaR 更直观；
- L4 评估层补充指标。

**落地位置**：
- 评估：`PortfolioEvaluationEngine` 输出的评价对象字段 `uci`

**与下行波动率的关系**：UCI 关注**回撤路径的全程**，下行波动率关注**收益分布**。UCI 对「长时间浅回撤」的惩罚强于 MDD。

## 4. 落地位置：L4 评价层

所有上述度量在 V4.6 中落地于 **L4 评价层**（对应 `docs/ARCHITECTURE_INFLUENCE_MATRIX.md` §6 `R-1`），不进入 L1-L3 的决策主路径。

### 4.1 L4 评价对象字段（目标设计）

`PortfolioEvaluationEngine` 产出的评价对象必须包含以下字段（可测试对象，不是图表）：

| 字段 | 类型 | 来源度量 |
|---|---|---|
| `downside_vol` | `float` | 现有 Champion |
| `cvar_95` | `float` | CVaR（α=0.05） |
| `evar_95` | `float` | EVaR（α=0.05） |
| `cdar_95` | `float` | CDaR（α=0.05） |
| `mdd` | `float` | MDD |
| `uci` | `float` | UCI |
| `calmar` | `float` | 年化收益 / MDD |
| `estimation_window` | `tuple[date, date]` | 估计窗口 |
| `input_data_version` | `str` | 数据版本 |
| `risk_measure_engine_version` | `str` | `RiskfolioRiskMeasureEngine` 版本 |

### 4.2 评价对象的硬性约束

- **不得只输出图表**（验收条件 #9）。必须输出可断言、可比较、可序列化的对象；
- **必须可复现**：同一 `input_data_version` + 同一 `risk_measure_engine_version` 必须产出相同结果（测试 #7）；
- **必须可审计**：`estimation_window` 与 `input_data_version` 必须记录（测试 #8）。

## 5. 与现有下行波动率的关系矩阵

| 比较维度 | 下行波动率 | CVaR | EVaR | CDaR | MDD | UCI |
|---|---|---|---|---|---|---|
| 关注对象 | 收益下行离散度 | 尾部期望损失 | 尾部熵上界 | 回撤尾部期望 | 单点最大回撤 | 回撤深度均方根 |
| 是否路径依赖 | 否 | 否 | 否 | 是 | 是 | 是 |
| 对尾部厚度敏感 | 中 | 高 | 最高 | 中 | 低 | 中 |
| 对回撤持续时间敏感 | 否 | 否 | 否 | 中 | 否 | 高 |
| 数值稳定性 | 高 | 中 | 低（需数值优化） | 中 | 低（单点） | 中 |
| 是否需商业求解器 | 否 | 否 | **是**（精确） | 否 | 否 | 否 |
| V4.6 生产主路径 | **是** | 否 | 否 | 否 | 否 | 否 |
| V4.6 Shadow / 评估 | — | 是 | 是 | 是 | 是 | 是 |

## 6. 差距分析

| 目标设计 | 当前状态 | 差距 |
|---|---|---|
| 下行波动率 | 已实现（Champion） | 无 |
| CVaR 评估 | 未实现 | 待引入 `RiskfolioRiskMeasureEngine` |
| EVaR 评估 | 未实现 | 待引入；注意商业求解器依赖（X-8） |
| CDaR 评估 | 未实现 | 待引入 |
| MDD 评估 | 未实现 | 待引入 |
| UCI 评估 | 未实现 | 待引入 |
| L4 评价对象 | 未实现 | 待定义对象 + 字段 |
| 评价对象可测试（验收 #9） | 未实现 | 待实现断言接口 |
| `RiskfolioMinCVaRAllocator` | 未实现 | 待实现（仅 Shadow） |

## 7. 硬性约束

1. **EVaR 不得作为优化目标**（V4.6 首版）—— 精确求解需商业求解器，违反 X-8；仅作评估；
2. **所有度量必须历史法计算** —— 不引入参数模型假设（与 V4.6「不依赖收益分布假设」立场一致）；
3. **估计窗口必须可审计** —— 每个度量的 `estimation_window` 必须记录，且不得晚于决策日（测试 #11）；
4. **评价对象必须可测试** —— 不得只输出图表（验收 #9）；
5. **风险贡献之和与总风险应满足数值误差检查**（测试 #12）—— `RiskContributionAnalyzer` 输出的风险贡献之和与总风险度量的相对误差 ≤ 1e-4。

## 8. 诚实标记

本文档描述的 CVaR / EVaR / CDaR / MDD / UCI 度量在 V4.6 首版中**均未实现**，状态为 `DEFERRED_BY_SCOPE (Wave 3)`。下行波动率已实现且为 Champion，不在本标记范围内。

在以下条件全部满足前，本文档任何度量不得被表述为「已实现」：

1. `RiskfolioRiskMeasureEngine` 有实现且有测试；
2. L4 评价对象有定义且字段完整；
3. 评价对象可断言、可序列化（验收 #9）；
4. 风险贡献数值误差检查通过（测试 #12）；
5. EVaR 的商业求解器依赖已明确标记为 PARTIAL 或有开源回退（X-8 / 测试 #10）。

## 9. 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-08-02 | v1.0 | 初版。定义 CVaR/EVaR/CDaR/MDD/UCI 五度量的公式、参数、落地位置、与下行波动率的关系矩阵。状态 DEFERRED_BY_SCOPE (Wave 3)。 |

---

## 对应验收条件

- **#9 风险评价不只停留在图表输出而能进入可测试的评价对象** —— §4.1 L4 评价对象字段定义；§4.2 硬性约束「不得只输出图表」。
- **#1 通过统一适配器调用** —— §4 度量通过 `RiskfolioRiskMeasureEngine` 接口产出，不直接调用 Riskfolio-Lib。
- **#4 求解器和数据输入可审计** —— §4.1 `estimation_window` + `input_data_version` + `risk_measure_engine_version` 字段。
- **#7 已完成至少一个滚动样本外基准实验** —— §2.2 度量用于基准实验的评价维度（`docs/RISKFOLIO_INTEGRATION.md` §7）。
- **#10 所有未完成能力诚实标记为 PARTIAL 或 DEFERRED_BY_SCOPE** —— §8 诚实标记段落，EVaR 商业求解器依赖明确标记。
