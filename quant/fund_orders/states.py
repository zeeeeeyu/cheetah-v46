"""场外基金订单状态机（11 态）。

场外（开放式）基金的执行现实与场内 ETF 完全不同：
  1. 无券商自动接口，用户必须在销售平台**手工提交**申赎申请；
  2. **未知价**：提交时净值未知，绝不能用信号日净值假设成交价/份额；
  3. **T+1 净值**：申请日次日才公布确认净值；
  4. **T+2 份额确认**：净值公布后才确认最终份额/金额；
  5. 只有份额被销售平台确认（CONFIRMED）后，才允许进入对账、改动真实持仓。

因此基金订单不能直接复用 ETF 的 OrderStatus，而是使用本模块定义的
11 态专属状态机。状态语义与合法转换严格建模上述现实，任何"跳态"都被拒绝。

与统一订单状态机的对应关系（概念映射，非代码耦合）::

    ETF (OrderStatus)          场外基金 (FundOrderStatus)
    ------------------         --------------------------------
    DRAFT                  ->  DRAFT
    RISK_APPROVED          ->  PENDING_REVIEW
    USER_APPROVED          ->  APPROVED               (计划批准令牌)
    SUBMITTED              ->  SUBMITTED_MANUALLY      (用户在平台手工提交)
    (无对应)               ->  WAITING_NAV             (T+1 等待净值)
    (无对应)               ->  WAITING_SHARE_CONFIRMATION (T+2 等待份额确认)
    FILLED + RECONCILED    ->  CONFIRMED              (份额确认 = 成交确认令牌)
    REJECTED/CANCELLED/    ->  REJECTED/CANCELLED/
    EXPIRED/FAILED             EXPIRED/DATA_MISMATCH
"""

from __future__ import annotations

from enum import Enum


class FundOrderStatus(str, Enum):
    """场外基金订单的 11 个状态。"""

    DRAFT = "DRAFT"
    """草稿：系统由目标权重生成的申赎意向，尚未提交风控/人工复核。"""

    PENDING_REVIEW = "PENDING_REVIEW"
    """待复核：已进入待人工计划批准队列，等待 MANUAL_LIVE_PLAN_APPROVED 令牌。"""

    APPROVED = "APPROVED"
    """计划已批准：计划批准令牌已应用；用户获授权可去销售平台下单，但尚未提交。"""

    SUBMITTED_MANUALLY = "SUBMITTED_MANUALLY"
    """已手工提交：用户已在销售平台按未知价提交申赎申请，等待当日收盘净值。"""

    WAITING_NAV = "WAITING_NAV"
    """等待净值：当日截止已过，等待 T+1 公布确认净值。此阶段净值仍未知。"""

    WAITING_SHARE_CONFIRMATION = "WAITING_SHARE_CONFIRMATION"
    """等待份额确认：确认净值已公布，等待 T+2 销售平台确认最终份额/金额。"""

    CONFIRMED = "CONFIRMED"
    """已确认：份额/金额已由销售平台确认。**唯一**可进入对账、改动真实持仓的终态。"""

    REJECTED = "REJECTED"
    """已拒绝：被风控或人工在批准前拒绝（终态，无持仓影响）。"""

    CANCELLED = "CANCELLED"
    """已撤单：在净值截止前于平台撤销申请（终态，无持仓影响）。"""

    EXPIRED = "EXPIRED"
    """已过期：信号或计划超过有效期未提交（终态，无持仓影响）。"""

    DATA_MISMATCH = "DATA_MISMATCH"
    """数据不一致：确认份额/金额与预期显著偏离，需人工干预（非终态，可修复或撤销）。"""


