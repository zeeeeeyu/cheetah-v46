# V4.6 生产接线审计（Production Wiring Audit）

> **日期**：2026-08-01
> **审计问题**：新增模块是否存在「有测试无落地」——即通过验收门、有单元测试，但**从未出现在真实运行链路上**。
> **判定标准**：从 `app/*.py` 真实入口（`run_daily` / `run_core_fund` / `run_tactical_etf` / `reconcile_account` / `menu`）出发，
> 沿 import 与调用关系可达者记为 **WIRED**；仅被 `tests/` 与 `app/v46_acceptance.py` 引用者记为 **ORPHAN（孤岛）**。
> **重要**：`app/v46_acceptance.py` 是验收门，**不是生产路径**。验收门调用某模块，只证明该模块可被调用，不证明它参与真实决策。

---

## 1. 一句话结论

> **三条生产链路真实存在且健康，「订单生成 → 执行」整条链现经 W2-4 接入生产（仅生成 DRAFT 意图、不提交真实单）。**
> `permission_gate` 已正确接入 `order_factory`，`order_factory` 经 `run_daily._record_decision_plans` → `record_daily_proposals(generate_orders=True)` 接入生产——闸门现在守着一扇真实有人走的门。
>
> **2026-08-01 修订**：本次审计发现的问题已修复四项——W2-1（PROVISIONAL 闸门）、W2-2（第一本账通电）、W2-3（第二本账通电）、W2-4（订单链生产可达）。
> 截至本次修订，三本账中的①模型目标账、②决策计划账均已通电；③真实持仓账始终有真实写入。剩余边界：`execution_service` 提交环节在 PAPER 零真实单原则下保持不接入（预期边界，非缺陷）。

这不是缺陷，是**当前范围的如实状态**：`app/run_daily.py:3` 明写 "First-run scope: PAPER analysis only"，`:61` 输出「本次未生成、未批准、未提交任何真实订单」。
但它直接决定了对外口径——**不能把「模块存在且测试通过」表述为「系统已具备该能力」**。

---

## 2. 生产链路实测图

### 链路 A：核心基金（`app/run_daily.py:48`、`app/run_core_fund.py:11`）

```
app/run_daily.py
  └─ quant.fund_pipeline.run_fund_daily(as_of)          ← 真实入口
       ├─ quant.calendar_groups                          ✅ WIRED
       │    (CalendarGroupError, normalize_calendar_group)
       ├─ quant.fund_universe.resolve_calendar_group      ✅ WIRED  (fund_universe.py:62)
       ├─ align_fund_frames_by_calendar_group             ✅ WIRED  (fund_pipeline.py:130 → 调用于 :297-301)
       └─ quant.fund_execution
            └─ quant.ledgers.guards.assert_write_allowed  ✅ WIRED  (fund_execution.py:12)
```

### 链路 B：战术 ETF（`app/run_daily.py:53`）

```
app/run_daily.py
  └─ quant.strategies.tactical_etf.engine.run_tactical_etf(as_of)
       ├─ .ranking.RankedETF / score_trend / rank_universe  ✅ WIRED  ← c32 验收锁定的正是这条
       ├─ quant.domain.signals.StrategyDecision             ✅ WIRED（旧对象）
       └─ ✗ 不涉及 TargetPortfolio / DecisionProposal / OrderIntent / order_factory / permission_gate
```

### 链路 C：对账（`app/menu.py:13,14`）

```
app/menu.py "3"/"4"
  └─ app.reconcile_account
       └─ quant.ledgers.guards.assert_write_allowed        ✅ WIRED  (reconcile_account.py:23)
```

---

## 3. 逐模块判定表

