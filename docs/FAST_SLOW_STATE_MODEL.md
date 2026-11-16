# V4.6 快慢双状态模型（Fast / Slow State Model）

> 文档编号：ARCH-STATE-001
> 版本：v1.0
> 生成日期：2026-08-01
> 对应需求：补充约束 Prompt 第三节「系统状态必须拆成快慢两层，不得用一个变量表达」
> 关联文档：`docs/V46_REQUIREMENTS_TRACEABILITY.md`、`docs/MARKET_OBSERVATION_LAYERS.md`、`docs/ORTHOGONAL_AXES.md`

---

## 1. 本文档的作用

本文档规定 V4.6 用**两个独立状态变量**表达市场：慢状态（结构性、需迟滞确认，决定战略仓位区间）与快状态（突发风险、要求立即响应，决定是否紧急降险/冻结交易）。
它给出两者的取值域、切换规则、以及「取更保守者」的合成规则，并诚实盘点当前实现：**迟滞与最小停留期的代码确实存在，但全部位于无生产调用方的孤立模块；生产链路上跑的是两个无状态纯函数，逐日重算、无记忆、无证据链。**

---

## 2. 目标设计

### 2.1 为什么必须拆成两层

用一个变量表达市场，会同时要求它「反应够快」和「够稳定」——这两个要求在数学上互斥。拆开后各自优化：

| | 慢状态 Slow State | 快状态 Fast State |
|---|---|---|
| 回答的问题 | 市场处在什么**结构**里 | 现在有没有**急事** |
| 载体 | `MarketRegime` | `SentinelLevel`（+ 待补的 `DATA_ERROR`） |
| 变化频率 | 数天到数周 | 分钟到单日 |
| 切换要求 | **双向都要迟滞**，且有最小停留期 | **单向快**：收紧即时，放松需连续确认 |
| 输出语义 | 战略仓位**区间**（例如权益 0–30%） | 是否**紧急降险 / 冻结开仓** |
| 误报代价 | 频繁调仓 → 摩擦成本 | 错过风险 → 本金损失 |
| 因此偏向 | 宁可慢，不可抖 | 宁可早，不可漏 |
| 轴归属 | 状态机与迟滞＝ENGINEERING_AXIS；区间数值＝STRATEGY_AXIS | 同左 |

### 2.2 慢状态定义（Slow State）

取值域沿用 `quant/domain/enums.py:101-107`：

| 取值 | 语义 | 最小停留期（建议） | 进入所需证据 |
|---|---|---|---|
| `TREND_RISK_ON` | 趋势确立，可承担风险 | 5 个交易日 | 多指数趋势一致为正 + 宽度健康 |
| `RANGE_NEUTRAL` | 震荡无方向 | 3 个交易日 | 趋势与宽度均无一致信号 |
| `STRESS_RISK_OFF` | 系统性压力 | 2 个交易日（**只对退出生效**，进入不受限） | 趋势转负 + 宽度恶化 + 波动抬升 |
| `RECOVERY_PROBE` | 压力缓解但未确认 | 3 个交易日 | 从 STRESS 退出的强制过渡态 |
| `UNKNOWN` | **不可判断** | 无（立即生效） | 输入指标缺失、陈旧或质量不合格 |

关键约束：

1. **`UNKNOWN` 必须是可达输出。** 数据不足时输出 `UNKNOWN`，由下游按最保守方式处理（等同 `STRESS_RISK_OFF` 的仓位约束），**不得**静默落到 `RANGE_NEUTRAL`。
2. **进入 `STRESS_RISK_OFF` 不受最小停留期限制**（风险可以立即进入）；**退出**必须同时满足「已停留 ≥ 最小停留期」与「连续 N 日确认」。
3. **不允许跨状态跳跃退出**：`STRESS_RISK_OFF` 只能先到 `RECOVERY_PROBE`，不能一步回到 `TREND_RISK_ON`。
4. **每次切换必须写入证据链**（见 §2.5）。

### 2.3 快状态定义（Fast State）

现有取值域 `quant/sentinel/levels.py:26-31`，**目标态需增加一档**：

