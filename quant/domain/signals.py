"""V4.6 信号与决策领域模型。

策略输出不直接包含真实成交。
所有信号包含版本与有效期。
所有决策包含原因。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .enums import DecisionStatus, Environment, MarketRegime


@dataclass(frozen=True)
class StrategySignal:
    """策略信号（不可变）。

    由策略引擎生成，包含评分、置信度、有效期和关联特征快照。
    信号不直接触发交易——它经过决策仲裁后被转换为 OrderIntent。
    """
    strategy_id: str
    strategy_version: str
    symbol: str
    signal_date: str
    signal_type: str  # TREND / BREAKOUT / MEAN_REVERSION / RISK_WARNING / HOLD
    score: float
    confidence: float
    expected_horizon: str  # SHORT / MEDIUM / LONG
    expires_at: datetime | None = None
    risk_direction: str = "NEUTRAL"  # BULLISH / BEARISH / NEUTRAL
    reasons: tuple[str, ...] = field(default_factory=tuple)
    feature_snapshot_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_expired(self, as_of: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        check_time = as_of or datetime.now()
        return check_time > self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "strategy_version": self.strategy_version,
            "symbol": self.symbol,
            "signal_date": self.signal_date,
            "signal_type": self.signal_type,
            "score": self.score,
            "confidence": self.confidence,
            "expected_horizon": self.expected_horizon,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "risk_direction": self.risk_direction,
            "reasons": list(self.reasons),
            "feature_snapshot_id": self.feature_snapshot_id,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class RejectedSignal:
    """被否决的信号（不可变）。

    记录信号本身和否决原因，用于审计和仲裁追踪。
    """
    signal: StrategySignal
    rejection_reason: str
    rejected_by: str  # RISK_VETO / CONFLICT_RESOLVER / DATA_QUALITY / MANUAL


@dataclass(frozen=True)
class StrategyDecision:
    """单一策略的决策输出（不可变）。

    包含多个信号、目标权重、预期换手和理由。
    不等同于真实成交——必须经过 PortfolioDecision → OrderIntent 链。
    """
    strategy_id: str
    as_of_date: str
    environment: Environment
    target_weights: dict[str, float]
    signals: tuple[StrategySignal, ...] = field(default_factory=tuple)
    cash_weight: float = 0.0
    expected_turnover: float = 0.0
    decision_status: DecisionStatus = DecisionStatus.HOLD
    reasons: tuple[str, ...] = field(default_factory=tuple)
    strategy_version: str = "0.0.0"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def non_cash_assets(self) -> list[str]:
        return [symbol for symbol in self.target_weights if symbol.upper() != "CASH"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "as_of_date": self.as_of_date,
            "environment": self.environment.value,
            "target_weights": dict(self.target_weights),
            "signals": [s.to_dict() for s in self.signals],
            "cash_weight": self.cash_weight,
            "expected_turnover": self.expected_turnover,
            "decision_status": self.decision_status.value,
            "reasons": list(self.reasons),
            "strategy_version": self.strategy_version,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class PortfolioDecision:
    """组合级决策（不可变）。

    将多个 StrategyDecision 合并为统一组合目标。
    包含风险否决结果和仲裁通过的信号列表。
    """
    account_id: str
    as_of_date: str
    strategy_decisions: tuple[StrategyDecision, ...] = field(default_factory=tuple)
    approved_strategy_budgets: dict[str, float] = field(default_factory=dict)
    aggregate_target_weights: dict[str, float] = field(default_factory=dict)
    rejected_signals: tuple[RejectedSignal, ...] = field(default_factory=tuple)
    risk_metrics: dict[str, float] = field(default_factory=dict)
    approval_required: bool = True
    reasons: tuple[str, ...] = field(default_factory=tuple)
    environment: Environment = Environment.LIVE

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "as_of_date": self.as_of_date,
            "strategy_decisions": [d.to_dict() for d in self.strategy_decisions],
            "approved_strategy_budgets": dict(self.approved_strategy_budgets),
            "aggregate_target_weights": dict(self.aggregate_target_weights),
            "rejected_signals": [
                {"signal": rs.signal.to_dict(), "rejection_reason": rs.rejection_reason, "rejected_by": rs.rejected_by}
                for rs in self.rejected_signals
            ],
            "risk_metrics": dict(self.risk_metrics),
            "approval_required": self.approval_required,
            "reasons": list(self.reasons),
            "environment": self.environment.value,
        }
