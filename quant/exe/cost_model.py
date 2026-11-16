"""V4.6 成本模型。"""
from __future__ import annotations; from dataclasses import dataclass, field; from typing import Any

@dataclass
class CostEstimate:
    estimated_fee: float = 0.0; estimated_spread_cost: float = 0.0; estimated_slippage: float = 0.0
    estimated_total_cost: float = 0.0; cost_ratio: float = 0.0; exceeds_max_ratio: bool = False
    warnings: list[str] = field(default_factory=list)
    def to_dict(self) -> dict[str, Any]:
        return {"estimated_fee": round(self.estimated_fee, 4), "estimated_spread_cost": round(self.estimated_spread_cost, 4), "estimated_slippage": round(self.estimated_slippage, 4), "estimated_total_cost": round(self.estimated_total_cost, 4), "cost_ratio": round(self.cost_ratio, 6), "exceeds_max_ratio": self.exceeds_max_ratio, "warnings": list(self.warnings)}

def estimate_etf_cost(amount: float, price: float, commission_rate: float = 0.00025, min_commission: float = 5.0, spread_rate: float = 0.0005, slippage_rate: float = 0.001, max_cost_ratio: float = 0.01) -> CostEstimate:
    if amount <= 0: return CostEstimate()
    fee = max(amount * commission_rate, min_commission); spread_cost = amount * spread_rate; slippage = amount * slippage_rate
    total = fee + spread_cost + slippage; ratio = total / amount
    w = [f"成本占比 {ratio:.4%} 超过上限 {max_cost_ratio:.2%}"] if ratio > max_cost_ratio else []
    return CostEstimate(estimated_fee=fee, estimated_spread_cost=spread_cost, estimated_slippage=slippage, estimated_total_cost=total, cost_ratio=ratio, exceeds_max_ratio=ratio > max_cost_ratio, warnings=w)

def estimate_fund_cost(amount: float, subscription_rate: float | None = 0.001, redemption_rate: float | None = None, side: str = "BUY", max_cost_ratio: float = 0.01) -> CostEstimate:
    if amount <= 0: return CostEstimate()
    fee = amount * (subscription_rate or 0) if side == "BUY" and subscription_rate is not None else amount * (redemption_rate or 0) if side == "SELL" and redemption_rate is not None else 0.0
    ratio = fee / amount if amount > 0 else 0.0
    w = [f"成本占比 {ratio:.4%} 超过上限 {max_cost_ratio:.2%}"] if ratio > max_cost_ratio else []
    return CostEstimate(estimated_fee=fee, estimated_total_cost=fee, cost_ratio=ratio, exceeds_max_ratio=ratio > max_cost_ratio, warnings=w)