| 取值 | 序数 | 语义 | 目标行为 |
|---|---|---|---|
| `GREEN` | 0 | 常态 | 允许正常运行与开仓 |
| `YELLOW` | 1 | 警戒 | 收紧风险、提高复核频率 |
| `ORANGE` | 2 | 高危 | 削减敞口、暂停新增开仓 |
| `RED` | 3 | 危机 | Risk-Off、只减不加 |
| **`DATA_ERROR`** | **待定（建议 4，最高）** | **观测失效：指标缺失/陈旧/质量不合格，系统「看不见」市场** | **冻结全部交易，Fail-Closed 转人工** | 

为什么 `DATA_ERROR` 必须是**独立且最高**的一档，而不是复用 `RED`：

- `RED` 的语义是「我看见了风险」，可依据风险大小做减仓；
- `DATA_ERROR` 的语义是「我看不见」，此时**任何**方向的操作都缺乏依据，连「减仓」都可能是错的（可能正在减一个不存在的风险，付出真实摩擦成本）；
- 两者的解除条件也不同：`RED` 靠市场平复解除，`DATA_ERROR` 靠数据修复解除，不应共用同一套降级计数。

快状态切换规则：

| 方向 | 规则 |
|---|---|
| 收紧（等级上升） | **即时到位**，一次读数即可跳升，可跨级（GREEN → RED 允许） |
| 放松（等级下降） | 需连续 N 次原始等级更低的确认，且**每次只降一级**；任何一次回升立即清零计数 |
| 进入 `DATA_ERROR` | 即时，且**优先级最高**，覆盖任何指标计算结果 |
| 退出 `DATA_ERROR` | 必须先满足「数据质量闸门通过」，再重新走一次完整评估；不走降级计数通道 |

### 2.4 合成规则：取更保守者（不得互相覆写）

两个状态**各自独立演进**，只在下游消费时合成一次。禁止任何一侧回写另一侧。

```
                 ┌── 慢状态 MarketRegime ──→ 战略仓位区间上限  cap_slow
最终仓位上限 = min(                                                    )
                 └── 快状态 SentinelLevel ─→ 快速风险仓位上限  cap_fast
```

| 规则 | 说明 |
|---|---|
| **R1 取小不取大** | 最终约束 = `min(cap_slow, cap_fast)`；两者都能收紧，谁都不能放松对方 |
| **R2 单向禁止回写** | 快状态转 RED 不得改写 `MarketRegime` 的值；慢状态转 RISK_ON 不得降低哨兵等级 |
| **R3 不可判断吸收一切** | 任一侧为 `UNKNOWN` / `DATA_ERROR` 时，合成结果直接取「冻结交易」，不再参与 min 运算 |
| **R4 合成过程无副作用** | 合成函数是纯函数，输入两个状态 + 两份证据，输出一个约束 + 合并证据；不修改任何状态机内部计数 |
| **R5 合成结果必须落盘** | 每次合成写入运行快照，含两侧输入状态、两侧证据、最终约束与「谁是约束方」 |

合成矩阵（示意，具体数值属 STRATEGY_AXIS，应由策略配置提供）：

| 慢 \ 快 | GREEN | YELLOW | ORANGE | RED | DATA_ERROR |
|---|---|---|---|---|---|
| `TREND_RISK_ON` | 区间上限 | 收紧一档 | 暂停开仓 | 只减不加 | 冻结 |
| `RANGE_NEUTRAL` | 中性上限 | 收紧一档 | 暂停开仓 | 只减不加 | 冻结 |
| `RECOVERY_PROBE` | 试探上限 | 收紧一档 | 暂停开仓 | 只减不加 | 冻结 |
| `STRESS_RISK_OFF` | 防御上限 | 防御上限 | 暂停开仓 | 只减不加 | 冻结 |
| `UNKNOWN` | 冻结 | 冻结 | 冻结 | 冻结 | 冻结 |

### 2.5 证据链要求

每一次状态切换（快或慢）都必须产出一条**结构化**记录，且能独立回答「凭什么切」：

