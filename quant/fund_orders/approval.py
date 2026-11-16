"""场外基金两阶段人工审批引擎。

阶段 9 硬约束：人工批准必须分成**两个独立阶段**，且**不得用一个令牌
同时代表计划批准和成交完成**：

  1. MANUAL_LIVE_PLAN_APPROVED —— **只**批准交易计划
       PENDING_REVIEW → APPROVED
     授权用户去销售平台下单，但绝不代表成交。

  2. MANUAL_EXECUTION_CONFIRMED —— **只**确认实际成交
       WAITING_SHARE_CONFIRMATION → CONFIRMED
     基于销售平台已确认的份额/金额，是唯一能改真实持仓的令牌。

任何令牌错配（用计划令牌去确认成交，或反之）都会被拒绝。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from ..domain.enums import Environment
from .orders import FundOrder, FundOrderError
from .states import FundOrderStatus


class FundApprovalToken(str, Enum):
    """基金人工审批令牌。两种令牌语义严格互斥。"""
    MANUAL_LIVE_PLAN_APPROVED = "MANUAL_LIVE_PLAN_APPROVED"
    MANUAL_EXECUTION_CONFIRMED = "MANUAL_EXECUTION_CONFIRMED"
    REJECTED = "REJECTED"


class FundApprovalError(FundOrderError):
    """基金审批令牌错配或前置状态不满足。"""


@dataclass(frozen=True)
class FundApprovalRecord:
    """一条人工审批记录（不可变，供审计）。"""
    order_id: str
    token: FundApprovalToken
    approved_by: str = "USER"
    approved_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    note: str = ""
    # 成交确认令牌专用：来自销售平台的已确认结果
    confirmed_nav: float | None = None
    confirmed_units: float | None = None
    confirmed_amount: float | None = None
    nav_date: str | None = None
    share_confirmation_date: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "token": self.token.value,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at,
            "note": self.note,
            "confirmed_nav": self.confirmed_nav,
            "confirmed_units": self.confirmed_units,
            "confirmed_amount": self.confirmed_amount,
            "nav_date": self.nav_date,
            "share_confirmation_date": self.share_confirmation_date,
        }


# ── 生命周期推进函数 ────────────────────────────────────────────────

def submit_for_review(order: FundOrder, *, actor: str = "RISK") -> FundOrder:
    """DRAFT → PENDING_REVIEW（风控/系统把草稿送入人工计划复核队列）。"""
    return order.transition_to(
        FundOrderStatus.PENDING_REVIEW, actor=actor, note="送入人工计划复核"
    )


def apply_plan_approval(order: FundOrder, record: FundApprovalRecord) -> FundOrder:
    """PENDING_REVIEW → APPROVED，**仅**接受 MANUAL_LIVE_PLAN_APPROVED 令牌。

    只批准计划，不代表任何成交。LIVE 订单必须先处于 PENDING_REVIEW。
    """
    if record.token != FundApprovalToken.MANUAL_LIVE_PLAN_APPROVED:
        raise FundApprovalError(
            f"计划批准只接受 MANUAL_LIVE_PLAN_APPROVED，收到 {record.token.value}"
        )
    if order.environment == Environment.LIVE and order.status != FundOrderStatus.PENDING_REVIEW:
        raise FundApprovalError(
            f"LIVE 基金订单计划批准前必须处于 PENDING_REVIEW，当前 {order.status.value}"
        )
    return order.transition_to(
        FundOrderStatus.APPROVED,
        actor=record.approved_by,
        note=f"计划批准 (token={record.token.value})",
    )


def submit_manually(
    order: FundOrder, *, application_date: str, actor: str = "USER"
) -> FundOrder:
    """APPROVED → SUBMITTED_MANUALLY（用户已在销售平台按未知价手工提交）。

    过期护栏：若 order 已过 expiration_date，不允许提交（应先置 EXPIRED）。
    """
    if order.status == FundOrderStatus.EXPIRED:
        raise FundApprovalError(f"订单 {order.order_id} 已过期，不可提交")
    if order.expiration_date and application_date > order.expiration_date:
        raise FundApprovalError(
            f"订单 {order.order_id} 提交日 {application_date} 已晚于有效期 "
            f"{order.expiration_date}，不可提交（应置为 EXPIRED）"
        )
    return order.transition_to(
        FundOrderStatus.SUBMITTED_MANUALLY,
        actor=actor,
        note="用户按未知价在销售平台手工提交",
        application_date=application_date,
    )


def advance_to_waiting_nav(order: FundOrder, *, actor: str = "SYSTEM") -> FundOrder:
    """SUBMITTED_MANUALLY → WAITING_NAV（当日截止已过，等待 T+1 净值）。"""
    return order.transition_to(
        FundOrderStatus.WAITING_NAV, actor=actor, note="当日截止，等待 T+1 净值"
    )


def record_nav(
    order: FundOrder, *, nav: float, nav_date: str, actor: str = "SYSTEM"
) -> FundOrder:
    """WAITING_NAV → WAITING_SHARE_CONFIRMATION（T+1 净值公布）。

    这是唯一可写入 confirmed_nav 的入口。净值必须为正。
    """
    if nav is None or nav <= 0:
        raise FundApprovalError(f"订单 {order.order_id} 确认净值必须为正，收到 {nav}")
    return order.transition_to(
        FundOrderStatus.WAITING_SHARE_CONFIRMATION,
        actor=actor,
        note=f"T+1 净值确认 nav={nav}",
        confirmed_nav=nav,
        nav_date=nav_date,
    )


def apply_execution_confirmation(order: FundOrder, record: FundApprovalRecord) -> FundOrder:
    """WAITING_SHARE_CONFIRMATION → CONFIRMED，**仅**接受 MANUAL_EXECUTION_CONFIRMED 令牌。

    只确认实际成交，是唯一能让订单进入"可改真实持仓"终态的路径。
    需提供销售平台确认的份额与金额。若数据缺失/异常，调用方应转 DATA_MISMATCH。
    """
    if record.token != FundApprovalToken.MANUAL_EXECUTION_CONFIRMED:
        raise FundApprovalError(
            f"成交确认只接受 MANUAL_EXECUTION_CONFIRMED，收到 {record.token.value}"
        )
    if order.status != FundOrderStatus.WAITING_SHARE_CONFIRMATION:
        raise FundApprovalError(
            f"成交确认前必须处于 WAITING_SHARE_CONFIRMATION，当前 {order.status.value}"
        )
    if record.confirmed_units is None or record.confirmed_units <= 0:
        raise FundApprovalError(
            f"成交确认必须提供正的确认份额 confirmed_units，收到 {record.confirmed_units}"
        )
    if record.confirmed_amount is None or record.confirmed_amount <= 0:
        raise FundApprovalError(
            f"成交确认必须提供正的确认金额 confirmed_amount，收到 {record.confirmed_amount}"
        )
    return order.transition_to(
        FundOrderStatus.CONFIRMED,
        actor=record.approved_by,
        note=f"成交确认 (token={record.token.value})",
        confirmed_units=record.confirmed_units,
        confirmed_amount=record.confirmed_amount,
        share_confirmation_date=record.share_confirmation_date,
    )


def flag_data_mismatch(order: FundOrder, *, reason: str, actor: str = "SYSTEM") -> FundOrder:
    """任意确认阶段 → DATA_MISMATCH（确认数据与预期不一致，需人工干预）。"""
    return order.transition_to(
        FundOrderStatus.DATA_MISMATCH, actor=actor, note=f"数据不一致: {reason}"
    )


def cancel(order: FundOrder, *, reason: str = "", actor: str = "USER") -> FundOrder:
    """撤单 → CANCELLED（仅在合法状态，通常为净值截止前）。"""
    return order.transition_to(
        FundOrderStatus.CANCELLED, actor=actor, note=f"撤单: {reason}"
    )


def reject(order: FundOrder, *, reason: str = "", actor: str = "RISK") -> FundOrder:
    """拒绝 → REJECTED。"""
    return order.transition_to(
        FundOrderStatus.REJECTED, actor=actor, note=f"拒绝: {reason}"
    )


def expire(order: FundOrder, *, actor: str = "SYSTEM") -> FundOrder:
    """过期 → EXPIRED。"""
    return order.transition_to(
        FundOrderStatus.EXPIRED, actor=actor, note="信号/计划过期"
    )
