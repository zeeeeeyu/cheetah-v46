"""V4.6 目标组合领域模型 —— 模型目标账（第一本账）的核心对象。

TargetPortfolio 表示"策略/模型希望账户达到的目标权重"。

严格边界（三本账物理隔离的第一道墙）：
- 它是"想要什么"，不是"决定做什么"（DecisionProposal），
  更不是"实际持有什么"（ActualPosition）。
- 只含权重（weight），绝不含 quantity / filled / order_id / 成交价。
- 它不可变（frozen），且无任何修改持仓的方法。
- 写入目标组合永远不会改变真实持仓——这是必须由负向测试锁死的不变量。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .enums import Environment, TrustLevel


@dataclass(frozen=True)
class TargetPortfolio:
    """模型目标账对象（不可变）。

    由策略层基于 ForecastBundle / 规则生成，表达期望的资产配置。
    它进入决策仲裁层后，才可能被转化为 DecisionProposal。
    """
    portfolio_id: str
    """目标组合唯一键。"""

    account_id: str
    """归属账户。"""

    strategy_id: str
    """产生该目标的策略。"""

    as_of: str
    """目标对应的时间戳。"""

    environment: Environment
    """环境（BACKTEST / PAPER / LIVE）。目标必须显式关联环境。"""

    target_weights: dict[str, float] = field(default_factory=dict)
    """标的 -> 目标权重。权重之和 + cash_weight 应约等于 1。"""

    cash_weight: float = 0.0
    """现金目标权重。"""

    source_forecast_id: str | None = None
    """上游 ForecastBundle 的 bundle_id（血缘追踪）。可为空（纯规则策略）。"""

    trust_level: TrustLevel = TrustLevel.L6_PROVEN
    """该目标所依赖信息的可信级别。纯规则/成熟策略默认可信。"""

    reasons: tuple[str, ...] = field(default_factory=tuple)
    """生成目标的理由，用于审计。"""

    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def non_cash_assets(self) -> list[str]:
        return [s for s in self.target_weights if s.upper() != "CASH"]

    @property
    def total_weight(self) -> float:
        return sum(self.target_weights.values()) + self.cash_weight

    def to_dict(self) -> dict[str, Any]:
        return {
            "portfolio_id": self.portfolio_id,
            "account_id": self.account_id,
            "strategy_id": self.strategy_id,
            "as_of": self.as_of,
            "environment": self.environment.value,
            "target_weights": dict(self.target_weights),
            "cash_weight": self.cash_weight,
            "source_forecast_id": self.source_forecast_id,
            "trust_level": int(self.trust_level.value),
            "reasons": list(self.reasons),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TargetPortfolio:
        kwargs = dict(data)
        if "environment" in kwargs and isinstance(kwargs["environment"], str):
            kwargs["environment"] = Environment(kwargs["environment"])
        if "trust_level" in kwargs and not isinstance(kwargs["trust_level"], TrustLevel):
            kwargs["trust_level"] = TrustLevel(int(kwargs["trust_level"]))
        if "target_weights" in kwargs and kwargs["target_weights"] is not None:
            kwargs["target_weights"] = dict(kwargs["target_weights"])
        if "reasons" in kwargs and isinstance(kwargs["reasons"], list):
            kwargs["reasons"] = tuple(kwargs["reasons"])
        if "metadata" in kwargs and kwargs["metadata"] is not None:
            kwargs["metadata"] = dict(kwargs["metadata"])
        return cls(**kwargs)
