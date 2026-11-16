from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


class DataStatus(str, Enum):
    PASS_ = "PASS"
    WARNING = "WARNING"
    BLOCK = "BLOCK"


class DecisionStatus(str, Enum):
    DATA_BLOCKED = "DATA_BLOCKED"
    MIGRATION_REQUIRED = "MIGRATION_REQUIRED"
    HOLD = "HOLD"
    HOLD_RECHECK = "HOLD_RECHECK"
    REBALANCE_CANDIDATE = "REBALANCE_CANDIDATE"
    RISK_OVERRIDE = "RISK_OVERRIDE"


class Regime(str, Enum):
    RISK_ON = "RISK_ON"
    NEUTRAL = "NEUTRAL"
    RISK_OFF = "RISK_OFF"


@dataclass
class CheckResult:
    status: DataStatus
    reasons: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        return value


@dataclass
class Signal:
    symbol: str
    name: str
    asset_class: str
    group: str
    eligible: bool
    reasons: list[str]
    r20: float | None = None
    r60: float | None = None
    r120: float | None = None
    downside_vol: float | None = None
    fragmentation: float | None = None
    trend_quality: float | None = None
    score: float | None = None
    median_turnover_20d: float | None = None
    correlation_to_benchmark: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RegimeResult:
    regime: Regime
    reasons: list[str]
    metrics: dict[str, float]
    hmm_shadow: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "regime": self.regime.value,
            "reasons": self.reasons,
            "metrics": self.metrics,
            "hmm_shadow": self.hmm_shadow,
        }


@dataclass
class Decision:
    as_of_date: str
    status: DecisionStatus
    reasons: list[str]
    regime: RegimeResult
    data_checks: dict[str, CheckResult]
    signals: list[Signal]
    target_weights: dict[str, float]
    strategic_target_weights: dict[str, float]
    champion_weights: dict[str, float]
    challenger_weights: dict[str, float]
    confidence: str
    estimated_turnover: float
    estimated_cost: float
    trade_plan_path: str | None = None
    operational_notes: list[str] = field(default_factory=list)
    strategy_confidence: str | None = None
    execution_readiness: str = "NOT_CHECKED"
    next_review: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of_date": self.as_of_date,
            "status": self.status.value,
            "reasons": self.reasons,
            "regime": self.regime.to_dict(),
            "data_checks": {key: value.to_dict() for key, value in self.data_checks.items()},
            "signals": [signal.to_dict() for signal in self.signals],
            "target_weights": self.target_weights,
            "strategic_target_weights": self.strategic_target_weights,
            "champion_weights": self.champion_weights,
            "challenger_weights": self.challenger_weights,
            "confidence": self.confidence,
            "estimated_turnover": self.estimated_turnover,
            "estimated_cost": self.estimated_cost,
            "trade_plan_path": self.trade_plan_path,
            "operational_notes": self.operational_notes,
            "strategy_confidence": self.strategy_confidence or self.confidence,
            "execution_readiness": self.execution_readiness,
            "next_review": self.next_review,
        }
