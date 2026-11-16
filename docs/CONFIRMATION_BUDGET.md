# 确认预算（Confirmation Budget）

> 文档编号：ARCH-CONFBUDGET-001
> 版本：v1.0
> 生成日期：2026-08-01
> 对应需求：补充约束 Prompt 第十六节「确认预算：试探是有限资源」
> 关联文档：`docs/V46_REQUIREMENTS_TRACEABILITY.md`、`docs/ARCHITECTURE_INFLUENCE_MATRIX.md`

---

## 0. 本文档的作用

系统在「不确定是否该行动」时，会本能地采取两种坏策略：一是**无限次试探**（这次探仓亏了，再探一次，总有一次对），二是**无限期等待**（信号不够强就一直挂着，直到某天它突然被当成有效信号执行）。两者都会在压力期造成不可控的累积损失。

本文档定义**确认预算**：把「试探」和「等待」显式建模成**有限的、会被消耗的、消耗过程留痕的资源**。预算耗尽即强制回落到保守态，而不是继续试。

本文档同时诚实盘点：**V4.6 目前不存在任何确认预算机制**（§3 全表证据）。生产链上只有若干**节奏控制**与**数据时效**机制，它们看起来像预算，但没有「消耗—扣减—耗尽」语义。

---

## 1. 目标设计

### 1.1 三类预算

确认预算由三个正交维度构成，任一维度耗尽即触发回落。

| 维度 | 定义 | 消耗方式 | 耗尽后的动作 |
|---|---|---|---|
| **时间预算（TTL）** | 一个「待确认」判断最多允许挂多久 | 每经过一个 NAV 观测日 / 交易日扣减 1 | 判断作废，回落到保守态（现金/防御），**不得**被后续运行当作已确认信号消费 |
| **次数预算（Probe Count）** | 同一方向、同一标的的试探性建仓最多允许几次 | 每生成一次试探性订单扣减 1 | 该方向进入冷却期，冷却期内任何试探请求直接拒绝 |
| **额度预算（Probe Exposure）** | 全部试探性仓位合计占总资产的上限 | 按试探仓位权重占用 | 超限时新试探请求被拒绝，已有试探仓位不被强制平掉（避免用坏数据决定卖出） |

### 1.2 关键定义

- **待确认判断（Pending Confirmation）**：信号已产生但未满足执行门槛（如排名领先幅度不足、两日稳定性未满足、数据质量存疑）的决策候选。它**必须**带一个显式失效时间，不允许无限期存在。
- **试探性建仓（Probe Position）**：在 `RECOVERY_PROBE` 一类「方向未确认但可能已反转」的状态下建立的、**明确标记为可被快速放弃**的小额仓位。它与正常建仓在账本上必须可区分。
- **冷却期（Cooldown）**：次数预算耗尽后的强制静默窗口。冷却期长度应长于一个完整的申赎确认周期（场外基金 ≥ T+2），否则「冷却」只是形式。
- **预算留痕（Budget Ledger）**：每一次预算消耗必须记录 `运行键 / 维度 / 消耗量 / 剩余量 / 消耗原因`，并落到 run 快照。**无留痕的消耗等于没有预算**——因为下一次运行无法知道自己还剩多少。

### 1.3 预算参数建议值

| 参数 | 建议值 | 依据 |
|---|---|---|
| `pending_confirmation_ttl_nav_days` | 3 | 与 `RECOVERY_CONFIRM_DAYS = 3`（`quant/regimes/regime_ensemble.py:79`）对齐 |
| `probe_count_per_direction` | 2 | 两次不成立即认为方向判断本身有问题，而非运气不好 |
| `probe_cooldown_nav_days` | 10 | 需覆盖 T+2 确认 + 观察窗口；与 `neutral_or_weak_interval_nav_days` 默认 10（`quant/fund_schedule.py:31`）同量级 |
| `probe_total_exposure_cap` | 0.10 | 试探合计不超过总资产 10%，单次试探不超过 5% |

> 上述值必须写进 `config/` 并纳入 `docs/UNFROZEN_PARAMETERS.md` 管理，不得硬编码在代码里。

### 1.4 与 `RECOVERY_PROBE` 状态的关系

