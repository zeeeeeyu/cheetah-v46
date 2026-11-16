"""V4.6 风险领域模型。

从"单只资产不超过多少比例"升级为组合波动率约束、
风险簇约束、策略风险预算、回撤预算、相关性约束和流动性约束。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .enums import MarketRegime


@dataclass(frozen=True)
class RiskClusterExposure:
    """风险簇暴露快照。"""
    cluster_name: str
    total_weight: float
    limit: float
    asset_contributions: dict[str, float] = field(default_factory=dict)
    breached: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "cluster_name": self.cluster_name,
            "total_weight": self.total_weight,
            "limit": self.limit,
            "asset_contributions": dict(self.asset_contributions),
            "breached": self.breached,
        }


@dataclass(frozen=True)
class RiskBudget:
    """风险预算定义。

    策略获得账户总风险预算的一部分。
    """
    strategy_id: str
    budget_weight: float
    max_drawdown: float
    max_volatility: float
    max_cluster_exposure: dict[str, float] = field(default_factory=dict)
    max_correlation: float = 0.85
    var_confidence: float = 0.95

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "budget_weight": self.budget_weight,
            "max_drawdown": self.max_drawdown,
            "max_volatility": self.max_volatility,
            "max_cluster_exposure": dict(self.max_cluster_exposure),
            "max_correlation": self.max_correlation,
            "var_confidence": self.var_confidence,
        }


@dataclass(frozen=True)
class PortfolioRiskSnapshot:
    """组合风险快照。"""
    account_id: str
    as_of_date: str
    total_volatility: float = 0.0
    downside_volatility: float = 0.0
    max_drawdown: float = 0.0
    conditional_var: float = 0.0
    cluster_exposures: tuple[RiskClusterExposure, ...] = field(default_factory=tuple)
    strategy_contributions: dict[str, float] = field(default_factory=dict)
    cash_ratio: float = 0.0
    regime: MarketRegime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "as_of_date": self.as_of_date,
            "total_volatility": self.total_volatility,
            "downside_volatility": self.downside_volatility,
            "max_drawdown": self.max_drawdown,
            "conditional_var": self.conditional_var,
            "cluster_exposures": [c.to_dict() for c in self.cluster_exposures],
            "strategy_contributions": dict(self.strategy_contributions),
            "cash_ratio": self.cash_ratio,
            "regime": self.regime.value if self.regime else None,
        }


@dataclass(frozen=True)
class RiskVetoResult:
    """风险否决结果。"""
    vetoed: bool
    reason: str
    vetoed_by: str  # RISK_BUDGET / CLUSTER_LIMIT / DRAWDOWN / VOLATILITY / CORRELATION / LIQUIDITY
    affected_strategies: tuple[str, ...] = field(default_factory=tuple)
    suggested_action: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "vetoed": self.vetoed,
            "reason": self.reason,
            "vetoed_by": self.vetoed_by,
            "affected_strategies": list(self.affected_strategies),
            "suggested_action": self.suggested_action,
        }
