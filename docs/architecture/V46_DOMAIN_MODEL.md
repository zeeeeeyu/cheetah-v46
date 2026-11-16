# 猎豹 V4.6 — 统一领域模型设计

## 概述

V4.6 领域模型位于 `quant/domain/` 包下，为策略、执行和风控提供统一数据契约。

### 设计原则

1. **不变性** — 所有 DataClass 使用 `frozen=True`，状态转换返回新实例
2. **无基础设施依赖** — 不导入 AkShare、pandas、CSV/JSON/SQLite
3. **显式表达** — signal_symbol、trade_symbol、environment 不允许隐式推断
4. **分层通信** — 模块间通过结构化对象通信，不传递原始 Prompt

### 包结构

```
quant/domain/
├── __init__.py    # 包入口
├── enums.py       # 统一枚举（Environment / AssetType / DataStatus / OrderStatus 等）
├── assets.py      # 资产模型（Asset / Position / Transaction / FundETFRelation）
├── signals.py     # 信号与决策模型（StrategySignal / StrategyDecision / PortfolioDecision / RejectedSignal）
├── orders.py      # 订单模型（OrderIntent + 状态机验证函数）
└── risks.py       # 风险模型（RiskClusterExposure / RiskBudget / PortfolioRiskSnapshot / RiskVetoResult）
```

---

## 枚举（enums.py）

### Environment
| 值 | 含义 |
|----|------|
| `BACKTEST` | 回测环境 |
| `PAPER` | 模拟盘环境 |
| `LIVE` | 实盘环境 |

### AssetType
| 值 | 含义 |
|----|------|
| `CASH` | 现金/货币基金 |
| `MONEY_MARKET` | 货币基金 |
| `BOND` | 债券基金 |
| `BROAD_EQUITY` | 宽基权益 |
| `STYLE_EQUITY` | 风格权益 |
| `INDUSTRY_EQUITY` | 行业权益 |
| `THEME_EQUITY` | 主题权益 |
| `GOLD` | 黄金 |
| `COMMODITY` | 商品 |
| `CROSS_BORDER` | 跨境 |
| `ACTIVE_FUND` | 主动型场外基金 |
| `ETF_LINKED_FUND` | ETF联接基金 |
| `ETF` | 场内ETF |

### DataStatus
| 值 | 含义 |
|----|------|
| `PASS` | 数据正常 |
| `DEGRADED` | 降级运行 |
| `QUARANTINED` | 单资产隔离 |
| `BLOCKED` | 阻断全局 |

### DecisionStatus
| 值 | 含义 |
|----|------|
| `DATA_BLOCKED` | 数据阻断 |
| `MIGRATION_REQUIRED` | 需要迁移 |
| `HOLD` | 持有/不交易 |
| `HOLD_RECHECK` | 持有但需复核 |
| `REBALANCE_CANDIDATE` | 可再平衡 |
| `RISK_OVERRIDE` | 风险覆盖 |
| `APPROVED` | 已审批通过 |
| `REJECTED` | 已否决 |

### OrderStatus
合法状态转换见下方状态机。

| 值 | 含义 |
|----|------|
| `DRAFT` | 草稿 |
| `RISK_APPROVED` | 风控通过 |
| `USER_APPROVED` | 用户审批通过 |
| `SUBMITTED` | 已提交 |
| `PARTIALLY_FILLED` | 部分成交 |
| `FILLED` | 完全成交 |
| `RECONCILED` | 已对账 |
| `REJECTED` | 已拒绝（终止） |
| `CANCELLED` | 已取消（终止） |
| `EXPIRED` | 已过期（终止） |
| `FAILED` | 已失败（终止） |

### OrderSide
| 值 | 含义 |
|----|------|
| `BUY` | 买入 |
| `SELL` | 卖出 |
| `HOLD` | 持有 |

