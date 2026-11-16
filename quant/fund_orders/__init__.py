"""场外基金订单状态机（V4.6 阶段 3 / 审计建议修改顺序第 3 步）。

场外（开放式）基金专属的 11 态订单生命周期，建模未知价、T+1 净值、
T+2 份额确认、两阶段人工审批与对账，确保：

  * 未知价原则：确认前绝不写入净值/份额/金额；
  * 只有 CONFIRMED（确认成交）可改真实持仓；
  * 赎回份额不得超过可赎回份额；
  * 过期信号不可提交；撤单/拒绝/过期无持仓影响；
  * 重复对账幂等；提供计划 vs 实际差异报告；
  * 全程审计追踪、不可跳态。
"""

from __future__ import annotations

from .approval import (
    FundApprovalError,
    FundApprovalRecord,
    FundApprovalToken,
    advance_to_waiting_nav,
    apply_execution_confirmation,
    apply_plan_approval,
    cancel,
    expire,
    flag_data_mismatch,
    record_nav,
    reject,
    submit_for_review,
    submit_manually,
)
from .orders import (
    FundOrder,
    FundOrderError,
    FundOrderEvent,
    FundOrderSide,
    RedemptionExceedsAvailableError,
    UnknownNavViolationError,
)
from .reconciliation import (
    FundExecutionDifference,
    FundPositionDelta,
    FundReconciler,
    FundReconciliationError,
    build_execution_difference,
)
from .states import (
    POSITION_EFFECTIVE_STATE,
    TERMINAL_STATES,
    UNKNOWN_NAV_STATES,
    FundOrderStatus,
    FundOrderTransitionError,
    allowed_transitions,
    is_terminal,
    is_valid_transition,
    validate_transition,
)

__all__ = [
    # states
    "FundOrderStatus",
    "FundOrderTransitionError",
    "allowed_transitions",
    "is_valid_transition",
    "validate_transition",
    "is_terminal",
    "TERMINAL_STATES",
    "UNKNOWN_NAV_STATES",
    "POSITION_EFFECTIVE_STATE",
    # orders
    "FundOrder",
    "FundOrderSide",
    "FundOrderEvent",
    "FundOrderError",
    "UnknownNavViolationError",
    "RedemptionExceedsAvailableError",
    # approval
    "FundApprovalToken",
    "FundApprovalRecord",
    "FundApprovalError",
    "submit_for_review",
    "apply_plan_approval",
    "submit_manually",
    "advance_to_waiting_nav",
    "record_nav",
    "apply_execution_confirmation",
    "flag_data_mismatch",
    "cancel",
    "reject",
    "expire",
    # reconciliation
    "FundReconciler",
    "FundPositionDelta",
    "FundExecutionDifference",
    "FundReconciliationError",
    "build_execution_difference",
]
