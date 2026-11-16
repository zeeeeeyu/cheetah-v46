# 猎豹 V4.6 — 账户 Ledger 设计

## 设计原则

1. **账户不可被策略直接覆盖** — 策略只能生成 OrderIntent，不能写入持仓
2. **Transaction 不可变** — 一旦写入禁止修改，错误通过冲正记录修正
3. **写入必须使用事务** — 确保数据一致性
4. **重复导入幂等** — 通过 transaction_id 去重
5. **每条记录关联来源 + 审计时间戳**
6. **资产代码使用 TEXT** — 保留基金前导零
7. **明确区分六种状态**：计划目标、待确认订单、已提交订单、已成交订单、已对账交易、真实持仓

## 数据库 Schema

数据库位于 `storage/ledgers/account.db`（SQLite WAL 模式）。

### 表结构

| 表名 | 行数含义 | 主键 |
|------|---------|------|
| `accounts` | 每个账户一行 | `account_id` |
| `assets` | 资产主数据本地副本 | `symbol` |
| `transactions` | 不可变交易事件 | `(transaction_id, account_id)` |
| `orders` | 订单记录 | `(order_id, account_id)` |
| `executions` | 执行记录 | `(execution_id, account_id)` |
| `position_snapshots` | 持仓快照（对账生成） | `snapshot_id` (自增) |
| `reconciliation_runs` | 对账运行记录 | `run_id` (自增) |
| `audit_log` | 审计日志 | `log_id` (自增) |
| `schema_version` | Schema 版本追踪 | 单行 |

### 精度定义

| 字段类型 | 精度 | 用途 |
|----------|------|------|
| `DECIMAL(18,4)` | 18位整数, 4位小数 | 金额、份额 |
| `DECIMAL(12,6)` | 12位整数, 6位小数 | 价格、净值 |

### accounts 表

| 字段 | 类型 | 说明 |
|------|------|------|
| `account_id` | TEXT PK | 账户 ID |
| `account_mode` | TEXT | MICRO / RETAIL / ADVANCED_RETAIL |
| `total_capital_in` | DECIMAL(18,4) | 累计投入本金 |
| `additional_capital` | DECIMAL(18,4) | 追加本金 |
| `withdrawn_capital` | DECIMAL(18,4) | 取出本金 |
| `total_assets` | DECIMAL(18,4) | 总资产（现金+市值） |
| `cash_balance` | DECIMAL(18,4) | 现金余额 |
| `realized_pnl` | DECIMAL(18,4) | 已实现盈亏 |
| `unrealized_pnl` | DECIMAL(18,4) | 未实现盈亏 |
| `total_fees` | DECIMAL(18,4) | 累计费用 |
| `live_order_permission` | INTEGER | 0=禁用, 1=启用 |
| `currency` | TEXT | 货币（默认 CNY） |

### transactions 表（不可变事件）

| 字段 | 类型 | 说明 |
|------|------|------|
| `transaction_id` | TEXT | 交易 ID |
| `account_id` | TEXT FK | 账户 ID |
| `symbol` | TEXT | 资产代码 |
| `strategy_id` | TEXT | 策略来源 |
| `side` | TEXT | BUY/SELL/DIVIDEND/FEE/TRANSFER_IN/TRANSFER_OUT/REVERSAL |
| `quantity` | DECIMAL(18,4) | 份额 |
| `price` | DECIMAL(12,6) | 价格/净值 |
| `gross_amount` | DECIMAL(18,4) | 总金额 |
| `fee` | DECIMAL(18,4) | 费用 |
| `execution_date` | TEXT | 执行日期 |
| `confirmation_date` | TEXT | 确认日期 |
| `source` | TEXT | 来源（MANUAL/BROKER/SYSTEM/LEGACY_IMPORT） |
| `reversal_for` | TEXT | 冲正的目标 transaction_id |
| `note` | TEXT | 备注 |

**side 约束**：`CHECK (side IN ('BUY','SELL','DIVIDEND','FEE','TRANSFER_IN','TRANSFER_OUT','REVERSAL'))`

**不可变契约**：应用层代码不对 transactions 表发出 UPDATE。错误通过插入 `side='REVERSAL'` + `reversal_for=原transaction_id` 的记录修正。

### 订单状态

```
DRAFT → RISK_APPROVED → USER_APPROVED → SUBMITTED → PARTIALLY_FILLED → FILLED → RECONCILED
        → REJECTED / CANCELLED / EXPIRED / FAILED （任意状态可异常终止）
```

## 六种状态区分

| 状态 | 存储位置 | 写入者 |
|------|---------|--------|
| 计划目标 (TARGET) | StrategyDecision.target_weights | 策略引擎 |
| 待确认订单 (PLANNED) | orders (status=DRAFT) | 执行层 |
| 已提交订单 (SUBMITTED) | orders (status=SUBMITTED) | 执行层/用户 |
| 已成交订单 (FILLED) | executions | 执行层/对账 |
| 已对账交易 (RECONCILED) | position_snapshots (source=RECONCILIATION) | 对账模块 |
| 真实持仓 (REAL) | position_snapshots (最新对账结果) | 仅对账后 |

**核心规则**：策略生成的目标权重永远不能直接写入 position_snapshots。

## 精度控制

- 金额：DECIMAL(18,4) — 最大 99999999999999.9999 元
- 价格/净值：DECIMAL(12,6) — 最大 999999999999.999999 元/份
- 用于基金净值计算和毫厘级精度要求