| 字段 | 含义 | 为什么必需 |
|---|---|---|
| `from` / `to` | 切换前后状态 | 基本事实 |
| `as_of` | 判定所用数据的截止日期 | 区分「今天判的」与「用今天数据判的」 |
| `triggering_indicators` | 触发指标列表，每项含 `名称 / 数值 / 阈值 / 方向` | 中文散文理由无法机器复核 |
| `data_lineage_refs` | 所依赖 L1 数据的血缘引用（content_hash） | 事后能重放同一批数据 |
| `dwell_days` | 切换前在原状态停留的天数 | 校验最小停留期是否被绕过 |
| `confirmation_streak` | 当前确认计数 / 所需计数 | 校验迟滞是否被绕过 |
| `decided_by` | 规则版本或人工 actor | 追责 |

**硬性要求：证据链缺任一字段即视为无效切换，状态保持不变（Fail-Closed）。**

---

## 3. 当前实现现状（诚实盘点）

### 3.1 慢状态（Slow State）

| 能力项 | 现状 | 证据（文件:行号） | 状态标签 |
|---|---|---|---|
| 五值 `MarketRegime` 枚举 | 存在，取值与目标设计一致 | `quant/domain/enums.py:101-107` | `TESTED_BUT_NO_PROD_CALLER` |
| 生产实际使用的慢状态 | **不是 5 值枚举**，而是旧的 3 值 `Regime`（RISK_ON/NEUTRAL/RISK_OFF） | `quant/models.py:23-26`；生产消费点 `quant/fund_pipeline.py:196,208,216,242`、`quant/strategies/tactical_etf/engine.py:80,103-104` | `TESTED_AND_WIRED`（但是旧模型） |
| 新旧枚举映射 | 映射表存在，但只覆盖 3→3，`RECOVERY_PROBE`/`UNKNOWN` 无来源 | `quant/domain/enums.py:153-157` | `PARTIAL` |
| `UNKNOWN` 作为可达输出 | **不可达**。仅作为 `RegimeEnsemble` 的初始占位；`compute()` 的四个分支只产出其余 4 值，切换保护也只返回 `STRESS_RISK_OFF`/`RECOVERY_PROBE`/proposed | `quant/regimes/regime_ensemble.py:82`（唯一赋值处）、`:117-128`（四分支无 UNKNOWN）、`:200-233`（返回值无 UNKNOWN） | `NOT_FOUND` |
| 慢状态迟滞（快进慢出） | **代码存在**：进入 RISK_OFF 快速、退出需确认 | `quant/regimes/regime_ensemble.py:200-233`（`:207-211` 快进、`:214-229` 慢出） | `TESTED_BUT_NO_PROD_CALLER` |
| 慢状态最小停留期 | **代码存在**：`MIN_RISK_OFF_DAYS=2`、`RECOVERY_CONFIRM_DAYS=3` | `quant/regimes/regime_ensemble.py:78-79`，判定逻辑 `:216-222` | `TESTED_BUT_NO_PROD_CALLER` |
| 禁止跨状态跳跃退出 | 存在：从 RISK_OFF 退出被强制落到 `RECOVERY_PROBE` | `quant/regimes/regime_ensemble.py:225-229` | `TESTED_BUT_NO_PROD_CALLER` |
| 状态有效期（valid_until） | 存在，按状态给 1/3/5 天 | `quant/regimes/regime_ensemble.py:240-249` | `TESTED_BUT_NO_PROD_CALLER` |
| **生产链路的慢状态迟滞** | **不存在**。生产用的是两个**无状态纯函数**，每日独立重算，不记忆前一日状态，也就不可能有迟滞或停留期 | `quant/fund_regime.py:12-33`（`fund_nav_regime`，无 self、无历史状态）；`quant/regime.py:12-35`（`core_regime`，同样无状态） | `NOT_FOUND` |
| 慢状态数据缺失 → `UNKNOWN` | **不存在，且方向相反**：三处一致地静默落到中性 | ①`quant/strategies/tactical_etf/engine.py:74-80`（取数失败 → 空 DataFrame → `Regime.NEUTRAL`）②`quant/fund_pipeline.py:192-194`（基准不可用 → `Regime.NEUTRAL`，且 metrics 为空 `{}`）③`quant/fund_regime.py:19-20`（历史 <120 日 → `Regime.NEUTRAL`）④`quant/regimes/regime_ensemble.py:170,182,193,198`（各维度无输入一律返回 0.5 → 合成 0.5 → `RANGE_NEUTRAL`） | `NOT_FOUND` |
| 慢状态证据链 | 部分：`RegimeResult` 带 `reasons` + `metrics` 且随决策日志落盘 | `quant/models.py:64-76`（`to_dict` 含 metrics）；写盘 `quant/fund_pipeline.py:375,410,440` | `PARTIAL` |
| 证据链的结构化要素（阈值/方向/停留天数/确认计数/血缘引用） | 不存在。`reasons` 是中文散文串，`metrics` 只有裸数值无阈值 | `quant/fund_regime.py:28-33`（metrics 无阈值字段）；`quant/regimes/regime_ensemble.py:119-128`（reasons 为格式化字符串） | `NOT_FOUND` |
| 战术 ETF 链路的慢状态证据 | 更弱：输出对象只保留 `regime_label` 字符串，指标全部丢弃 | `quant/strategies/tactical_etf/engine.py:44-51`（`to_dict` 无 metrics）；`app/run_tactical_etf.py:16`（快照 metrics 只有 regime 标签） | `NOT_FOUND` |