| 模块 / 符号 | 生产调用方（证据行） | 判定 |
|---|---|---|
| `quant/calendar_groups.py` | `fund_universe.py:8,62`、`fund_pipeline.py:8,133,160,297-301` → `run_fund_daily` → `run_daily.py:48` | ✅ **WIRED** |
| `align_fund_frames_by_calendar_group` | `fund_pipeline.py:130` 定义，`:297-301` 在 `run_fund_daily` 内调用 | ✅ **WIRED** |
| `quant/ledgers/guards.py` `assert_write_allowed` | `fund_execution.py:12`、`execution_service.py:6`、`reconcile_account.py:23` | ✅ **WIRED**（经链路 A、C 可达） |
| `ranking.score_trend` / `rank_universe` | `strategies/tactical_etf/engine.py` → `run_tactical_etf` → `run_daily.py:53` | ✅ **WIRED** |
| `quant/exe/permission_gate.py` | `order_factory.py:38,66`（2026-08-01 W2-4 经 `run_daily._record_decision_plans` → `record_daily_proposals(generate_orders=True)` 接入生产）；调用方含 `tests/`、`app/v46_acceptance.py:328` | ✅ **WIRED**（W2-4 已修复；原 ORPHAN 间接） |
| `quant/exe/order_factory.py` | `app/run_daily.py:_record_decision_plans` → `ledgers/recorder.py:record_daily_proposals(generate_orders=True)` → `order_factory.generate_*_orders` | ✅ **WIRED**（W2-4 已修复；原 ORPHAN） |
| `quant/exe/execution_service.py` | **零生产调用方**（`v46_acceptance.py:452` 仅做源码 inspect）；提交环节在 PAPER 零真实单原则下保持不接入 | ❌ **ORPHAN（预期边界）** —— 见 §4 注 |
| `quant/domain/portfolios.TargetPortfolio` | `ledgers/recorder.py` → `app/run_daily.py`（2026-08-01 W2-2 接入） | ✅ **WIRED**（原为 ORPHAN） |
| `quant/domain/proposals.DecisionProposal` | `ledgers/decision_plans.py:write_decision_proposal` ← `ledgers/recorder.py:record_daily_proposals` ← `app/run_daily.py:_record_decision_plans`（W2-3 接入） | ✅ **WIRED**（W2-3 已修复；原 ORPHAN） |
| `quant/domain/orders.OrderIntent` | `order_factory`（W2-4 接入生产）、`execution_service`（自身孤岛）、`ledgers/decision_plans.py` + `tests/` | ✅ **WIRED**（W2-4 已修复；原 ORPHAN） |
| `model_targets.write_target_portfolio` | `ledgers/recorder.py:record_target_portfolio` → `run_daily.py` | ✅ **WIRED**（W2-2 已修复） |
| `decision_plans.write_decision_proposal` | `ledgers/recorder.py:record_daily_proposals` ← `app/run_daily.py:_record_decision_plans`（W2-3 接入） | ✅ **WIRED**（W2-3 已修复；原 ORPHAN） |
| `quant/riskfolio_adapters.py` | 仅 `tests/test_riskfolio_adapters.py:12` | ❌ **ORPHAN**（Wave 1 骨架，已知且预期） |
| `quant/provisional.py` `verify_provisional_values` | `app/inspect_system.py`（2026-08-01 接入）→ `run_daily.py:32-35` | ✅ **WIRED**（W2-1 已修复；原为 ORPHAN） |

---

## 4. 三本账的真实状态

| 账本 | 生产写入方 | 实际状态 |
|---|---|---|
| ① 模型目标账 `model_targets` | `app/run_daily.py:_record_model_targets` → `ledgers/recorder.py`（2026-08-01 接入） | ✅ **已通电**：实跑后 `storage/ledgers/model_targets.db` 有真实记录 |
| ② 决策计划账 `decision_plans` | `app/run_daily.py:_record_decision_plans` → `ledgers/recorder.py`（2026-08-01 W2-3 接入） | ✅ **已通电**：实跑 `run_daily --as-of 2026-07-31` 后 `decision_plans.db` 有 2 条真实记录，血缘 `source_portfolio_id` 可回溯 W2-2 |
| ③ 真实持仓账 `actual_positions` / `position_snapshots` | `fund_execution.py`、`reconcile_account.py`（经守卫白名单） | ✅ 有真实写入 |

**含义（2026-08-01 更新）**：三本账物理隔离的**守卫机制**（`guards.py` 白名单、负向测试）一直是真实且生效的。
「三本账并行记账」这一**能力**：W2-2 后第一本通电、W2-3 后第二本通电、第三本（真实持仓）始终有真实写入——**三本账现已全部在生产中记账**。
可对外如实宣称「三本账均在生产中记账」；但 `execution_service` 提交环节仍不接入（PAPER 零真实单），故「记账」≠「下单」。

