"""V4.6 真实持仓领域模型 —— 真实持仓账（第三本账）的核心对象。

ActualPosition 表示"账户真实持有的仓位"。

严格边界（三本账物理隔离的最后一道墙，也是最关键的一道）：
- 它只能由对账（reconciliation）从不可变交易账（Transaction Ledger）聚合产生。
- 每个实例必须携带 reconciliation_run_id 证明其来源，且 source 必须为 RECONCILIATION。
- 目标权重（TargetPortfolio）、决策提案（DecisionProposal）、
  订单意图（OrderIntent，含 SUBMITTED）都无法直接构造出合法的 ActualPosition。
- 领域层在 __post_init__ 即拦截非法来源，先于任何持久化。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_RECONCILIATION_SOURCE = "RECONCILIATION"


class ActualPositionProvenanceError(ValueError):
    """当试图以非对账来源构造真实持仓时抛出。"""


@dataclass(frozen=True)
class ActualPosition:
    """真实持仓账对象（不可变）。

    这是系统对"账户真实持有什么"的唯一权威表达。
    它拒绝一切未经对账的来源——这是"账户事实优先"原则的领域层落点。
    """
    account_id: str
    symbol: str
    quantity: float
    available_quantity: float
    average_cost: float
    market_value: float
    as_of: str
    reconciliation_run_id: str
    """对账运行唯一键。必填且非空——证明本持仓来自一次真实对账。"""

    source: str = _RECONCILIATION_SOURCE
    """来源。必须恒为 RECONCILIATION，否则构造即失败。"""

    strategy_allocations: dict[str, float] = field(default_factory=dict)
    """策略归因（可选）：该持仓在各策略间的拆分。仅为分析用途。"""

    def __post_init__(self) -> None:
        # 领域层护栏：真实持仓只能来自对账。
        if self.source != _RECONCILIATION_SOURCE:
            raise ActualPositionProvenanceError(
                f"真实持仓来源非法：{self.source!r}。"
                f"ActualPosition 只能由对账（{_RECONCILIATION_SOURCE}）产生。"
            )
        if not self.reconciliation_run_id or not str(self.reconciliation_run_id).strip():
            raise ActualPositionProvenanceError(
                "真实持仓缺少 reconciliation_run_id：无法证明其来自一次对账。"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "symbol": self.symbol,
            "quantity": self.quantity,
            "available_quantity": self.available_quantity,
            "average_cost": self.average_cost,
            "market_value": self.market_value,
            "as_of": self.as_of,
            "reconciliation_run_id": self.reconciliation_run_id,
            "source": self.source,
            "strategy_allocations": dict(self.strategy_allocations),
        }
