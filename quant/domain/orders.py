"""V4.6 订单与执行领域模型。

OrderIntent 是交易执行的核心对象，包含完整的订单状态机。
订单状态转换必须遵循合法路径：
  DRAFT → RISK_APPROVED → USER_APPROVED → SUBMITTED → FILLED → RECONCILED
异常路径：→ REJECTED / CANCELLED / EXPIRED / FAILED
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .enums import Environment, OrderStatus, OrderSide

# ── 订单状态机合法转换表 ──────────────────────────────────────────

_ORDER_STATE_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.DRAFT: {OrderStatus.RISK_APPROVED, OrderStatus.REJECTED, OrderStatus.CANCELLED},
    OrderStatus.RISK_APPROVED: {OrderStatus.USER_APPROVED, OrderStatus.REJECTED, OrderStatus.CANCELLED},
    OrderStatus.USER_APPROVED: {OrderStatus.SUBMITTED, OrderStatus.CANCELLED, OrderStatus.EXPIRED},
    OrderStatus.SUBMITTED: {OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED, OrderStatus.FAILED, OrderStatus.CANCELLED},
    OrderStatus.PARTIALLY_FILLED: {OrderStatus.FILLED, OrderStatus.FAILED, OrderStatus.CANCELLED},
    OrderStatus.FILLED: {OrderStatus.RECONCILED},
    # 异常终止状态不可转换
    OrderStatus.REJECTED: set(),
    OrderStatus.CANCELLED: set(),
    OrderStatus.EXPIRED: set(),
    OrderStatus.FAILED: set(),
    OrderStatus.RECONCILED: set(),
}


def is_valid_transition(from_status: OrderStatus, to_status: OrderStatus) -> bool:
    """检查订单状态转换是否合法。"""
    allowed = _ORDER_STATE_TRANSITIONS.get(from_status, set())
    return to_status in allowed


def validate_transition(from_status: OrderStatus, to_status: OrderStatus) -> None:
    """验证订单状态转换，非法时抛出 ValueError。"""
    if not is_valid_transition(from_status, to_status):
        raise ValueError(
            f"非法的订单状态转换：{from_status.value} → {to_status.value}。"
            f"允许的目标状态：{[s.value for s in _ORDER_STATE_TRANSITIONS.get(from_status, set())]}"
        )


# ── 订单创建辅助函数 ──────────────────────────────────────────────

def max_risk_approval_status(environment: Environment) -> OrderStatus:
    """根据环境返回风控审批自动达到的最高状态。

    BACKTEST / PAPER 环境下，DRAFT 可自动达到 USER_APPROVED。
    LIVE 环境下，必须经过人工审批。
    """
    if environment == Environment.LIVE:
        return OrderStatus.DRAFT
    return OrderStatus.USER_APPROVED


# ── 领域模型 ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class OrderIntent:
    """订单意向（不可变）。

    订单状态机：
      DRAFT → RISK_APPROVED → USER_APPROVED → SUBMITTED → PARTIALLY_FILLED → FILLED → RECONCILED
      异常：→ REJECTED / CANCELLED / EXPIRED / FAILED

    LIVE 环境下的 OrderIntent 必须经过 USER_APPROVED 才能进入 SUBMITTED。
    """
    order_id: str
    account_id: str
    strategy_id: str
    symbol: str
    side: OrderSide
    target_amount: float
    estimated_quantity: float
    signal_date: str
    earliest_execution_date: str
    expiration_date: str | None = None
    estimated_cost: float = 0.0
    status: OrderStatus = OrderStatus.DRAFT
    environment: Environment = Environment.LIVE
    reasons: tuple[str, ...] = field(default_factory=tuple)
    parent_decision_id: str | None = None
    updated_at: str | None = None

    def transition_to(self, new_status: OrderStatus) -> OrderIntent:
        """返回一个新的 OrderIntent 实例，状态转换为 new_status。

        原实例不可变，此方法创建新实例。
        """
        validate_transition(self.status, new_status)
        return OrderIntent(
            order_id=self.order_id,
            account_id=self.account_id,
            strategy_id=self.strategy_id,
            symbol=self.symbol,
            side=self.side,
            target_amount=self.target_amount,
            estimated_quantity=self.estimated_quantity,
            signal_date=self.signal_date,
            earliest_execution_date=self.earliest_execution_date,
            expiration_date=self.expiration_date,
            estimated_cost=self.estimated_cost,
            status=new_status,
            environment=self.environment,
            reasons=self.reasons,
            parent_decision_id=self.parent_decision_id,
            updated_at=datetime.now().isoformat(timespec="seconds"),
        )

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            OrderStatus.REJECTED,
            OrderStatus.CANCELLED,
            OrderStatus.EXPIRED,
            OrderStatus.FAILED,
            OrderStatus.RECONCILED,
        }

    @property
    def is_pending(self) -> bool:
        return self.status in {
            OrderStatus.DRAFT,
            OrderStatus.RISK_APPROVED,
            OrderStatus.USER_APPROVED,
            OrderStatus.SUBMITTED,
            OrderStatus.PARTIALLY_FILLED,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "account_id": self.account_id,
            "strategy_id": self.strategy_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "target_amount": self.target_amount,
            "estimated_quantity": self.estimated_quantity,
            "signal_date": self.signal_date,
            "earliest_execution_date": self.earliest_execution_date,
            "expiration_date": self.expiration_date,
            "estimated_cost": self.estimated_cost,
            "status": self.status.value,
            "environment": self.environment.value,
            "reasons": list(self.reasons),
            "parent_decision_id": self.parent_decision_id,
            "updated_at": self.updated_at,
        }