### 3.2 快状态（Fast State）

| 能力项 | 现状 | 证据（文件:行号） | 状态标签 |
|---|---|---|---|
| 四级 `SentinelLevel` 枚举 | 存在（GREEN/YELLOW/ORANGE/RED） | `quant/sentinel/levels.py:26-31` | `TESTED_BUT_NO_PROD_CALLER` |
| **`DATA_ERROR` 档** | **缺失**。枚举只有 4 个成员，且验收条件把「恰为 4 级」写死为通过条件，等于把缺陷固化 | `quant/sentinel/levels.py:26-31`；`app/v46_acceptance.py:172-175`（`assert len(list(SentinelLevel)) == 4`） | `NOT_FOUND` |
| 快状态非对称迟滞（升即时/降慢） | **代码存在且逻辑正确**：升级即时并重置计数、降级需连续确认且每次只降一级 | `quant/sentinel/sentinel.py:157-161`（升）、`:162-177`（降）、`:178-180`（同级打断计数）；`quant/sentinel/levels.py:53-57`（每次只降一级） | `TESTED_BUT_NO_PROD_CALLER` |
| 降级确认次数可配置 | 存在，默认 2 | `quant/sentinel/sentinel.py:139-141` | `TESTED_BUT_NO_PROD_CALLER` |
| 事件强制升级（黑天鹅） | 存在，已确认事件可直接推到 RED；未确认事件返回 0 不生效 | `quant/sentinel/events.py:56-69`、`:113-117`；合成 `quant/sentinel/sentinel.py:99-105` | `TESTED_BUT_NO_PROD_CALLER` |
| 快状态决策可入快照 | 结构存在（`SentinelDecision.to_dict`），但无人调用 | `quant/sentinel/sentinel.py:109-127` | `TESTED_BUT_NO_PROD_CALLER` |
| **快状态在生产链路的调用** | **零调用**。全仓库引用 `MarketSentinel`/`SentinelLevel`/`SentinelReading` 的文件只有：验收脚本、测试、哨兵包自身、文档 | grep `MarketSentinel|SentinelLevel|compute_raw_level|SentinelReading` 命中文件：`app/v46_acceptance.py`、`tests/test_market_sentinel.py`、`quant/sentinel/{levels,sentinel,events,__init__}.py`、`docs/architecture/V46_SCHEDULER_AND_SENTINEL.md`——**生产入口 `quant/fund_pipeline.py` 与 `quant/strategies/tactical_etf/engine.py` 均不在其中** | `TESTED_BUT_NO_PROD_CALLER` |
| 指标全缺时的快状态 | **Fail-Open 缺陷**：所有指标为 None 时 `scores` 为空列表，等级直接判为 0 = GREEN | `quant/sentinel/sentinel.py:97` `indicator_level = max(scores) if scores else 0` | `BLOCKED`（必须先修复才能接入生产） |
| 策略参数混入工程枚举 | `allows_new_entries` / `is_risk_off` 把「ORANGE 以上停开仓」硬编码在枚举属性上 | `quant/sentinel/levels.py:38-45` | `PARTIAL`（见 `docs/ORTHOGONAL_AXES.md` GAP-A3） |

