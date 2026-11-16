# 猎豹 V4.6 — 执行与对账设计

## 全流程

```
PortfolioDecision
    ↓
OrderFactory (generate_etf_orders / generate_fund_orders)
    ↓
OrderIntent (DRAFT)
    ↓
RISK_APPROVED (自动风控)
    ↓
MANUAL_LIVE_PLAN_APPROVED (用户审批)
    ↓
USER_APPROVED
    ↓
SUBMITTED (用户在销售平台操作)
    ↓
PARTIALLY_FILLED / FILLED (成交反馈)
    ↓
MANUAL_EXECUTION_CONFIRMED (用户确认)
    ↓
RECONCILED
    ↓
ReconciliationService.write_to_ledger()
```

## 两阶段审批

| 阶段 | 令牌 | 作用 |
|------|------|------|
| 交易计划批准 | `MANUAL_LIVE_PLAN_APPROVED` | 批准交易的意向和金额 |
| 成交确认 | `MANUAL_EXECUTION_CONFIRMED` | 确认实际已经成交 |
| 拒绝 | `REJECTED` | 拒绝交易 |

两个令牌独立使用，**不得用一个令牌同时表示计划批准和成交完成**。

## 订单状态机

```
DRAFT → RISK_APPROVED → USER_APPROVED → SUBMITTED → PARTIALLY_FILLED → FILLED → RECONCILED
  ↓        ↓              ↓              ↓           ↓                  ↓
REJECTED  REJECTED      CANCELLED      FAILED      CANCELLED/FAILED   (终止)
                    EXPIRED
```

## 对账流程

1. 用户输入实际成交数据（手动或 CSV 导入）
2. ReconciliationService 比较预期与实际的数量和金额
3. 差异报告输出
4. 只有 RECONCILED 状态的订单写入 Ledger
5. 重复导入幂等

## Ledger 写入规则

- 只有 `status=RECONCILED` 的订单可以写入 Ledger
- 每条 execution_id 唯一，重复写入自动跳过
- 写入内容：executions + transactions + position_snapshots