### StrategyLifecycle
| 值 | 含义 |
|----|------|
| `RESEARCH` | 研究阶段 |
| `PAPER` | 模拟盘 |
| `LIVE_LIMITED` | 有限实盘 |
| `LIVE_FULL` | 完整实盘 |
| `PAUSED` | 暂停 |
| `RETIRED` | 退役 |

### MarketRegime
| 值 | 旧 Regime 映射 |
|----|----------------|
| `TREND_RISK_ON` | `RISK_ON` |
| `RANGE_NEUTRAL` | `NEUTRAL` |
| `STRESS_RISK_OFF` | `RISK_OFF` |
| `RECOVERY_PROBE` | —（新增） |
| `UNKNOWN` | —（默认） |

### CapitalPermission
| 值 | 含义 |
|----|------|
| `REAL` | 可真实交易 |
| `REAL_LIMITED` | 有限真实交易 |
| `PAPER` | 仅模拟 |
| `DISABLED` | 禁用 |
| `OBSERVATION_ONLY` | 仅观察，不交易 |

---

## 资产模型（assets.py）

### Asset（不可变）
```python
@dataclass(frozen=True)
class Asset:
    symbol: str              # 交易代码，基金保留前导零
    name: str                # 资产名称
    asset_type: AssetType    # 资产类型
    exchange: str | None     # 交易所（ETF必填）
    currency: str            # 货币（默认 CNY）
    lot_size: int            # 最小交易单位（ETF=100，基金=1）
    signal_symbol: str | None  # 信号来源代码
    trade_symbol: str | None   # 实际交易代码
    risk_clusters: tuple[str, ...]  # 风险簇
    strategy_eligible: bool       # 策略候选资格
    execution_eligible: bool      # 执行资格
    permission: CapitalPermission  # 资金权限
```

**关键约束**：
- `symbol` 必须是字符串，基金代码必须有前导零
- `signal_symbol` 和 `trade_symbol` 默认等于 `symbol`，但必须显式表达
- `asset_type` 决定身份（ETF vs ACTIVE_FUND），不能通过代码格式推断

### Position（不可变）
```python
@dataclass(frozen=True)
class Position:
    account_id: str
    symbol: str
    quantity: float
    available_quantity: float
    average_cost: float
    market_value: float
    strategy_allocations: dict[str, float]  # 各策略占用比例
    updated_at: datetime | None
```

**关键约束**：Position 只能由 Ledger 聚合生成，不能从目标权重直接创建。

### Transaction（不可变事件）
```python
@dataclass(frozen=True)
class Transaction:
    transaction_id: str
    account_id: str
    symbol: str
    strategy_id: str
    side: str           # BUY / SELL
    quantity: float
    gross_amount: float
    fee: float
    execution_date: str
    confirmation_date: str | None
    source: str         # MANUAL / BROKER_RECONCILIATION
```

**关键约束**：Transaction 不可覆盖，错误通过新增冲正记录修正。

---

## 信号与决策模型（signals.py）

### StrategySignal（不可变）
```python
@dataclass(frozen=True)
class StrategySignal:
    strategy_id: str
    strategy_version: str
    symbol: str
    signal_date: str
    signal_type: str       # TREND / BREAKOUT / MEAN_REVERSION / RISK_WARNING / HOLD
    score: float
    confidence: float
    expected_horizon: str  # SHORT / MEDIUM / LONG
    expires_at: datetime | None   # 过期时间
    risk_direction: str    # BULLISH / BEARISH / NEUTRAL
    reasons: tuple[str, ...]
    feature_snapshot_id: str | None  # 关联特征快照
```

### StrategyDecision（不可变）
```python
@dataclass(frozen=True)
class StrategyDecision:
    strategy_id: str
    as_of_date: str
    environment: Environment       # 必须关联
    target_weights: dict[str, float]
    signals: tuple[StrategySignal, ...]
    cash_weight: float
    expected_turnover: float
    decision_status: DecisionStatus
    reasons: tuple[str, ...]
```

