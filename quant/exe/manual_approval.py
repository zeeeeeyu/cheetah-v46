"""V4.6 两阶段人工审批。"""
from __future__ import annotations
from dataclasses import dataclass, field; from datetime import datetime; from enum import Enum; from typing import Any
from quant.domain.enums import OrderStatus, Environment

class ApprovalToken(str, Enum):
    MANUAL_LIVE_PLAN_APPROVED = "MANUAL_LIVE_PLAN_APPROVED"
    MANUAL_EXECUTION_CONFIRMED = "MANUAL_EXECUTION_CONFIRMED"
    REJECTED = "REJECTED"

@dataclass
class ApprovalRecord:
    order_id: str; token: ApprovalToken; approved_by: str = "USER"
    approved_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds")); note: str = ""
    def to_dict(self) -> dict[str, Any]: return {"order_id": self.order_id, "token": self.token.value, "approved_by": self.approved_by, "approved_at": self.approved_at, "note": self.note}

def apply_plan_approval(order, record: ApprovalRecord):
    if record.token != ApprovalToken.MANUAL_LIVE_PLAN_APPROVED: raise ValueError(f"无效令牌: {record.token}")
    if order.environment == Environment.LIVE and order.status != OrderStatus.RISK_APPROVED: raise ValueError(f"LIVE 订单需先 RISK_APPROVED")
    return order.transition_to(OrderStatus.USER_APPROVED)

def apply_execution_confirmation(order, record: ApprovalRecord):
    if record.token != ApprovalToken.MANUAL_EXECUTION_CONFIRMED: raise ValueError(f"无效令牌: {record.token}")
    if order.status != OrderStatus.FILLED: raise ValueError(f"需 FILLED 状态")
    return order.transition_to(OrderStatus.RECONCILED)
