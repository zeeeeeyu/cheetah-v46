"""V4.6 决策提案领域模型 —— 决策计划账（第二本账）的核心对象。

DecisionProposal 表示"经决策仲裁后，系统决定采取的行动方案"。

严格边界（三本账物理隔离的第二道墙）：
- 它是"决定做什么"，介于"想要什么"（TargetPortfolio）与
  "实际发生了什么"（ActualPosition）之间。
- 它链接到一组 OrderIntent（订单意图），意图 != 成交。
- 它需要审批（approval_required），审批不改变真实持仓。
- 它不可变（frozen），不含真实成交价、真实份额或真实持仓。
- 即使其内含 OrderIntent 已到 SUBMITTED，也不代表真实持仓变化——
  真实持仓只能由对账（reconciliation）产生。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .enums import Environment
from .orders import OrderIntent


@dataclass(frozen=True)
class DecisionProposal:
    """决策计划账对象（不可变）。

    由决策仲裁层（冲突仲裁 + 风险否决）产出，是人机协同审批的载体。
    审批通过后，其 OrderIntent 才被推送到执行层；执行完成并对账后，
    才会（间接地、经真实持仓账）反映为 ActualPosition。
    """
    proposal_id: str
    """提案唯一键。"""

    account_id: str
    """归属账户。"""

    as_of: str
    """提案生成时间戳。"""

    environment: Environment
    """环境。提案必须显式关联环境。"""

    source_portfolio_id: str | None = None
    """上游 TargetPortfolio 的 portfolio_id（血缘追踪）。"""

    order_intents: tuple[OrderIntent, ...] = field(default_factory=tuple)
    """订单意图集合。意图，不是成交。"""

    approval_required: bool = True
    """是否需要人工审批。LIVE 默认必须审批。"""

    approval_status: str = "PENDING"
    """审批状态：PENDING / APPROVED / REJECTED。"""

    risk_metrics: dict[str, float] = field(default_factory=dict)
    """决策时的组合风险指标快照。"""

    reasons: tuple[str, ...] = field(default_factory=tuple)
    """决策理由（含被否决信号摘要），用于审计。"""

    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_approved(self) -> bool:
        return self.approval_status == "APPROVED"

    @property
    def order_ids(self) -> list[str]:
        return [o.order_id for o in self.order_intents]

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "account_id": self.account_id,
            "as_of": self.as_of,
            "environment": self.environment.value,
            "source_portfolio_id": self.source_portfolio_id,
            "order_intents": [o.to_dict() for o in self.order_intents],
            "approval_required": self.approval_required,
            "approval_status": self.approval_status,
            "risk_metrics": dict(self.risk_metrics),
            "reasons": list(self.reasons),
            "metadata": dict(self.metadata),
        }