`MarketRegime.RECOVERY_PROBE`（`quant/domain/enums.py:106`）在命名上就承诺了「探仓」语义。但目前它**没有任何配套的探仓授权与预算**：

- `quant/decision/conflict_resolver.py:166` 把 `RECOVERY_PROBE` 直接映射为 `StrategyDirection.NEUTRAL`；
- 也就是说它与 `RANGE_NEUTRAL`（同文件 `:165`）行为完全一致，**探仓状态并不真的允许探仓**。

目标设计要求：`RECOVERY_PROBE` 必须是**唯一**可以消耗试探次数预算的状态；进入该状态时初始化预算，离开时结算并留痕。

---

## 2. 规则表：预算检查在链路上的位置

| 阶段 | 检查项 | 失败动作 |
|---|---|---|
| 信号产生后 | 为「未达执行门槛」的候选打上 TTL | 无 TTL 的待确认候选拒绝入库 |
| 每次运行开始 | 扫描所有待确认候选，扣减 TTL | TTL ≤ 0 → 作废并写入失效原因 |
| 决策生成试探订单前 | 查次数预算与冷却状态 | 预算为 0 或冷却中 → 拒绝生成，写入拒绝原因 |
| 订单进入执行前 | 查试探总额度 | 超限 → 拒绝新试探 |
| 运行结束 | 把三类预算余额写入 run 快照 | 未写入视为运行不完整 |

---

## 3. 当前实现现状（诚实盘点）

### 3.1 核对方法

```bash
grep -rniE "ConfirmationBudget|confirmation_budget|probe_budget|probe_count|ProbeBudget|attempt_budget|max_probe" \
  --include="*.py" --include="*.json" --include="*.yaml" --include="*.toml" .
# 命中数：0
```

### 3.2 盘点表