# ── 合法状态转换表 ──────────────────────────────────────────────────
#
# 设计要点：
#  * 撤单（CANCELLED）只允许在净值截止前，即 SUBMITTED_MANUALLY 阶段；
#    一旦进入 WAITING_NAV（当日截止已过、净值即将/已锁定），不再可撤。
#  * DATA_MISMATCH 是可修复的异常态：人工核对后可回到 WAITING_SHARE_CONFIRMATION
#    重试确认、直接 CONFIRMED（修正后），或最终撤销/拒绝。
#  * CONFIRMED / REJECTED / CANCELLED / EXPIRED 为终态，无任何出边。
_FUND_ORDER_TRANSITIONS: dict[FundOrderStatus, frozenset[FundOrderStatus]] = {
    FundOrderStatus.DRAFT: frozenset({
        FundOrderStatus.PENDING_REVIEW,
        FundOrderStatus.REJECTED,
        FundOrderStatus.CANCELLED,
        FundOrderStatus.EXPIRED,
    }),
    FundOrderStatus.PENDING_REVIEW: frozenset({
        FundOrderStatus.APPROVED,
        FundOrderStatus.REJECTED,
        FundOrderStatus.CANCELLED,
        FundOrderStatus.EXPIRED,
    }),
    FundOrderStatus.APPROVED: frozenset({
        FundOrderStatus.SUBMITTED_MANUALLY,
        FundOrderStatus.CANCELLED,
        FundOrderStatus.EXPIRED,
    }),
    FundOrderStatus.SUBMITTED_MANUALLY: frozenset({
        FundOrderStatus.WAITING_NAV,
        FundOrderStatus.CANCELLED,       # 净值截止前可撤
        FundOrderStatus.DATA_MISMATCH,
    }),
    FundOrderStatus.WAITING_NAV: frozenset({
        FundOrderStatus.WAITING_SHARE_CONFIRMATION,
        FundOrderStatus.DATA_MISMATCH,
    }),
    FundOrderStatus.WAITING_SHARE_CONFIRMATION: frozenset({
        FundOrderStatus.CONFIRMED,
        FundOrderStatus.DATA_MISMATCH,
    }),
    FundOrderStatus.DATA_MISMATCH: frozenset({
        FundOrderStatus.WAITING_SHARE_CONFIRMATION,
        FundOrderStatus.CONFIRMED,
        FundOrderStatus.CANCELLED,
        FundOrderStatus.REJECTED,
    }),
    # 终态
    FundOrderStatus.CONFIRMED: frozenset(),
    FundOrderStatus.REJECTED: frozenset(),
    FundOrderStatus.CANCELLED: frozenset(),
    FundOrderStatus.EXPIRED: frozenset(),
}

# 终态集合：无出边、生命周期结束。
TERMINAL_STATES: frozenset[FundOrderStatus] = frozenset({
    FundOrderStatus.CONFIRMED,
    FundOrderStatus.REJECTED,
    FundOrderStatus.CANCELLED,
    FundOrderStatus.EXPIRED,
})

# 未知价阶段：此区间内确认净值/份额必须保持 None（未知价原则）。
UNKNOWN_NAV_STATES: frozenset[FundOrderStatus] = frozenset({
    FundOrderStatus.DRAFT,
    FundOrderStatus.PENDING_REVIEW,
    FundOrderStatus.APPROVED,
    FundOrderStatus.SUBMITTED_MANUALLY,
    FundOrderStatus.WAITING_NAV,
})

# 唯一可改动真实持仓的状态。
POSITION_EFFECTIVE_STATE: FundOrderStatus = FundOrderStatus.CONFIRMED


class FundOrderTransitionError(ValueError):
    """非法的基金订单状态转换。"""


def allowed_transitions(status: FundOrderStatus) -> frozenset[FundOrderStatus]:
    """返回给定状态的所有合法后继状态。"""
    return _FUND_ORDER_TRANSITIONS.get(status, frozenset())


def is_valid_transition(from_status: FundOrderStatus, to_status: FundOrderStatus) -> bool:
    """判断状态转换是否合法。"""
    return to_status in _FUND_ORDER_TRANSITIONS.get(from_status, frozenset())


def validate_transition(from_status: FundOrderStatus, to_status: FundOrderStatus) -> None:
    """校验状态转换，非法时抛出 FundOrderTransitionError。

    这是"不可跳态"硬约束的执行点：例如 DRAFT 不能直接跳到 CONFIRMED，
    SUBMITTED_MANUALLY 不能直接跳到 CONFIRMED（必须经 WAITING_NAV →
    WAITING_SHARE_CONFIRMATION）。
    """
    if not is_valid_transition(from_status, to_status):
        allowed = sorted(s.value for s in _FUND_ORDER_TRANSITIONS.get(from_status, frozenset()))
        raise FundOrderTransitionError(
            f"非法的基金订单状态转换：{from_status.value} → {to_status.value}。"
            f"合法后继：{allowed or '（终态，无出边）'}"
        )


def is_terminal(status: FundOrderStatus) -> bool:
    """是否为终态。"""
    return status in TERMINAL_STATES