---

## 5. 对完成度口径的直接影响

这次审计为「86% vs 19% 之争」提供了决定性证据（数字随 W2-1~W2-4 落地而演进：验收门 81%→86%、追溯矩阵 16%→19%）：

- 验收门机械覆盖率 **36/42 = 86%** 统计的是「条件可被验证通过」；
  其中 c35/c36（第二本账通电、订单链生产可达）验证的已是从 `run_daily` 真实走到的模块——但 c37–c42 仍有 6 条已知缺口未实现。
- 追溯矩阵 **12/63 ≈ 19%** 统计的是「生产调用链真实执行」；
  本次逐模块 grep 独立复核后，**这一量级是可信的**。

→ 结论：**验收门比率仍不能作为对外完成度**（分母随实现移动、且衡量的是「自测完整性」而非「需求完成度」）。对外口径一律用追溯矩阵 19%。详见 `docs/audit/V46_COMPLETION_METRIC_RULING.md`。

---

## 6. 由此确定的 Wave 2 真实任务（按性价比排序）

| 优先级 | 任务 | 为什么 | 落地判据 |
|---|---|---|---|
| ~~**W2-1**~~ ✅ | ~~把 `verify_provisional_values` 挂成启动/运行前闸门~~ | **2026-08-01 已完成**：接入 `app/inspect_system.py`，`run_daily.py:32-35` Fail-Closed；3 条负向测试 + 验收 c33 锁定 | 已达成 |
| ~~**W2-2**~~ ✅ | ~~产出 `TargetPortfolio` 并写入模型目标账~~ | **2026-08-01 已完成**：`quant/ledgers/recorder.py` + `app/run_daily.py:_record_model_targets`；实跑 `--as-of 2026-07-31` 后 `model_targets.db` 有 2 条真实记录；11 条测试 + 验收 c34 锁定 | 已达成 |
| ~~**W2-3**~~ ✅ | ~~治理管线产出 `DecisionProposal` 并写入决策计划账~~ | **2026-08-01 已完成**：`quant/ledgers/recorder.py` + `app/run_daily.py:_record_decision_plans`；实跑 `--as-of 2026-07-31` 后 `decision_plans.db` 有 2 条真实记录，血缘可回溯 W2-2；11 条测试 + 验收 c35 锁定 | 已达成 |
| ~~**W2-4**~~ ✅ | ~~生产链路接入 `order_factory`（PAPER 模式下只生成不提交）~~ | **2026-08-01 已完成**：`run_daily._record_decision_plans` → `record_daily_proposals(generate_orders=True)` → `order_factory`；实跑产生 `OrderIntent(DRAFT)`，未授权标的被 `permission_gate` 拦下；验收 c36 锁定 | 已达成 |
| W2-5 | `order_factory:25-30` / `datahub.py:80-84` 两处 weekday 近似改查组日历 | `CALENDAR_GROUPS.md` CG-P1-2 已定位 | 节假日不再被当交易日 |
| W3-1 | Riskfolio 适配器实装 | 依赖第三方库 + 回测数据，Wave 3 前不具备条件 | —（明确延后，不做假实现） |

**W2-1 至 W2-4 已全部完成**（严格按依赖顺序落地）。剩余 W2-5（组日历替代 weekday）、W3-1（Riskfolio 实装）按原优先级推进；`execution_service` 提交环节在 PAPER 零真实单原则下保持不接入，属预期边界。

---

## 7. 审计方法（可复现）

```bash
# 1. 找符号的全部引用
grep -rn "check_execution_permission\|permission_gate" .
# 2. 剔除 tests/ 与 app/v46_acceptance.py，剩下的才是候选生产调用方
# 3. 对每个候选调用方，递归向上追问「谁调用它」，直到抵达 app/*.py 入口或确认为零
# 4. 抵达入口 = WIRED；追到零 = ORPHAN
```

**关键教训**：只查一层引用会得出错误结论。
`permission_gate` 第一层查出 `order_factory.py:38,66`，看起来「已接入生产」；
追第二层才发现 `order_factory` 自己就是孤岛。**接线审计必须追到入口为止。**