| 能力项 | 现状 | 证据(文件:行号) | 状态标签 |
|---|---|---|---|
| `ConfirmationBudget` 概念/类/配置 | 全仓库零命中，概念完全不存在 | 上述 grep 命中数 0 | `NOT_FOUND` |
| 时间预算（待确认判断 TTL） | 无。待确认候选不落库、不计时、不失效 | 全仓库无 `pending_confirmation` / `ttl` 相关实现 | `NOT_FOUND` |
| 次数预算（试探建仓计数） | 无。不存在任何按次数递减的计数器 | grep `probe_count` / `max_probe` 命中数 0 | `NOT_FOUND` |
| 额度预算（试探仓位上限） | 无「试探仓位」这一类别；仓位不区分正常/试探 | `quant/exe/order_factory.py:35,59` 生成订单时无 probe 标记字段 | `NOT_FOUND` |
| 预算消耗留痕 | 无。run 快照中无任何预算余额字段 | `quant/fund_pipeline.py:413-439` payload 字段清单中无预算项 | `NOT_FOUND` |
| 冷却期机制 | 无。次数预算不存在，冷却自然也不存在 | 同上 | `NOT_FOUND` |
| **额度型风险预算（近似物）** | `RiskBudget` 只有权重/回撤/波动/簇暴露/相关性五个**额度**字段，**没有时间维度、没有次数维度** | `quant/port/risk_budget.py:10-13`（`budget_weight` / `max_drawdown` / `max_volatility` / `max_cluster_exposure` / `max_correlation`） | `PARTIAL` |
| `RiskBudget` 是否在生产链上 | 否。`quant/fund_pipeline.py:7-30` 的 import 清单中不含 `quant.port.*` | `quant/fund_pipeline.py:7-30` | `TESTED_BUT_NO_PROD_CALLER` |
| `RECOVERY_PROBE` 状态定义 | 枚举存在 | `quant/domain/enums.py:106` | `IMPLEMENTED_UNTESTED` |
| `RECOVERY_PROBE` 是否授权探仓 | **否**。被映射为 `NEUTRAL`，行为与 `RANGE_NEUTRAL` 完全相同，「探」字落空 | `quant/decision/conflict_resolver.py:166`（对比 `:165` RANGE_NEUTRAL 同为 NEUTRAL） | `PARTIAL` |
| `RECOVERY_PROBE` 产生逻辑 | 存在两处：得分区间 0.2–0.4，以及恢复确认期 | `quant/regimes/regime_ensemble.py:121`、`:229` | `TESTED_BUT_NO_PROD_CALLER` |
| 恢复确认天数计数（最接近「预算」的现存物） | `_recovery_streak` 逐日累加至 `RECOVERY_CONFIRM_DAYS=3`；但它是**实例内存属性**，进程重启即归零，**未持久化** | `quant/regimes/regime_ensemble.py:79`（常量 `RECOVERY_CONFIRM_DAYS = 3`）、`:85`（`self._recovery_streak: int = 0`）、`:224-227`（累加与判定） | `TESTED_BUT_NO_PROD_CALLER` |
| `RegimeEnsemble` 是否在生产链上 | 否。全仓库仅 `tests/test_features_regime.py:13,95,106,114` 引用 | grep `RegimeEnsemble` 结果：无非测试调用方 | `TESTED_BUT_NO_PROD_CALLER` |
| 信号过期判定函数 | `signal_expiration(age, max_valid_days=30)` 存在，是时间预算的可用原语 | `quant/feat/time_decay.py:19-20` | `TESTED_BUT_NO_PROD_CALLER` |
| `signal_expiration` 生产调用方 | **零**。仅 `tests/test_features_regime.py:9,50` 引用 | grep `signal_expiration` 结果 | `TESTED_BUT_NO_PROD_CALLER` |
| 两日稳定性确认（生产在跑） | `require_two_day_stability` 要求 Top-N 排序连续两次一致，否则降级信心 | `quant/guard.py:64-77`，经 `quant/fund_pipeline.py:202` 调用 | `TESTED_AND_WIRED` |
| 上述稳定性是否构成「预算」 | **不构成**。它是**每次运行独立重算的门禁**，不累计、不扣减、不会耗尽；同一判断可以被无限次重复检查 | `quant/guard.py:47-77` 无任何跨运行状态 | `PARTIAL` |
| 复核节奏控制（生产在跑） | 按 regime/信心/状态返回下次复核所需的新增 NAV 观测数（1/3/10/20） | `quant/fund_schedule.py:19-32`，经 `quant/fund_pipeline.py:237-239`、`:360-362` 调用 | `TESTED_AND_WIRED` |
| 上述节奏是否构成「冷却」 | **部分**。它限制了复核**频率**，但不限制**试探次数**；连续 20 次复核都判 REBALANCE 也不会被拦 | `quant/fund_pipeline.py:241` `scheduled_due` 仅是布尔门禁 | `PARTIAL` |
| 数据时效上限（生产在跑） | `max_staleness_calendar_days` 对 NAV 陈旧度设硬上限 | `quant/fund_pipeline.py:167-171` 传入 `fund_nav_quality_gate` | `TESTED_AND_WIRED` |
| 上述时效是否等于「判断 TTL」 | **否**。它约束的是**输入数据**的新鲜度，不是**已产生判断**的存活期。数据新鲜但判断挂了两个月，系统不会察觉 | 同上；全仓库无判断级 TTL | `PARTIAL` |
| 幂等指纹（生产在跑） | 相同决策指纹不重复建单 | `quant/fund_pipeline.py:266-285` | `TESTED_AND_WIRED` |
| 幂等是否等于次数预算 | **否**。它只拦「完全相同」的决策；权重差 0.1% 就是新指纹，可无限次重新试探 | `quant/fund_pipeline.py:270-272` 指纹含 `actionable_target` 全量权重 | `PARTIAL` |

### 3.3 结论

生产链上存在**四个看起来像预算的机制**（两日稳定性、复核节奏、数据时效、幂等指纹），但它们全部是**无状态门禁**：每次运行独立判断，不累计消耗、不会耗尽、不留余额。

**系统当前没有任何机制可以回答「我今年已经在这个方向上试探过几次了」这个问题。** 这是本文档标记的最严重缺陷。

---

## 4. 差距与落地计划