**关键约束**：StrategyDecision 不包含成交信息（无 order_id / execution_price）。

### PortfolioDecision（不可变）
```python
@dataclass(frozen=True)
class PortfolioDecision:
    account_id: str
    as_of_date: str
    strategy_decisions: tuple[StrategyDecision, ...]
    approved_strategy_budgets: dict[str, float]
    aggregate_target_weights: dict[str, float]
    rejected_signals: tuple[RejectedSignal, ...]
    approval_required: bool  # LIVE 模式默认 True
```

---

## 订单模型（orders.py）

### OrderIntent（不可变）

订单状态机：

```
                    ┌─────────┐
                    │  DRAFT  │
                    └────┬────┘
                    ┌────▼────────┐
              ┌─────│ RISK_APPROVED│──────┐
              │     └──────┬───────┘      │
              │     ┌──────▼────────┐     │
              ├─────│ USER_APPROVED │     │
              │     └──────┬────────┘     │
              │     ┌──────▼──────┐       │
              ├─────│  SUBMITTED  │       │
              │     └──────┬──────┘       │
              │     ┌──────▼───────────┐  │
              ├─────│ PARTIALLY_FILLED │  │
              │     └──────┬───────────┘  │
              │     ┌──────▼──────┐       │
              ├─────│   FILLED    │       │
              │     └──────┬──────┘       │
              │     ┌──────▼─────────┐    │
              └─────│   RECONCILED   │    │
                    └────────────────┘    │
                    ┌──────────────┐      │
                    │  REJECTED ◄──┘      │
                    │  CANCELLED ◄──┘     │
                    │  EXPIRED ◄──────────┘
                    │  FAILED ◄───────────┘
                    └──────────────┘
```

**状态转换规则**：
- DRAFT → RISK_APPROVED → USER_APPROVED → SUBMITTED → FILLED → RECONCILED
- 任意状态可异常终止：→ REJECTED / CANCELLED / EXPIRED / FAILED
- 终止状态不可再转换
- FILLED 只能到 RECONCILED
- RECONCILED 不可再转换

**LIVE 环境约束**：OrderIntent 必须经过 USER_APPROVED 才能进入 SUBMITTED。

### 状态机验证函数
```python
def is_valid_transition(from_status: OrderStatus, to_status: OrderStatus) -> bool
def validate_transition(from_status: OrderStatus, to_status: OrderStatus) -> None
```

---

## 风险模型（risks.py）

### RiskClusterExposure
| 字段 | 类型 | 含义 |
|------|------|------|
| `cluster_name` | str | 风险簇名称 |
| `total_weight` | float | 总暴露权重 |
| `limit` | float | 上限 |
| `asset_contributions` | dict | 各资产贡献 |
| `breached` | bool | 是否超限 |

### RiskBudget
| 字段 | 类型 | 含义 |
|------|------|------|
| `strategy_id` | str | 策略 ID |
| `budget_weight` | float | 风险预算分配权重 |
| `max_drawdown` | float | 最大回撤上限 |
| `max_volatility` | float | 最大波动率上限 |
| `max_cluster_exposure` | dict | 风险簇上限 |
| `max_correlation` | float | 最大相关上限 |

### PortfolioRiskSnapshot
包含账户组合的风险整体快照：波动率、回撤、风险簇暴露、各策略贡献、现金比例等。

### RiskVetoResult
风险否决结果：是否否决、原因、触发者、影响策略、建议动作。

---

## 序列化

所有领域对象提供 `to_dict()` 方法输出 JSON 兼容字典。
部分对象提供 `from_dict()` 类方法反向构建。

```python
asset = Asset(symbol="510300", ..., asset_type=AssetType.ETF)
d = asset.to_dict()           # → JSON 兼容 dict
restored = Asset.from_dict(d) # → 新 Asset 实例
```

---

## 兼容性映射

见 `V451_MODEL_COMPATIBILITY.md`。
