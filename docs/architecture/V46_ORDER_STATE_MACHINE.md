# 场外基金订单状态机（V46_ORDER_STATE_MACHINE）

> 模块：`quant/fund_orders/`　测试：`tests/test_fund_order_state_machine.py`（30）

场外基金与 ETF 的本质区别在于**未知价成交**：T 日提交申购/赎回申请时，
成交净值（NAV）尚未公布（T+1 才有），份额确认要到 T+2。因此不能用 ETF 的
"下单即知价"状态机，必须为基金单独建模。

## 1. 十一态

| 状态 | 含义 | 是否终态 | confirmed_* |
|---|---|---|---|
| `DRAFT` | 策略草拟 | 否 | 必须为空 |
| `PENDING_REVIEW` | 待人工复核 | 否 | 必须为空 |
| `APPROVED` | 计划已批（第一阶段令牌） | 否 | 必须为空 |
| `SUBMITTED_MANUALLY` | 已在券商/基金端人工提交 | 否 | 必须为空 |
| `WAITING_NAV` | 等待 T+1 净值 | 否 | 必须为空 |
| `WAITING_SHARE_CONFIRMATION` | 已知净值，等待 T+2 份额确认 | 否 | 可写 nav |
| `CONFIRMED` | 成交确认（第二阶段令牌） | 否* | 必须齐备 |
| `REJECTED` | 被拒 | 是 | — |
| `CANCELLED` | 已撤单 | 是 | — |
| `EXPIRED` | 过期未提交 | 是 | — |
| `DATA_MISMATCH` | 数据不一致待修复 | 否 | — |

\* `CONFIRMED` 是"持仓生效"状态：`produces_position_effect` 仅在此为 True。

## 2. 三条硬约束

1. **未知价原则**：`WAITING_SHARE_CONFIRMATION` 之前的任何状态，
   `confirmed_nav / confirmed_units / confirmed_amount` 必须为 `None`。
   由 `FundOrder.__post_init__` 强制，杜绝"下单即假设成交价"。
2. **不可跳态**：所有转换必须走 `validate_transition`，非法转换抛
   `FundOrderTransitionError`。撤单仅允许在净值截止前（`SUBMITTED_MANUALLY`）。
3. **赎回上限**：`REDEEM` 的 `target_units` 不得超过 `available_units`。

## 3. 两阶段人工审批（令牌严格互斥）

- **第一阶段**（`apply_plan_approval`）：仅接受 `MANUAL_LIVE_PLAN_APPROVED`，
  只批准"计划"（`PENDING_REVIEW → APPROVED`），**不代表**已成交。
- **第二阶段**（`apply_execution_confirmation`）：仅接受
  `MANUAL_EXECUTION_CONFIRMED`，且必须同时提供确认净值/份额/金额，
  才能 `WAITING_SHARE_CONFIRMATION → CONFIRMED`。
- 用计划令牌去确认成交（或反之）一律拒绝——从令牌层面杜绝"批了计划就当成交"。

## 4. 幂等对账

`FundReconciler.reconcile(order, *, reconciliation_run_id, as_of)`：
- 仅 `CONFIRMED` 订单可产生 `FundPositionDelta`（申购 +份额、赎回 −份额）；
- 非 `CONFIRMED` 抛 `FundReconciliationError`；
- 重复 `order_id` 返回 `None`（幂等去重），防止重复导入放大持仓。

`build_execution_difference` 输出"计划 vs 实际"差异报告，供人工复盘。

## 5. 已知隐患（后续迁移）

`quant/exe/order_factory.generate_fund_orders` 旧路径用
`estimated_quantity = target_amount / nav` 违反未知价原则。新状态机**不走**
该路径；后续应把基金决策迁移到 `fund_orders` 包统一治理。
