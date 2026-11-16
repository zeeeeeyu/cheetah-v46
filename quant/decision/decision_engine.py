"""V4.6 组合决策引擎。

将核心基金 + 战术 ETF + 实验策略的独立建议，经冲突仲裁和风险否决后，
输出统一的 PortfolioDecision。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from quant.domain.enums import Environment, MarketRegime
from quant.domain.signals import (
    StrategyDecision as DomainStrategyDecision,
    PortfolioDecision as DomainPortfolioDecision,
    RejectedSignal,
)
from quant.port.capital_permissions import PermissionManager, CapitalPermission
from quant.port.account_mode import AccountModeManager, AccountMode
from quant.port.risk_budget import (
    RiskBudget,
    PortfolioRiskSnapshot,
    aggregate_strategy_budgets,
    apply_position_limits,
)
from quant.assets.risk_clusters import RiskClusterCalculator, PortfolioClusterReport
from quant.decision.conflict_resolver import (
    ConflictResolver, ConflictResolution, StrategyDirection,
)
from quant.decision.risk_veto import RiskVetoEngine, RiskVeto


@dataclass
class PortfolioDecision:
    """V4.6 组合级决策输出。

    由决策引擎整合所有策略和约束后生成。
    不包含具体订单金额或执行价格。
    不修改 Ledger。
    """
    as_of_date: str
    environment: Environment = Environment.LIVE
    regime_label: str = "UNKNOWN"
    target_weights: dict[str, float] = field(default_factory=dict)
    strategy_weights: dict[str, dict[str, float]] = field(default_factory=dict)
    cash_weight: float = 0.0
    estimated_turnover: float = 0.0
    estimated_cost: float = 0.0
    risk_snapshot: PortfolioRiskSnapshot | None = None
    conflict_resolution: ConflictResolution | None = None
    vetos: list[RiskVeto] = field(default_factory=list)
    rejected_signals: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    approval_required: bool = True
    has_real_order_capability: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of_date": self.as_of_date,
            "environment": self.environment.value,
            "regime_label": self.regime_label,
            "target_weights": {k: round(v, 4) for k, v in self.target_weights.items()},
            "strategy_weights": self.strategy_weights,
            "cash_weight": round(self.cash_weight, 4),
            "estimated_turnover": round(self.estimated_turnover, 4),
            "estimated_cost": round(self.estimated_cost, 4),
            "risk_snapshot": self.risk_snapshot.to_dict() if self.risk_snapshot else None,
            "conflict_resolution": self.conflict_resolution.to_dict() if self.conflict_resolution else None,
            "vetos": [v.to_dict() for v in self.vetos],
            "rejected_signals": list(self.rejected_signals),
            "reasons": list(self.reasons),
            "approval_required": self.approval_required,
            "has_real_order_capability": self.has_real_order_capability,
        }


class DecisionEngine:
    """组合决策引擎。

    流程：
    1. 收集各策略建议
    2. 冲突仲裁
    3. 风险否决
    4. 组合预算分配
    5. 输出 PortfolioDecision
    """

    def __init__(
        self,
        permission_manager: PermissionManager | None = None,
        account_mode_manager: AccountModeManager | None = None,
        conflict_resolver: ConflictResolver | None = None,
        risk_veto_engine: RiskVetoEngine | None = None,
        cluster_calculator: RiskClusterCalculator | None = None,
    ):
        self.perm_mgr = permission_manager or PermissionManager()
        self.account_mgr = account_mode_manager or AccountModeManager()
        self.resolver = conflict_resolver or ConflictResolver(self.perm_mgr, self.account_mgr)
        self.veto_engine = risk_veto_engine or RiskVetoEngine(self.perm_mgr)
        self.cluster_calc = cluster_calculator or RiskClusterCalculator()

    def decide(
        self,
        as_of_date: str,
        regime: MarketRegime,
        core_decision: dict[str, float] | None = None,
        tactical_decision: dict[str, float] | None = None,
        experimental_decision: dict[str, float] | None = None,
        current_weights: dict[str, float] | None = None,
        data_quality_ok: bool = True,
        reconciliation_ok: bool = True,
        environment: Environment = Environment.LIVE,
        core_risk_direction: str = "NEUTRAL",
        tactical_risk_direction: str = "NEUTRAL",
    ) -> PortfolioDecision:
        """执行完整决策流程。"""
        reasons: list[str] = []
        constraints = self.account_mgr.constraints
        core_decision = core_decision or {}
        tactical_decision = tactical_decision or {}
        experimental_decision = experimental_decision or {}
        current_weights = current_weights or {"CASH": 1.0}

        # ── 1. 权限过滤 ──
        strategy_decisions: list[tuple[str, dict[str, float]]] = [
            ("CORE_FUND", core_decision),
            ("TACTICAL_ETF", tactical_decision),
        ]
        allowed_decisions: list[tuple[str, dict[str, float]]] = []
        for sid, weights in strategy_decisions:
            perm = self.perm_mgr.get_or_default(sid)
            if perm.can_generate_orders():
                allowed_decisions.append((sid, weights))
                reasons.append(f"{sid}: 权限 {perm.permission.value}, 预算 {perm.max_risk_budget_pct:.0%}")
            else:
                reasons.append(f"{sid}: 权限 {perm.permission.value}, 跳过")

        # Experimental always PAPER/disallowed for real
        exp_perm = self.perm_mgr.get_or_default("EXPERIMENTAL")
        if exp_perm.can_generate_orders():
            allowed_decisions.append(("EXPERIMENTAL", experimental_decision))
        else:
            reasons.append(f"EXPERIMENTAL: 权限 {exp_perm.permission.value}, 无真实风险预算")

        # ── 2. 冲突仲裁 ──
        # Map strategy directions based on their risk direction
        core_dir = StrategyDirection(core_risk_direction.upper()) if core_risk_direction.upper() in ("BULLISH", "BEARISH", "NEUTRAL") else StrategyDirection.NEUTRAL
        tactical_dir = StrategyDirection(tactical_risk_direction.upper()) if tactical_risk_direction.upper() in ("BULLISH", "BEARISH", "NEUTRAL") else StrategyDirection.NEUTRAL

        conflict = self.resolver.resolve(regime, core_dir, tactical_dir)
        reasons.append(f"冲突仲裁: {conflict.reason}")

        # ── 3. 预算合并 ──
        perms = {sid: self.perm_mgr.get_or_default(sid) for sid, _ in allowed_decisions}
        combined = aggregate_strategy_budgets(allowed_decisions, perms)

        # ── 3b. 应用冲突约束（实际修改目标组合）──
        if not conflict.tactical_can_add:
            # 战术不能新增风险：移除战术决策中的新增资产
            for sym in list(combined.keys()):
                if sym in tactical_decision and sym not in core_decision:
                    # 给核心有仓位且战术要新增的资产，退还到现金
                    combined[sym] = 0.0
            reasons.append("冲突约束: 禁止战术新增风险，战术独有资产已移除")

        if conflict.cash_floor_override is not None:
            current_cash = combined.get("CASH", 0.0)
            if current_cash < conflict.cash_floor_override:
                combined["CASH"] = conflict.cash_floor_override
            reasons.append(f"冲突约束: 现金下限提升至 {conflict.cash_floor_override:.0%}")

        if conflict.max_tactical_weight < 1.0:
            tactical_total = sum(combined.get(s, 0.0) for s in (tactical_decision or {}))
            if tactical_total > conflict.max_tactical_weight:
                scale = conflict.max_tactical_weight / max(tactical_total, 1e-6)
                for s in (tactical_decision or {}):
                    if s in combined:
                        combined[s] = combined.get(s, 0.0) * scale
                reasons.append(f"冲突约束: 战术总权重限制在 {conflict.max_tactical_weight:.0%}")

        # ── 4. 位置约束 ──
        constrained = apply_position_limits(
            combined, constraints,
            max_positions=constraints.max_total_positions,
        )

        # ── 5. 风险否决 ──
        cluster_report = self.cluster_calc.compute(constrained)
        cluster_exposures = {e.cluster_id: e.total_weight for e in cluster_report.cluster_exposures}
        cluster_limits = {c.cluster_id: c.max_exposure for c in cluster_report.cluster_exposures}

        vetos = self.veto_engine.run_all_checks(
            data_quality_ok=data_quality_ok,
            reconciliation_ok=reconciliation_ok,
            proposed_weights=constrained,
            cluster_exposures=cluster_exposures,
            cluster_limits=cluster_limits,
            min_cash_floor=conflict.cash_floor_override or constraints.min_cash_floor,
        )

        # ── 6. 最终输出 ──
        cash_weight = constrained.get("CASH", 0.0)
        turnover = sum(abs(constrained.get(k, 0.0) - current_weights.get(k, 0.0)) for k in set(constrained) | set(current_weights)) * 0.5
        cost = turnover * 0.0015  # 默认单边成本

        risk_snapshot = PortfolioRiskSnapshot(
            account_id="MAIN",
            as_of_date=as_of_date,
            cash_ratio=cash_weight,
            cluster_exposures=cluster_report.cluster_exposures,
            regime=regime,
            estimated_turnover=turnover,
            estimated_cost=cost,
        )

        has_real = any(
            self.perm_mgr.get_or_default(sid).permission == CapitalPermission.REAL
            for sid, _ in allowed_decisions
        )

        return PortfolioDecision(
            as_of_date=as_of_date,
            environment=environment,
            regime_label=regime.value,
            target_weights=constrained,
            strategy_weights={sid: w for sid, w in allowed_decisions},
            cash_weight=cash_weight,
            estimated_turnover=turnover,
            estimated_cost=cost,
            risk_snapshot=risk_snapshot,
            conflict_resolution=conflict,
            vetos=vetos,
            reasons=reasons,
            approval_required=constraints.require_manual_approval,
            has_real_order_capability=has_real and reconciliation_ok and data_quality_ok and len(vetos) == 0,
        )