### 3.3 两状态合成

| 能力项 | 现状 | 证据（文件:行号） | 状态标签 |
|---|---|---|---|
| 合成函数（取更保守者） | 不存在。全仓库无任何函数同时接收 `MarketRegime`（或 `Regime`）与 `SentinelLevel` | 生产决策点 `quant/fund_pipeline.py:216`（`apply_regime_caps` 只吃 regime）、`:242-244`（risk_override 只看 `Regime.RISK_OFF`）；哨兵零调用（见 §3.2） | `NOT_FOUND` |
| 禁止互相覆写 | 无从谈起（两者从未同时存在于同一调用栈） | 同上 | `NOT_FOUND` |
| 不可判断吸收一切（R3） | 不存在。慢状态无 `UNKNOWN` 输出、快状态无 `DATA_ERROR` 档 | `quant/regimes/regime_ensemble.py:117-128`；`quant/sentinel/levels.py:26-31` | `NOT_FOUND` |
| 合成结果落盘 | 不存在 | `app/common.py:52-79` 快照只写 payload 与 `as_of` 参数指纹（`:72`） | `NOT_FOUND` |
| 哨兵内部的「取更高者」 | 存在，但只是**指标 vs 事件**两个快状态输入之间的合成，不是快慢合成 | `quant/sentinel/sentinel.py:105` `raw = max(indicator_level, event_level)` | `TESTED_BUT_NO_PROD_CALLER` |

### 3.4 对 Prompt 原始判断的一处更正（必须记录）

任务简报中写「迟滞、最小停留期、证据链目前**都不存在**」。经逐行核对，更准确的表述是：

| 项 | 简报表述 | 核对后的事实 | 结论 |
|---|---|---|---|
| 快状态迟滞 | 不存在 | **存在**，`quant/sentinel/sentinel.py:157-180`，且有验收条件 `app/v46_acceptance.py:178-189` 覆盖 | 更正为 `TESTED_BUT_NO_PROD_CALLER` |
| 慢状态迟滞 + 最小停留期 | 不存在 | **存在**，`quant/regimes/regime_ensemble.py:78-79,200-233`，有测试 `tests/test_features_regime.py:93-115`，但**无任何验收条件覆盖，也无生产调用方** | 更正为 `TESTED_BUT_NO_PROD_CALLER` |
| 证据链 | 不存在 | 生产侧有 `reasons` + `metrics` 落盘（`quant/models.py:64-76`），但缺阈值、停留天数、确认计数、血缘引用四类关键字段 | 更正为 `PARTIAL` |

**这一更正不减轻问题的严重性，反而加重了它**：迟滞机制不是「还没写」，而是「写了、测了、然后没有人用」。生产上跑的仍是 `fund_nav_regime()` 与 `core_regime()` 两个无记忆纯函数——它们连「昨天是什么状态」都不知道，物理上不可能有迟滞。这是典型的「两个平行世界」（见 `docs/ARCHITECTURE_INFLUENCE_MATRIX.md` §8）。

---

## 4. 差距与落地计划

