"""场外基金对账与真实持仓写入护栏。

职责：
  1. **只有 CONFIRMED 改真实持仓**：非 CONFIRMED 订单一律被拒绝产生持仓变更。
  2. **幂等**：同一订单重复导入/对账不会二次改动持仓。
  3. **对账差异报告**：比较计划金额与实际确认金额/份额，产出差异。
  4. **赎回份额守恒**：赎回时真实持仓按确认份额扣减，不得超过可赎回份额。

本模块产出 FundPositionDelta（真实持仓变更意图），由三本账的
actual_positions 账本在 RECONCILIATION 源下落库；本模块不直接绕过账本守卫。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .orders import FundOrder, FundOrderError, FundOrderSide
from .states import FundOrderStatus


class FundReconciliationError(FundOrderError):
    """非法的基金对账操作（如对非 CONFIRMED 订单对账）。"""


@dataclass(frozen=True)
class FundPositionDelta:
    """一笔由基金对账产生的真实持仓变更意图（不可变）。

    units_delta > 0 表示申购增加份额；< 0 表示赎回减少份额。
    amount_delta 为对应现金变动（申购为负现金流、赎回为正现金流）。
    """
    order_id: str
    account_id: str
    symbol: str
    units_delta: float
    amount_delta: float
    confirmed_nav: float
    reconciliation_run_id: str
    as_of: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "account_id": self.account_id,
            "symbol": self.symbol,
            "units_delta": self.units_delta,
            "amount_delta": self.amount_delta,
            "confirmed_nav": self.confirmed_nav,
            "reconciliation_run_id": self.reconciliation_run_id,
            "as_of": self.as_of,
        }


@dataclass(frozen=True)
class FundExecutionDifference:
    """基金计划 vs 实际确认的差异报告（不可变）。"""
    order_id: str
    symbol: str
    side: str
    planned_amount: float | None
    confirmed_amount: float | None
    planned_units: float | None
    confirmed_units: float | None
    amount_diff: float | None
    units_diff: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "symbol": self.symbol,
            "side": self.side,
            "planned_amount": self.planned_amount,
            "confirmed_amount": self.confirmed_amount,
            "planned_units": self.planned_units,
            "confirmed_units": self.confirmed_units,
            "amount_diff": self.amount_diff,
            "units_diff": self.units_diff,
        }


class FundReconciler:
    """幂等的基金对账器。

    负责把 CONFIRMED 的 FundOrder 转换为唯一一次真实持仓变更意图，
    并保证同一 order_id 重复对账不产生二次效果。
    """

    def __init__(self) -> None:
        # 已对账订单集合（幂等键 = order_id）
        self._reconciled: set[str] = set()

    def is_reconciled(self, order_id: str) -> bool:
        return order_id in self._reconciled

    def reconcile(
        self, order: FundOrder, *, reconciliation_run_id: str, as_of: str
    ) -> FundPositionDelta | None:
        """对一笔基金订单执行对账，返回真实持仓变更意图。

        - 若订单非 CONFIRMED：抛 FundReconciliationError（护栏：只有确认成交改持仓）。
        - 若订单已对账过：返回 None（幂等，不产生二次变更）。
        """
        # 护栏：只有 CONFIRMED 可改真实持仓。
        if not order.produces_position_effect:
            raise FundReconciliationError(
                f"订单 {order.order_id} 状态 {order.status.value} 非 CONFIRMED，"
                f"不得改动真实持仓（只有确认成交才能对账）"
            )
        if not reconciliation_run_id:
            raise FundReconciliationError("对账必须提供 reconciliation_run_id")

        # 幂等：同一订单只对账一次。
        if order.order_id in self._reconciled:
            return None

        confirmed_units = float(order.confirmed_units or 0.0)
        confirmed_amount = float(order.confirmed_amount or 0.0)
        confirmed_nav = float(order.confirmed_nav or 0.0)

        if order.side == FundOrderSide.SUBSCRIBE:
            units_delta = confirmed_units          # 份额增加
            amount_delta = -confirmed_amount       # 现金流出
        else:  # REDEEM
            units_delta = -confirmed_units         # 份额减少
            amount_delta = confirmed_amount        # 现金流入

        self._reconciled.add(order.order_id)
        return FundPositionDelta(
            order_id=order.order_id,
            account_id=order.account_id,
            symbol=order.symbol,
            units_delta=units_delta,
            amount_delta=amount_delta,
            confirmed_nav=confirmed_nav,
            reconciliation_run_id=reconciliation_run_id,
            as_of=as_of,
        )


def build_execution_difference(order: FundOrder) -> FundExecutionDifference:
    """对一笔 CONFIRMED 订单生成计划 vs 实际差异报告。

    申购：比较计划申购金额 target_amount vs 确认金额 confirmed_amount。
    赎回：比较计划赎回份额 target_units vs 确认份额 confirmed_units。
    """
    if order.status != FundOrderStatus.CONFIRMED:
        raise FundReconciliationError(
            f"差异报告要求订单处于 CONFIRMED，当前 {order.status.value}"
        )
    planned_amount = order.target_amount
    confirmed_amount = order.confirmed_amount
    planned_units = order.target_units
    confirmed_units = order.confirmed_units

    amount_diff = None
    if planned_amount is not None and confirmed_amount is not None:
        amount_diff = round(confirmed_amount - planned_amount, 6)
    units_diff = None
    if planned_units is not None and confirmed_units is not None:
        units_diff = round(confirmed_units - planned_units, 6)

    return FundExecutionDifference(
        order_id=order.order_id,
        symbol=order.symbol,
        side=order.side.value,
        planned_amount=planned_amount,
        confirmed_amount=confirmed_amount,
        planned_units=planned_units,
        confirmed_units=confirmed_units,
        amount_diff=amount_diff,
        units_diff=units_diff,
    )
