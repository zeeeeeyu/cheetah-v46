"""V4.6 策略冲突仲裁。

处理四种冲突情形：
A: 战术减风险，核心仍看多
B: 战术加风险，核心仍看空
C: 双方看多
D: 双方看空

否决优先级：数据质量 > 账户账实 > 组合硬风险 > 战术减 > 核心配置 > 战术加 > 实验
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from quant.domain.enums import MarketRegime
from quant.port.capital_permissions import CapitalPermission, PermissionManager
from quant.port.account_mode import AccountModeManager


class StrategyDirection(str, Enum):
    """策略方向。"""
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


@dataclass
class VetoResult:
    """否决结果。"""
    vetoed: bool
    reason: str
    vetoed_by: str  # DATA_QUALITY / ACCOUNT_UNRECONCILED / RISK_BUDGET / CLUSTER_LIMIT / POSITION_LIMIT / CASH_FLOOR


@dataclass
class ConflictResolution:
    """冲突仲裁结果。"""
    core_allowed: bool = True
    tactical_allowed: bool = True
    experimental_allowed: bool = False
    core_review_required: bool = False
    tactical_can_reduce: bool = True
    tactical_can_add: bool = True
    max_tactical_weight: float = 0.25
    cash_floor_override: float | None = None
    new_risk_clusters_blocked: list[str] = field(default_factory=list)
    reason: str = ""
    emergency: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "core_allowed": self.core_allowed,
            "tactical_allowed": self.tactical_allowed,
            "experimental_allowed": self.experimental_allowed,
            "core_review_required": self.core_review_required,
            "tactical_can_reduce": self.tactical_can_reduce,
            "tactical_can_add": self.tactical_can_add,
            "max_tactical_weight": self.max_tactical_weight,
            "cash_floor_override": self.cash_floor_override,
            "new_risk_clusters_blocked": list(self.new_risk_clusters_blocked),
            "reason": self.reason,
            "emergency": self.emergency,
        }


class ConflictResolver:
    """策略冲突仲裁器。

    根据市场状态、策略方向和风险预算，输出组合级约束。
    """

    def __init__(
        self,
        permission_manager: PermissionManager | None = None,
        account_mode_manager: AccountModeManager | None = None,
    ):
        self.perm_mgr = permission_manager or PermissionManager()
        self.account_mgr = account_mode_manager or AccountModeManager()

    def resolve(
        self,
        regime: MarketRegime,
        core_direction: StrategyDirection,
        tactical_direction: StrategyDirection,
        core_target_clusters: list[str] | None = None,
        current_clusters: list[tuple[str, float]] | None = None,
    ) -> ConflictResolution:
        """根据市场状态和策略方向解决冲突。"""
        current_clusters = current_clusters or []
        core_clusters = core_target_clusters or []

        # ── 情形 A：战术减风险，核心仍看多 ──
        if tactical_direction == StrategyDirection.BEARISH and core_direction == StrategyDirection.BULLISH:
            return ConflictResolution(
                core_allowed=True,
                tactical_allowed=True,
                tactical_can_add=False,
                tactical_can_reduce=True,
                core_review_required=True,
                new_risk_clusters_blocked=core_clusters,
                reason=("情形A: 战术看空/核心看多 — 允许战术减仓，核心进入提前复核，"
                        "禁止新增同类高风险风险簇"),
            )

        # ── 情形 B：战术加风险，核心仍看空 ──
        if tactical_direction == StrategyDirection.BULLISH and core_direction == StrategyDirection.BEARISH:
            return ConflictResolution(
                core_allowed=True,
                tactical_allowed=True,
                tactical_can_add=True,
                tactical_can_reduce=True,
                max_tactical_weight=0.15,
                cash_floor_override=0.10,
                reason=("情形B: 战术看多/核心看空 — 只允许有限试仓，"
                        "禁止场外基金追涨，风险预算缩小"),
            )

        # ── 情形 C：双方看多 ──
        if tactical_direction == StrategyDirection.BULLISH and core_direction == StrategyDirection.BULLISH:
            return ConflictResolution(
                core_allowed=True,
                tactical_allowed=True,
                tactical_can_add=True,
                tactical_can_reduce=True,
                max_tactical_weight=0.25,
                reason="情形C: 双方看多 — 各自在权限内运行",
            )

        # ── 情形 D：双方看空 ──
        if tactical_direction == StrategyDirection.BEARISH and core_direction == StrategyDirection.BEARISH:
            return ConflictResolution(
                core_allowed=True,
                tactical_allowed=True,
                tactical_can_add=False,
                tactical_can_reduce=True,
                cash_floor_override=0.20,
                core_review_required=True,
                reason="情形D: 双方看空 — 战术优先减仓，核心低频调整，提高现金下限",
                emergency=regime in (MarketRegime.STRESS_RISK_OFF,),
            )

        # ── 默认（中性） ──
        return ConflictResolution(
            reason="无冲突 — 各策略按权限运行",
        )

    def resolve_by_regime(
        self,
        regime: MarketRegime,
        core_risk_direction: str = "NEUTRAL",
        tactical_risk_direction: str = "NEUTRAL",
    ) -> ConflictResolution:
        """基于市场状态直接推断策略方向。"""
        core_dir = self._regime_to_direction(regime, "core")
        tactical_dir = self._regime_to_direction(regime, "tactical")
        return self.resolve(regime, core_dir, tactical_dir)

    @staticmethod
    def _regime_to_direction(regime: MarketRegime, strategy: str = "core") -> StrategyDirection:
        mapping = {
            MarketRegime.TREND_RISK_ON: StrategyDirection.BULLISH,
            MarketRegime.RANGE_NEUTRAL: StrategyDirection.NEUTRAL,
            MarketRegime.RECOVERY_PROBE: StrategyDirection.NEUTRAL,
            MarketRegime.STRESS_RISK_OFF: StrategyDirection.BEARISH,
            MarketRegime.UNKNOWN: StrategyDirection.NEUTRAL,
        }
        return mapping.get(regime, StrategyDirection.NEUTRAL)
