"""场外基金订单领域对象（不可变）。

FundOrder 建模一笔场外基金申赎的完整生命周期，遵守以下硬约束：

  1. **未知价原则**：在 CONFIRMED 之前，confirmed_nav / confirmed_units /
     confirmed_amount 必须为 None。系统绝不用信号日净值假设成交价或份额。
  2. **赎回份额上限**：赎回（REDEEM）的申请份额不得超过可赎回份额 available_units。
  3. **只有 CONFIRMED 改真实持仓**：produces_position_effect 仅在 CONFIRMED 为真。
  4. **不可跳态**：所有转换经 states.validate_transition 校验。
  5. **审计追踪**：每次状态转换追加一条不可变 FundOrderEvent。

对象不可变：所有状态变化都返回**新的** FundOrder 实例。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Any

from ..domain.enums import Environment
from .states import (
    POSITION_EFFECTIVE_STATE,
    TERMINAL_STATES,
    UNKNOWN_NAV_STATES,
    FundOrderStatus,
    validate_transition,
)


# ── 基金申赎方向 ────────────────────────────────────────────────────

class FundOrderSide(str, Enum):
    """场外基金申赎方向。

    场外基金规则：申购按**金额**下单，赎回按**份额**下单。
    """
    SUBSCRIBE = "SUBSCRIBE"  # 申购（按金额）
    REDEEM = "REDEEM"        # 赎回（按份额）


# ── 领域异常 ────────────────────────────────────────────────────────

class FundOrderError(ValueError):
    """基金订单领域约束被违反的基类异常。"""


class UnknownNavViolationError(FundOrderError):
    """未知价原则被违反：在确认前写入了净值/份额/金额。"""


class RedemptionExceedsAvailableError(FundOrderError):
    """赎回份额超过可赎回份额上限。"""


# ── 审计事件 ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FundOrderEvent:
    """基金订单状态转换审计事件（不可变）。"""
    from_status: FundOrderStatus
    to_status: FundOrderStatus
    at: str
    actor: str = "SYSTEM"
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_status": self.from_status.value,
            "to_status": self.to_status.value,
            "at": self.at,
            "actor": self.actor,
            "note": self.note,
        }


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ── 领域对象 ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FundOrder:
    """场外基金订单（不可变）。

    生命周期：
      DRAFT → PENDING_REVIEW → APPROVED → SUBMITTED_MANUALLY
            → WAITING_NAV → WAITING_SHARE_CONFIRMATION → CONFIRMED
      异常：→ REJECTED / CANCELLED / EXPIRED / DATA_MISMATCH
    """

    order_id: str
    account_id: str
    strategy_id: str
    symbol: str                          # 基金代码
    side: FundOrderSide

    # 申购按金额、赎回按份额（互斥语义）
    target_amount: float | None = None   # 申购金额（CNY）；赎回时通常为 None
    target_units: float | None = None    # 赎回份额；申购时通常为 None
    available_units: float = 0.0         # 赎回可用份额上限（赎回护栏）

    # 关键日期
    signal_date: str = ""
    application_date: str | None = None           # 用户在平台提交申请日
    nav_date: str | None = None                   # 确认净值公布日（T+1）
    share_confirmation_date: str | None = None    # 份额确认日（T+2）
    expiration_date: str | None = None            # 信号/计划有效期

    # 确认结果（未知价原则：CONFIRMED 前必须为 None）
    confirmed_nav: float | None = None
    confirmed_units: float | None = None
    confirmed_amount: float | None = None

    status: FundOrderStatus = FundOrderStatus.DRAFT
    environment: Environment = Environment.LIVE
    estimated_cost: float = 0.0
    reasons: tuple[str, ...] = field(default_factory=tuple)
    audit_trail: tuple[FundOrderEvent, ...] = field(default_factory=tuple)
    parent_decision_id: str | None = None
    updated_at: str | None = None

    # ── 构造期护栏 ──────────────────────────────────────────────────

    def __post_init__(self) -> None:
        # 护栏 1：赎回份额不得超过可赎回份额。
        if self.side == FundOrderSide.REDEEM:
            if self.target_units is None or self.target_units <= 0:
                raise RedemptionExceedsAvailableError(
                    f"赎回订单 {self.order_id} 必须提供正的赎回份额 target_units"
                )
            if self.target_units > self.available_units + 1e-9:
                raise RedemptionExceedsAvailableError(
                    f"赎回订单 {self.order_id} 赎回份额 {self.target_units} "
                    f"超过可赎回份额 {self.available_units}"
                )
        # 护栏 2：申购必须提供正金额。
        if self.side == FundOrderSide.SUBSCRIBE:
            if self.target_amount is None or self.target_amount <= 0:
                raise FundOrderError(
                    f"申购订单 {self.order_id} 必须提供正的申购金额 target_amount"
                )
        # 护栏 3：未知价原则——确认前不得有净值/份额/金额。
        if self.status in UNKNOWN_NAV_STATES:
            if (self.confirmed_nav is not None
                    or self.confirmed_units is not None
                    or self.confirmed_amount is not None):
                raise UnknownNavViolationError(
                    f"订单 {self.order_id} 处于未知价阶段 {self.status.value}，"
                    f"不得写入确认净值/份额/金额（未知价原则）"
                )

    # ── 状态属性 ────────────────────────────────────────────────────

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATES

    @property
    def is_active(self) -> bool:
        return self.status not in TERMINAL_STATES

    @property
    def produces_position_effect(self) -> bool:
        """是否可改动真实持仓——**仅** CONFIRMED 为真。

        这是"只有确认成交才能改真实持仓"硬约束的领域侧执行点。
        DRAFT / SUBMITTED_MANUALLY / WAITING_* / CANCELLED 等一律返回 False。
        """
        return self.status == POSITION_EFFECTIVE_STATE

    # ── 核心状态转换 ────────────────────────────────────────────────

    def transition_to(
        self,
        new_status: FundOrderStatus,
        *,
        actor: str = "SYSTEM",
        note: str = "",
        **updates: Any,
    ) -> "FundOrder":
        """校验并转换到 new_status，返回新的 FundOrder 实例。

        - 经 validate_transition 强制"不可跳态"；
        - 追加一条审计事件；
        - 允许通过 **updates 更新伴随字段（如 nav_date/confirmed_* 等），
          更新后仍会经 __post_init__ 复核未知价等护栏。
        """
        validate_transition(self.status, new_status)
        now = _now()
        event = FundOrderEvent(
            from_status=self.status,
            to_status=new_status,
            at=now,
            actor=actor,
            note=note,
        )
        return replace(
            self,
            status=new_status,
            audit_trail=self.audit_trail + (event,),
            updated_at=now,
            **updates,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "account_id": self.account_id,
            "strategy_id": self.strategy_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "target_amount": self.target_amount,
            "target_units": self.target_units,
            "available_units": self.available_units,
            "signal_date": self.signal_date,
            "application_date": self.application_date,
            "nav_date": self.nav_date,
            "share_confirmation_date": self.share_confirmation_date,
            "expiration_date": self.expiration_date,
            "confirmed_nav": self.confirmed_nav,
            "confirmed_units": self.confirmed_units,
            "confirmed_amount": self.confirmed_amount,
            "status": self.status.value,
            "environment": self.environment.value,
            "estimated_cost": self.estimated_cost,
            "reasons": list(self.reasons),
            "audit_trail": [e.to_dict() for e in self.audit_trail],
            "parent_decision_id": self.parent_decision_id,
            "updated_at": self.updated_at,
        }
