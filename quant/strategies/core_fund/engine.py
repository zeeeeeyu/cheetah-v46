"""V4.6 核心基金引擎 — 战略动作映射与 StrategyDecision 适配。

将 V4.5.1 fund_pipeline 的输出包装为 V4.6 统一领域模型。
不修改现有 fund_pipeline.py 的核心逻辑。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

import pandas as pd

from quant.domain.enums import Environment, DecisionStatus as DomainDecisionStatus
from quant.domain.signals import StrategySignal, StrategyDecision as DomainStrategyDecision
from quant.config import project_path


# ── 战略动作枚举 ──────────────────────────────────────────────

class StrategicAction(str, Enum):
    """核心基金策略输出仅限于以下战略动作。

    不得直接输出"买入"或"卖出"订单。
    """
    STRATEGIC_HOLD = "STRATEGIC_HOLD"           # 维持当前配置
    STRATEGIC_INCREASE = "STRATEGIC_INCREASE"     # 建议逐步增配
    STRATEGIC_REDUCE = "STRATEGIC_REDUCE"         # 建议逐步减配
    FREEZE = "FREEZE"                             # 冻结（不申购，不赎回）
    REVIEW_REQUIRED = "REVIEW_REQUIRED"           # 需要人工复核
    EXIT_CANDIDATE = "EXIT_CANDIDATE"             # 退出候选


@dataclass
class FundStrategicDecision:
    """核心基金引擎的战略决策输出。

    包含每个资产的战略动作、建议优先级、触发原因和风险提示。
    不包含具体订单金额或执行价格。
    """
    as_of_date: str
    environment: Environment
    regime_label: str
    strategic_actions: dict[str, StrategicAction]  # {symbol: action}
    target_weights: dict[str, float]               # 战略目标权重（仅参考，非订单）
    reasons: list[str] = field(default_factory=list)
    risk_cluster_warnings: list[str] = field(default_factory=list)
    next_review_date: str | None = None
    strategy_confidence: str = "NEUTRAL"

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of_date": self.as_of_date,
            "environment": self.environment.value,
            "regime_label": self.regime_label,
            "strategic_actions": {k: v.value for k, v in self.strategic_actions.items()},
            "target_weights": dict(self.target_weights),
            "reasons": list(self.reasons),
            "risk_cluster_warnings": list(self.risk_cluster_warnings),
            "next_review_date": self.next_review_date,
            "strategy_confidence": self.strategy_confidence,
        }


# ── 目标权重 → 战略动作映射 ──────────────────────────────

TOLERANCE_THRESHOLD = 0.03  # 3% 以内视为 HOLD


def classify_strategic_action(
    symbol: str,
    current_weight: float,
    target_weight: float,
    regime: str,
    is_quarantined: bool = False,
    is_observation_only: bool = False,
) -> tuple[StrategicAction, str]:
    """根据当前权重 vs 目标权重，结合市场状态，输出战略动作。

    核心原则：
    - 不直接输出"买入"或"卖出"
    - 战略动作需要执行层进一步处理
    - 004813 等 OBSERVATION_ONLY 资产始终 FREEZE
    """
    if is_observation_only:
        return StrategicAction.REVIEW_REQUIRED, "观察中资产，不生成自动调整"

    if is_quarantined:
        return StrategicAction.FREEZE, "数据异常隔离，持仓冻结"

    diff = target_weight - current_weight

    if abs(diff) <= TOLERANCE_THRESHOLD:
        return StrategicAction.STRATEGIC_HOLD, "偏离在容忍带内，维持当前配置"

    if diff > TOLERANCE_THRESHOLD:
        if regime in ("RISK_OFF", "STRESS_RISK_OFF"):
            return StrategicAction.REVIEW_REQUIRED, "风险状态下建议增配，需要人工复核确认"
        return StrategicAction.STRATEGIC_INCREASE, f"目标超出当前 {diff:.1%}，建议逐步增配"

    if diff < -TOLERANCE_THRESHOLD:
        if regime in ("RISK_OFF", "STRESS_RISK_OFF"):
            return StrategicAction.STRATEGIC_REDUCE, "风险状态下触发减配信号"
        if abs(diff) > 0.15:
            return StrategicAction.EXIT_CANDIDATE, f"偏离超过15%，退出候选"
        return StrategicAction.STRATEGIC_REDUCE, f"目标低于当前 {abs(diff):.1%}，建议逐步减配"


def convert_legacy_decision_to_strategic(
    legacy_decision: Any,          # 旧 Decision 对象
    current_weights: dict[str, float],
    observation_symbols: set[str] | None = None,
    quarantined_symbols: set[str] | None = None,
) -> FundStrategicDecision:
    """将旧 fund_pipeline 的 Decision 转换为 V4.6 战略决策。"""
    obs = observation_symbols or set()
    quar = quarantined_symbols or set()
    target_weights = legacy_decision.target_weights
    regime_label = legacy_decision.regime.regime.value if hasattr(legacy_decision.regime, 'regime') else str(legacy_decision.regime)

    actions: dict[str, StrategicAction] = {}
    action_reasons: list[str] = []
    all_symbols = set(current_weights) | set(target_weights)

    for symbol in all_symbols:
        current = current_weights.get(symbol, 0.0)
        target = target_weights.get(symbol, 0.0)
        if symbol == "CASH":
            continue
        action, reason = classify_strategic_action(
            symbol, current, target, regime_label,
            is_quarantined=symbol in quar,
            is_observation_only=symbol in obs,
        )
        actions[symbol] = action
        action_reasons.append(f"{symbol}: {reason}")

    return FundStrategicDecision(
        as_of_date=legacy_decision.as_of_date,
        environment=Environment.LIVE,
        regime_label=regime_label,
        strategic_actions=actions,
        target_weights=target_weights,
        reasons=legacy_decision.reasons + action_reasons,
        next_review_date=str(legacy_decision.next_review.get("next_review_date", "")) if hasattr(legacy_decision, 'next_review') else None,
        strategy_confidence=legacy_decision.confidence if hasattr(legacy_decision, 'confidence') else "NEUTRAL",
    )
