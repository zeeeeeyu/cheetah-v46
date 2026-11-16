"""V4.6 风险预算与组合风险。"""
from __future__ import annotations
from dataclasses import dataclass, field; from typing import Any
import numpy as np; import pandas as pd
from quant.port.capital_permissions import CapitalPermission, PermissionManager, StrategyPermission
from quant.port.account_mode import AccountMode, AccountModeManager, AccountModeConstraints
from quant.domain.enums import MarketRegime

@dataclass
class RiskBudget:
    strategy_id: str; budget_weight: float; max_drawdown: float; max_volatility: float
    max_cluster_exposure: dict[str, float] = field(default_factory=dict); max_correlation: float = 0.85
    def to_dict(self) -> dict[str, Any]: return {"strategy_id": self.strategy_id, "budget_weight": self.budget_weight, "max_drawdown": self.max_drawdown, "max_volatility": self.max_volatility, "max_cluster_exposure": dict(self.max_cluster_exposure), "max_correlation": self.max_correlation}

@dataclass
class PortfolioRiskSnapshot:
    account_id: str; as_of_date: str; total_volatility: float = 0.0; downside_volatility: float = 0.0
    max_drawdown: float = 0.0; cluster_exposures: list = field(default_factory=list)
    strategy_contributions: dict[str, float] = field(default_factory=dict); cash_ratio: float = 0.0
    regime: MarketRegime | None = None; estimated_turnover: float = 0.0; estimated_cost: float = 0.0
    def to_dict(self) -> dict[str, Any]:
        return {"account_id": self.account_id, "as_of_date": self.as_of_date, "total_volatility": round(self.total_volatility, 6), "downside_volatility": round(self.downside_volatility, 6), "max_drawdown": round(self.max_drawdown, 4), "cluster_exposures": [c.to_dict() if hasattr(c, 'to_dict') else c for c in self.cluster_exposures], "strategy_contributions": dict(self.strategy_contributions), "cash_ratio": round(self.cash_ratio, 4), "regime": self.regime.value if self.regime else None, "estimated_turnover": round(self.estimated_turnover, 4), "estimated_cost": round(self.estimated_cost, 4)}

def aggregate_strategy_budgets(decisions: list[tuple[str, dict[str, float]]], permissions: dict[str, StrategyPermission]) -> dict[str, float]:
    combined: dict[str, float] = {}
    for sid, tw in decisions:
        p = permissions.get(sid)
        if p is None or not p.can_generate_orders(): continue
        br = p.max_risk_budget_pct
        for sym, w in tw.items(): combined[sym] = combined.get(sym, 0.0) + w * br
    return combined

def apply_position_limits(weights: dict[str, float], constraints: AccountModeConstraints, max_positions: int | None = None, cash: str = "CASH") -> dict[str, float]:
    result, mp = dict(weights), max_positions or constraints.max_total_positions
    cw = result.get(cash, 0.0)
    if cw < constraints.min_cash_floor:
        result[cash] = constraints.min_cash_floor
        nc = sum(v for k, v in result.items() if k != cash)
        if nc > 0:
            s = (1.0 - constraints.min_cash_floor) / nc
            for k in list(result.keys()):
                if k != cash: result[k] *= s
    for k in list(result.keys()):
        if k != cash and result[k] > constraints.max_single_weight: result[k] = constraints.max_single_weight
    total = sum(result.values())
    if total > 0: result = {k: v / total for k, v in result.items()}
    nc_items = {k: v for k, v in result.items() if k != cash}
    if len(nc_items) > mp:
        sorted_nc = sorted(nc_items.items(), key=lambda x: x[1], reverse=True)
        kept, dropped = dict(sorted_nc[:mp]), sum(v for _, v in sorted_nc[mp:])
        result = {**kept, cash: result.get(cash, 0.0) + dropped}
    return result