| 优先级 | 任务 | 依赖 | 交付物 |
|---|---|---|---|
| **P0** | 定义预算持久化载体：新增 `state/confirmation_budget.json`（或 SQLite 表），字段含 `dimension / remaining / consumed_at / consumed_by / reason`。**预算必须跨运行存活**，否则重启即作弊 | 无 | 数据结构 + schema 校验 |
| **P0** | 把预算余额写入 run 快照 payload（`quant/fund_pipeline.py:413` 的 `payload.update`）。先只读不拦，建立可观测性 | 上一条 | run 快照新增 `confirmation_budget` 段 |
| **P1** | 实现时间预算：把 `quant/feat/time_decay.py:19` 的 `signal_expiration` 接入生产，为「未达门槛的候选」打 TTL 并逐日扣减 | P0 | 待确认候选表 + TTL 扣减 |
| **P1** | 让 `RECOVERY_PROBE` 具备真实语义：在 `quant/decision/conflict_resolver.py:166` 之外增加 probe 授权分支，使其不再等同于 `RANGE_NEUTRAL` | P0 | 状态与授权解耦 |
| **P1** | 持久化 `RegimeEnsemble._recovery_streak`（当前 `quant/regimes/regime_ensemble.py:85` 为内存变量），并接入生产 regime 判定 | P0 | 恢复确认跨运行连续 |
| **P2** | 实现次数预算 + 冷却期：订单意图增加 `is_probe` 标记（`quant/domain/orders.py`），生成前查预算 | P1 | 试探订单与正常订单可区分 |
| **P2** | 实现额度预算：扩展 `quant/port/risk_budget.py:10-13` 的 `RiskBudget`，增加 `max_probe_exposure` / `probe_ttl_days` / `max_probe_count` 三字段 | P1 | RiskBudget 覆盖三维度 |
| **P3** | 预算消耗的回溯分析：统计历史上预算耗尽后的实际结果，检验参数是否合理 | P2 | 研究报告 |
| **P4** | 按 regime 自适应预算（压力期收紧、稳定期放宽） | P3 | 自适应策略 |

---

## 5. 验收方式

以下条件必须**可执行**（写成测试或验收脚本），不接受人工目测。

### 5.1 时间预算

- 给定一个 TTL = 3 个 NAV 观测日的待确认候选，当经过 4 个 NAV 观测日后仍未被确认，则该候选状态为 `EXPIRED`，且**不出现**在当次 `target_weights` 中，失效原因写入 run 快照。
- 给定进程在候选存活期间重启，当重启后再次运行，则剩余 TTL **保持连续**（不重置为初始值）。

### 5.2 次数预算

- 给定 `probe_count_per_direction = 2` 且某方向已消耗 2 次，当决策再次要求在该方向试探建仓，则订单生成被拒绝，拒绝原因含「试探次数预算已耗尽」，且 `orders` 长度为 0。
- 给定次数预算已耗尽并进入冷却，当冷却期内任意运行发起试探，则一律拒绝；当冷却期满，则预算重置为初始值并留痕。

### 5.3 额度预算

- 给定试探总额度上限 10% 且当前试探仓位已占 9%，当新试探请求需要 3%，则请求被拒绝（不允许部分成交到 10%，除非配置显式允许截断）。
- 给定试探仓位超限，当系统执行回落，则**不得**基于超限本身强制赎回已有试探仓位（避免用约束违反当作卖出信号）。

### 5.4 留痕

- 给定任意一次消耗了预算的运行，当读取该次 run 快照，则能查到 `dimension / consumed / remaining / reason` 四个字段，缺一即验收失败。
- 给定连续 N 次运行，当汇总所有 run 快照的预算消耗，则总消耗量与 `state/confirmation_budget.json` 中的累计值**完全一致**（对账闭合）。

### 5.5 RECOVERY_PROBE 语义

- 给定 regime 为 `RECOVERY_PROBE`，当查询该状态对应的策略方向，则结果**不得**与 `RANGE_NEUTRAL` 完全相同（否则说明探仓语义仍未落地）。
- 给定 regime 为 `RECOVERY_PROBE` 以外的任意状态，当尝试生成试探性订单，则被拒绝——试探次数预算**只能**在 `RECOVERY_PROBE` 下消耗。

---

## 6. 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-08-01 | v1.0 | 初版。定义时间/次数/额度三类确认预算与留痕要求；核实 `ConfirmationBudget` 系列标识符全仓库零命中（`NOT_FOUND`）；核实 `RiskBudget`（`quant/port/risk_budget.py:10-13`）仅覆盖额度维度；核实 `RECOVERY_PROBE` 被映射为 `NEUTRAL`（`quant/decision/conflict_resolver.py:166`），探仓语义落空 |
