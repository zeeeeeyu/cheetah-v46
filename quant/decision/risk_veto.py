"""V4.6 风险否决引擎。

否决优先级：
数据质量 > 账户账实状态 > 组合硬风险 > 战术减风险 > 核心配置 > 战术加风险 > 实验策略
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from quant.domain.enums import MarketRegime
from quant.port.capital_permissions import CapitalPermission, PermissionManager
from quant.port.account_mode import AccountModeManager, AccountModeConstraints
from quant.port.risk_budget import PortfolioRiskSnapshot


@dataclass
class RiskVeto:
    """单次风险否决结果。"""
    vetoed: bool
    reason: str
    vetoed_by: str  # DATA_QUALITY / ACCOUNT_MISMATCH / RISK_BUDGET / CLUSTER_LIMIT / POSITION_LIMIT / CASH_FLOOR
    affected_strategies: list[str] = field(default_factory=list)
    suggested_action: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "vetoed": self.vetoed,
            "reason": self.reason,
            "vetoed_by": self.vetoed_by,
            "affected_strategies": list(self.affected_strategies),
            "suggested_action": self.suggested_action,
        }


class RiskVetoEngine:
    """风险否决引擎，按优先级逐级检查。"""

    # 否决优先级（越高越优先）
    PRIORITY_DATA_QUALITY = 1
    PRIORITY_ACCOUNT = 2
    PRIORITY_HARD_RISK = 3
    PRIORITY_TACTICAL_REDUCE = 4
    PRIORITY_CORE = 5
    PRIORITY_TACTICAL_ADD = 6
    PRIORITY_EXPERIMENTAL = 7

    def __init__(
        self,
        permission_manager: PermissionManager | None = None,
    ):
        self.perm_mgr = permission_manager or PermissionManager()

    def check_data_quality(
        self, data_quality_ok: bool, failed_symbols: list[str],
    ) -> RiskVeto | None:
        """数据质量否决（最高优先级）。"""
        if not data_quality_ok:
            return RiskVeto(
                vetoed=True,
                reason=f"数据质量阻断: {', '.join(failed_symbols)}",
                vetoed_by="DATA_QUALITY",
                affected_strategies=["ALL"],
                suggested_action="修复数据源后重试",
            )
        return None

    def check_account_reconciled(
        self, reconciliation_ok: bool,
    ) -> RiskVeto | None:
        """账户账实否决。"""
        if not reconciliation_ok:
            return RiskVeto(
                vetoed=True,
                reason="账户未完成对账，LIVE 订单被阻断",
                vetoed_by="ACCOUNT_MISMATCH",
                affected_strategies=["ALL"],
                suggested_action="运行 reconcile_account 完成人工核对",
            )
        return None

    def check_observation_only(
        self, symbol: str, permission: CapitalPermission,
    ) -> RiskVeto | None:
        """观察权限否决。"""
        if permission == CapitalPermission.OBSERVATION_ONLY:
            return RiskVeto(
                vetoed=True,
                reason=f"{symbol} 为观察权限，不能生成订单",
                vetoed_by="RISK_BUDGET",
                affected_strategies=["CORE_FUND"],
                suggested_action="保持观察模式",
            )
        return None

    def check_cluster_limit(
        self,
        proposed_weights: dict[str, float],
        cluster_exposures: dict[str, float],
        cluster_limits: dict[str, float],
        strategy_id: str = "ALL",
    ) -> RiskVeto | None:
        """风险簇上限否决。

        cluster_exposures 已经由 RiskClusterCalculator 从完整目标组合一次性聚合。
        不需要再对单个资产做二次累加。
        """
        for cluster, limit in cluster_limits.items():
            exposure = cluster_exposures.get(cluster, 0.0)
            if exposure > limit + 1e-6:
                return RiskVeto(
                    vetoed=True,
                    reason=f"风险簇 {cluster} 目标暴露 {exposure:.1%} 超过上限 {limit:.0%}",
                    vetoed_by="CLUSTER_LIMIT",
                    affected_strategies=[strategy_id],
                    suggested_action=f"减少 {cluster} 簇的持仓",
                )
        return None

    def check_cash_floor(
        self, proposed_weights: dict[str, float],
        current_cash: float, min_cash_floor: float,
        cash_symbol: str = "CASH",
    ) -> RiskVeto | None:
        """现金下限否决。"""
        new_cash = proposed_weights.get(cash_symbol, 0.0)
        effective_cash = new_cash
        if effective_cash < min_cash_floor - 1e-6:
            return RiskVeto(
                vetoed=True,
                reason=f"调整后现金比例 {effective_cash:.1%} 低于下限 {min_cash_floor:.0%}",
                vetoed_by="CASH_FLOOR",
                suggested_action=f"增加现金至 {min_cash_floor:.0%} 以上",
            )
        return None

    def run_all_checks(
        self,
        data_quality_ok: bool = True,
        failed_symbols: list[str] | None = None,
        reconciliation_ok: bool = True,
        proposed_weights: dict[str, float] | None = None,
        cluster_exposures: dict[str, float] | None = None,
        cluster_limits: dict[str, float] | None = None,
        min_cash_floor: float = 0.0,
        strategy_id: str = "ALL",
        asset_permissions: dict[str, CapitalPermission] | None = None,
    ) -> list[RiskVeto]:
        """按优先级运行全部否决检查。

        ``asset_permissions`` 提供 ``{symbol: CapitalPermission}``，用于对目标权重中
        的每个标的执行观察权限否决（REQ-ASSET-03）。未提供时跳过该项检查 —— 注意这
        **不是** Fail-Open 兜底：订单生成侧的 ``quant.exe.permission_gate`` 是强制闸
        门且不可绕过，本处是决策层的前置提示，二者互为纵深防御。
        """
        vetos: list[RiskVeto] = []
        checks = [
            (self.check_data_quality(data_quality_ok, failed_symbols or []), self.PRIORITY_DATA_QUALITY),
            (self.check_account_reconciled(reconciliation_ok), self.PRIORITY_ACCOUNT),
        ]
        # 观察权限否决：逐个标的检查（此前该检查已实现但零调用方）
        if asset_permissions:
            for symbol in (proposed_weights or {}):
                if symbol == "CASH":
                    continue
                perm = asset_permissions.get(symbol)
                if perm is None:
                    continue
                checks.append((
                    self.check_observation_only(symbol, perm),
                    self.PRIORITY_HARD_RISK,
                ))
        if proposed_weights and cluster_exposures is not None and cluster_limits:
            checks.append((
                self.check_cluster_limit(proposed_weights, cluster_exposures, cluster_limits, strategy_id),
                self.PRIORITY_HARD_RISK,
            ))
        checks.append((
            self.check_cash_floor(proposed_weights or {}, 0.0, min_cash_floor),
            self.PRIORITY_HARD_RISK,
        ))

        for veto, _ in checks:
            if veto is not None:
                vetos.append(veto)
                if veto.vetoed_by in ("DATA_QUALITY", "ACCOUNT_MISMATCH"):
                    break  # 高优先级否决阻断后续

        return vetos