| 编号 | 差距 | 归属轴 | 优先级 | 落地动作 |
|---|---|---|---|---|
| GAP-S1 | 快状态缺 `DATA_ERROR` 档，且验收条件把「恰 4 级」写死 | ENGINEERING_AXIS | **P0** | 新增 `DATA_ERROR`（序数最高）；同步把 `app/v46_acceptance.py:174` 的 `== 4` 改为 `== 5` 并补一条「数据失效必须能表达」的条件 |
| GAP-S2 | `quant/sentinel/sentinel.py:97` 指标全缺判 GREEN（Fail-Open） | ENGINEERING_AXIS | **P0** | 改为：`scores` 为空 → 返回 `DATA_ERROR`；部分缺失 → 返回 `DATA_ERROR` 或按缺失比例阈值降权，绝不返回 0 |
| GAP-S3 | 慢状态数据缺失静默落 `NEUTRAL`（四处） | ENGINEERING_AXIS | **P0** | 四处统一改为输出 `UNKNOWN`：`tactical_etf/engine.py:74-80`、`fund_pipeline.py:192-194`、`fund_regime.py:19-20`、`regime_ensemble.py:170,182,193,198` |
| GAP-S4 | `MarketRegime.UNKNOWN` 为不可达输出 | ENGINEERING_AXIS | **P0** | 在 `RegimeEnsemble.compute()` 前置输入完整性检查，不满足即返回 `UNKNOWN` 并短路后续打分（`regime_ensemble.py:89-158`） |
| GAP-S5 | 证据链缺阈值 / 停留天数 / 确认计数 / 血缘引用 | ENGINEERING_AXIS | **P0** | 定义 `StateTransitionEvidence` 结构体，按 §2.5 七字段落盘；缺字段即拒绝切换 |
| GAP-S6 | 快状态零生产调用方 | ENGINEERING_AXIS | **P1** | 在 `quant/fund_pipeline.py` 决策闸门前接入 `MarketSentinel.evaluate()`，其输出参与 §2.4 合成（前置依赖：GAP-S2 必须先修） |
| GAP-S7 | 慢状态迟滞/停留期零生产调用方；生产用无状态纯函数 | ENGINEERING_AXIS | **P1** | 用 `RegimeEnsemble` 替换生产链路的 `fund_nav_regime()`/`core_regime()`，并把状态机的持久化状态（`_prev_regime`/`_risk_off_start`/`_recovery_streak`）落到可审计存储，而非进程内存 |
| GAP-S8 | 状态机状态存于进程内存，重启即丢失 | ENGINEERING_AXIS | **P1** | `RegimeEnsemble.__init__`（`regime_ensemble.py:81-87`）与 `MarketSentinel.__init__`（`sentinel.py:139-142`）的内部计数必须可持久化、可从快照恢复；否则每次运行都从 `UNKNOWN`/`GREEN` 重新开始，迟滞形同虚设 |
| GAP-S9 | 无快慢合成函数 | BOTH | **P1** | 新增纯函数 `compose_state_constraint(slow, slow_evidence, fast, fast_evidence) -> Constraint`，实现 R1–R5 |
| GAP-S10 | 生产慢状态仍是 3 值旧枚举 | STRATEGY_AXIS | **P2** | 生产链路迁移到 5 值 `MarketRegime`；`REGIME_LEGACY_MAP`（`enums.py:153-157`）降级为只读兼容层 |
| GAP-S11 | 最小停留期/确认次数是类常量，非配置 | STRATEGY_AXIS | **P2** | `MIN_RISK_OFF_DAYS`/`RECOVERY_CONFIRM_DAYS`（`regime_ensemble.py:78-79`）与 `deescalation_confirmations`（`sentinel.py:139`）外移到策略配置 |
| GAP-S12 | 慢状态迟滞无验收条件覆盖 | ENGINEERING_AXIS | **P2** | 补一条与 `c14_asymmetric_hysteresis`（`app/v46_acceptance.py:178-189`）对称的慢状态迟滞验收条件 |
| GAP-S13 | 状态切换历史无时间序列留痕，无法回放 | ENGINEERING_AXIS | **P3** | 状态切换事件独立成一份 append-only 日志，支持按日期区间回放 |
| GAP-S14 | 缺「状态切换频率」监控（抖动检测） | ENGINEERING_AXIS | **P4** | 已有 `_transition_count` 计数器但只自增不消费（`regime_ensemble.py:86,146`）；改为滚动窗口抖动率并在超阈值时告警 |

---

## 5. 验收方式

### 5.1 慢状态

