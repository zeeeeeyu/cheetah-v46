# 风险簇模型（Risk Cluster Model）

> 文档编号：RISKFOLIO-CLUSTER-003
> 版本：v1.0
> 生成日期：2026-08-02
> 状态：**DEFERRED_BY_SCOPE (Wave 3)**
> 关联文档：`docs/ASSET_PERMISSION_AND_RISK_CLUSTERS.md`、`docs/ARCHITECTURE_INFLUENCE_MATRIX.md` §6、`docs/RISKFOLIO_INTEGRATION.md` §5、`docs/PORTFOLIO_OPTIMIZATION_GOVERNANCE.md`

---

## 0. 本文档的作用

本文档定义 V4.6 中**风险簇**的三源构成模型，明确每一类的数据来源、判定规则、合并规则，以及 Riskfolio-Lib 在其中的**职责边界**。

本文档是 `docs/ASSET_PERMISSION_AND_RISK_CLUSTERS.md` §2「风险簇是先验的经济约束」的扩展：该文档定义了「经济先验」这一类，本文档在此基础上补齐「持仓暴露」与「统计聚类」两类，并定义三者的合并规则。

## 1. 当前状态

| 维度 | 现状 |
|---|---|
| 经济先验簇 | **已定义 15 个**（`config/risk/risk_clusters.json`），含 `max_exposure` 与 `estimation_quality` |
| 持仓暴露簇 | **未形成**。`quant/ledgers/` 有持仓事实，但未聚合为「持仓暴露簇」 |
| 统计聚类簇 | **未接入**。Riskfolio-Lib 未引入，无 `RiskfolioClusterAnalyzer` |
| 三源合并 | **未实现**。`RiskClusterCalculator`（`quant/assets/risk_clusters.py:57`）仅读取经济先验 |
| 生产接入 | **未接入**。`quant/fund_pipeline.py` 不调用 `RiskClusterCalculator`（见 `docs/ARCHITECTURE_INFLUENCE_MATRIX.md` §8 GAP） |
| 立场 | 统计聚类**不得**覆盖经济先验与持仓事实（与 `R-X1` 一致） |

**结论：本 Wave 3 工作尚未启动，本文档为设计规格。**

## 2. 目标设计：三类风险簇定义

### 2.1 第一类：经济先验簇（Economic Prior Cluster）

**定义**：因为共同的经济成因，天然应该被一起约束的一组资产。判定依据是行业、主题、底层资产、货币 / 地域暴露，**不是**历史收益率的相关系数。

| 属性 | 值 |
|---|---|
| 数据来源 | 人工维护于 `config/risk/risk_clusters.json` + 资产目录 |
| 判定规则 | 行业 / 主题 / 底层资产 / 货币 / 地域 |
| 变更方式 | 代码评审，进版本控制 |
| 当前数量 | 15 个簇 |
| 谁负责 | 资产目录 + 人工评审 |
| Riskfolio-Lib 参与 | **否** |
| 优先级 | **最高**（不得被统计聚类覆盖） |

**为什么优先级最高**：经济成因是结构性事实。两只同为半导体主题的基金，在震荡市里日收益相关性可能只有 0.5，但它们的风险来源是同一个——相关性会在压力期突然收敛到 1，而统计聚类恰好在那时失效（见 `docs/ASSET_PERMISSION_AND_RISK_CLUSTERS.md` §2.2）。

### 2.2 第二类：持仓暴露簇（Position Exposure Cluster）

**定义**：基于账户**实际持仓**聚合形成的暴露簇。它回答的问题是：「我现在实际上已经集中在哪里？」——这是一个事实问题，不是预测问题。

| 属性 | 值 |
|---|---|
| 数据来源 | `quant/ledgers/` 的实际持仓事实 |
| 判定规则 | 按底层资产 / 基金经理 / 持仓重合度聚合 |
| 变更方式 | 随账户事实自动更新（账户事实优先原则） |
| 谁负责 | 账户事实层 |
| Riskfolio-Lib 参与 | **否** |
| 优先级 | **第二**（不得被统计聚类覆盖；不得覆盖经济先验的边界，但可作为先验的实证补充） |

**与经济先验的关系**：经济先验定义「应该约束什么」，持仓暴露定义「实际约束了什么」。两者可能不一致——例如经济先验定义了「半导体 20% 上限」，但当前持仓暴露显示半导体实际占 25%，这本身就是一次约束违反事件，必须触发 Gate 2 拒绝（见 `docs/PORTFOLIO_OPTIMIZATION_GOVERNANCE.md`）。

### 2.3 第三类：统计聚类簇（Statistical Cluster）

**定义**：基于历史收益相关性 / 协方差矩阵的层次聚类（HRP / HERC / NCO 的聚类阶段）产出的簇。它回答的问题是：「从历史收益结构看，哪些资产表现得像一类？」——这是一个**描述性**问题，不是**约束性**问题。

| 属性 | 值 |
|---|---|
| 数据来源 | 历史收益协方差矩阵（由 `RiskfolioClusterAnalyzer` 产出） |
| 判定规则 | 层次聚类（linkage + distance） |
| 变更方式 | 随估计窗口滚动更新 |
| 谁负责 | `RiskfolioClusterAnalyzer`（Riskfolio-Lib 实现） |
| Riskfolio-Lib 参与 | **是（仅此一类）** |
| 优先级 | **最低**（不得覆盖经济先验与持仓事实） |

**为什么优先级最低**：统计聚类的三个固有缺陷（见 `docs/ASSET_PERMISSION_AND_RISK_CLUSTERS.md` §2.2）：

1. **平静期低估关联** —— 把同主题资产错误拆开；
2. **压力期突然收敛** —— 恰好在最需要时失效；
3. **训练 / 使用时点错位** —— 用平静期数据训练、在压力期被依赖。

因此统计聚类只能作为**补充信号**，用于发现经济先验可能遗漏的关联（例如两只看似无关的基金实际重仓同一只个股），**不得**作为约束边界本身。

## 3. 目标设计：三源合并规则

### 3.1 合并优先级（硬性规则）

```
经济先验簇（最高） > 持仓暴露簇（第二） > 统计聚类簇（最低）
```

**具体规则：**

1. **经济先验的约束边界不得被统计聚类放宽**。例如经济先验定义「半导体 20% 上限」，统计聚类不得把其中两只半导体基金分到不同簇从而绕过 20% 上限。
2. **持仓暴露的事实不得被统计聚类否定**。例如持仓暴露显示某底层资产实际占 30%，统计聚类不得把它归入「低集中度簇」。
3. **统计聚类只能收紧约束，不能放宽**。例如统计聚类发现经济先验未识别的关联（持仓重合度高），可以把这些资产**合并到一个更紧的约束桶**，但不得把经济先验已合并的资产**拆开**。
4. **冲突时取更紧约束**。三源对同一组资产的约束不一致时，取 `max(economic_prior_constraint, position_exposure_constraint, statistical_cluster_constraint)` 的**更紧**值。

### 3.2 合并算法（目标设计）

```
输入：
  E = 经济先验簇集合（15 个，来自 config/risk/risk_clusters.json）
  P = 持仓暴露簇集合（来自 quant/ledgers/ 实时聚合）
  S = 统计聚类簇集合（来自 RiskfolioClusterAnalyzer）

步骤：
1. 以 E 为基础约束桶（不可拆分）。
2. 对每个 E_i in E，查找 P 中与 E_i 成员重合的簇，记录实际暴露。
   - 若实际暴露 > E_i.max_exposure，标记为约束违反事件。
3. 对每个 S_j in S，检查是否与某个 E_i 的成员高度重合：
   - 若重合度 ≥ 阈值（默认 80%），视为「统计确认经济先验」，不产生新约束。
   - 若重合度 < 阈值且 S_j 跨越多个 E_i，视为「统计发现新关联」：
     a) 检查这些资产是否在 P 中也有高持仓重合；
     b) 若 P 也确认重合，则把 S_j 的成员合并到一个更紧的约束桶（取相关 E_i 的 max_exposure 的最小值）；
     c) 若 P 不确认，仅记录为「统计提示」，不产生约束。
4. 输出：合并后的约束桶集合 C = E ∪ {统计确认的新约束桶}。

输出：
  C = 合并后的风险簇约束集合
  violations = 约束违反事件列表
  statistical_hints = 统计提示列表（不产生约束）
```

### 3.3 合并规则的硬性约束

- **统计聚类不得独立产生约束**（测试 #4）。任何约束必须经经济先验或持仓暴露确认；
- **持仓暴露的事实不得被任何源否定**（账户事实优先原则）；
- **合并过程必须可审计**：记录每个约束桶的来源（`E` / `P` / `E+S` / `E+P+S`）。

## 4. Riskfolio-Lib 的职责边界

| 能力 | 是否允许 | 落地位置 |
|---|---|---|
| 产出统计聚类簇 S | **允许**（仅此一类） | `RiskfolioClusterAnalyzer` |
| 产出 HRP / HERC / NCO 权重 | **允许**（仅 Shadow） | `RiskfolioHRPAllocator` 等 |
| 定义经济先验簇 E | **禁止** | 必须人工维护于 `config/risk/risk_clusters.json` |
| 修改持仓暴露簇 P | **禁止** | 持仓暴露来自账户事实，`quant/ledgers/` |
| 覆盖经济先验的约束边界 | **禁止**（测试 #4） | 见 §3.1 规则 1 |
| 把统计聚类作为唯一风险簇 | **禁止**（X-3） | 见 `docs/RISKFOLIO_INTEGRATION.md` §3.2 |
| 用短期相关性替代行业 / 主题 / 基金经理约束 | **禁止**（X-4） | 见 `docs/RISKFOLIO_INTEGRATION.md` §3.2 |