| 编号 | 验收条件（给定…当…则…） |
|---|---|
| AC-STATE-01 | **给定** 基准数据缺失或未通过质量闸门，**当** 计算慢状态，**则** 输出必须为 `MarketRegime.UNKNOWN`；输出 `RANGE_NEUTRAL` 或 `NEUTRAL` 即判 FAIL |
| AC-STATE-02 | **给定** 慢状态刚进入 `STRESS_RISK_OFF` 第 1 天，**当** 次日输入转为强烈看多，**则** 状态必须仍为 `STRESS_RISK_OFF`（最小停留期未满），且证据链中 `dwell_days` 字段可读 |
| AC-STATE-03 | **给定** 慢状态已在 `STRESS_RISK_OFF` 停留满最小停留期，**当** 后续输入连续看多但确认天数未达 `RECOVERY_CONFIRM_DAYS`，**则** 状态只能落到 `RECOVERY_PROBE`，不得直接回到 `TREND_RISK_ON` |
| AC-STATE-04 | **给定** 输入急剧恶化，**当** 慢状态处于任意非 STRESS 状态，**则** 必须**当次**即切到 `STRESS_RISK_OFF`（进入不受停留期与确认次数限制） |
| AC-STATE-05 | **给定** 进程重启，**当** 从快照恢复状态机，**则** `_prev_regime`、`_risk_off_start`、`_recovery_streak` 必须与重启前一致；恢复后第一次评估的结果必须与不重启时相同 |
| AC-STATE-06 | **给定** 任意一次慢状态切换，**当** 读取其证据链，**则** §2.5 的七个字段必须全部非空；缺任一字段则状态保持不变且记录一条拒绝原因 |

### 5.2 快状态

| 编号 | 验收条件（给定…当…则…） |
|---|---|
| AC-STATE-07 | **给定** `SentinelReading` 的全部指标字段均为 `None` 且无已确认事件，**当** 调用 `compute_raw_level`，**则** 结果必须为 `DATA_ERROR`；结果为 `GREEN` 即判 FAIL（直接针对 `quant/sentinel/sentinel.py:97`） |
| AC-STATE-08 | **给定** 快状态为 `GREEN`，**当** 一次读数触发 RED 阈值，**则** 必须在**同一次评估内**跳到 `RED`（收紧即时、允许跨级） |
| AC-STATE-09 | **给定** 快状态为 `RED`，**当** 连续输入平静读数，**则** 第 1 次不得降级；达到确认次数后只能降到 `ORANGE`，不得一步回 `GREEN` |
| AC-STATE-10 | **给定** 快状态处于降级确认中（streak > 0），**当** 出现一次原始等级回升，**则** 确认计数必须清零 |
| AC-STATE-11 | **给定** 快状态为 `DATA_ERROR`，**当** 后续读数恢复正常但数据质量闸门仍未通过，**则** 状态必须保持 `DATA_ERROR`，不得通过降级计数通道退出 |
| AC-STATE-12 | **给定** 一次完整的生产运行（`app/run_daily.py` 或 `app/run_core_fund.py`），**当** 检查运行快照，**则** 必须能读到本次的快状态等级与其证据；读不到即说明哨兵仍未接入生产链路，FAIL |

### 5.3 合成

| 编号 | 验收条件（给定…当…则…） |
|---|---|
| AC-STATE-13 | **给定** 慢状态 `TREND_RISK_ON` 且快状态 `RED`，**当** 计算最终仓位约束，**则** 结果必须等于「只减不加」（取更保守者），而不是 RISK_ON 的区间上限 |
| AC-STATE-14 | **给定** 慢状态 `STRESS_RISK_OFF` 且快状态 `GREEN`，**当** 计算最终仓位约束，**则** 结果必须等于防御上限；快状态的 GREEN 不得放松慢状态的约束 |
| AC-STATE-15 | **给定** 任一侧为 `UNKNOWN` 或 `DATA_ERROR`，**当** 计算最终约束，**则** 结果必须为「冻结交易」，且不进入 min 运算 |
| AC-STATE-16 | **给定** 一次合成调用，**当** 调用前后分别读取两个状态机的内部计数，**则** 必须完全不变（合成是纯函数，R4） |
| AC-STATE-17 | **给定** 一次合成结果，**当** 读取运行快照，**则** 必须能读到「慢状态值、快状态值、两侧证据、最终约束、约束方是谁」五项；缺任一项 FAIL |

---

## 6. 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-08-01 | v1.0 | 初版。定义快慢双状态取值域、切换规则、取更保守者合成规则 R1–R5 与七字段证据链；提出补充 `DATA_ERROR` 档；完成现状盘点并对「迟滞/停留期/证据链均不存在」做出更正（实为已实现但零生产调用方）；列出 GAP-S1..S14 与 AC-STATE-01..17 |