### 4.1 Riskfolio-Lib 的 HRP/HERC 立场

`docs/ARCHITECTURE_INFLUENCE_MATRIX.md` §6.3 `R-X1` 明确拒绝 HRP / HERC 纯统计聚类。本文档细化该立场：

- **拒绝的是**：把 HRP / HERC 的聚类结果**作为风险簇约束本身**；
- **不拒绝的是**：把 HRP / HERC 的聚类结果**作为第三类输入 S**，经三源合并后产生约束（见 §3.2）。

换言之，HRP / HERC 可以**贡献**统计聚类簇 S，但 S 不得独立成为约束。这是 `R-X1` 的精确边界。

## 5. 数据来源清单

| 类别 | 数据来源 | 版本控制 | 更新频率 |
|---|---|---|---|
| 经济先验 E | `config/risk/risk_clusters.json` + 资产目录 | Git 版本控制 | 人工评审触发 |
| 持仓暴露 P | `quant/ledgers/` 实际持仓 | 账户事实账本 | 每次持仓变更 |
| 统计聚类 S | `RiskfolioClusterAnalyzer`（Riskfolio-Lib） | `input_data_version` + `estimation_window` | 随估计窗口滚动 |

### 5.1 数据一致性要求

- 持仓暴露 P 的数据版本必须与 `AllocationResult.input_data_version` 一致（测试 #8）；
- 统计聚类 S 的 `estimation_window` 必须与 `AllocationResult.estimation_window` 一致；
- 经济先验 E 的版本必须与 `AllocationResult.parameter_version` 一致，不一致视为配置漂移。

## 6. 差距分析

| 目标设计 | 当前状态 | 差距 |
|---|---|---|
| 经济先验簇 E | 15 个簇已定义 | 已实现（`TESTED_BUT_NO_PROD_CALLER`，见 `docs/ASSET_PERMISSION_AND_RISK_CLUSTERS.md`） |
| 持仓暴露簇 P | 未形成 | 待实现聚合逻辑 |
| 统计聚类簇 S | 未接入 | 待引入 `RiskfolioClusterAnalyzer` |
| 三源合并算法 | 未实现 | 待实现 §3.2 算法 |
| 合并可审计 | 未实现 | 待实现来源标记 |
| 生产接入 | 未接入 | `quant/fund_pipeline.py` 需调用合并后的簇校验 |
| 统计聚类不得独立产生约束（测试 #4） | 无统计聚类 | 待实现后验证 |

## 7. 与治理管线的对接

合并后的风险簇约束集合 C 进入 `docs/PORTFOLIO_OPTIMIZATION_GOVERNANCE.md` 的 **Gate 2（Risk Governance）**：

1. Gate 2 读取 C 与 `AllocationResult.weights`；
2. 计算每个约束桶的实际暴露；
3. 若某桶暴露 > `max_exposure`，标记为约束违反，`constraint_violations` 非空；
4. Gate 2 拒绝该 `AllocationResult` 进入 Gate 3。

**关键约束**：Gate 2 校验的是**合并后的 C**，不是统计聚类 S 单独。任何只校验 S 的代码路径视为违反 §3.1 规则 1（测试 #4）。

## 8. 诚实标记

本文档描述的持仓暴露簇、统计聚类簇、三源合并算法在 V4.6 首版中**均未实现**，状态为 `DEFERRED_BY_SCOPE (Wave 3)`。经济先验簇已定义但未接入生产（`TESTED_BUT_NO_PROD_CALLER`）。

在以下条件全部满足前，本文档任何条目不得被表述为「已实现」：

1. 三源合并算法有实现且有测试；
2. 统计聚类 S 经合并后才能产生约束（测试 #4 通过）；
3. 合并后的 C 在 Gate 2 真实执行（非仅测试调用）；
4. 持仓暴露 P 的数据版本与 `AllocationResult` 一致（测试 #8 通过）。

## 9. 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-08-02 | v1.0 | 初版。定义三类风险簇（经济先验 / 持仓暴露 / 统计聚类）、三源合并规则、Riskfolio-Lib 职责边界。状态 DEFERRED_BY_SCOPE (Wave 3)。 |

---

## 对应验收条件

- **#3 经济 / 持仓 / 统计风险簇已明确分离** —— §2 三类定义；§3 合并规则；§4 职责边界。
- **#1 通过统一适配器调用** —— §4 统计聚类通过 `RiskfolioClusterAnalyzer` 接口产出，不直接调用 Riskfolio-Lib。
- **#4 求解器和数据输入可审计** —— §5.1 数据一致性要求；三源版本对齐。
- **#10 所有未完成能力诚实标记为 PARTIAL 或 DEFERRED_BY_SCOPE** —— §8 诚实标记段落。
