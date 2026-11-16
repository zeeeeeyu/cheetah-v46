"""V4.6 订单生成器。

所有订单生成入口都必须先过 ``quant.exe.permission_gate`` 交易权限闸门（Fail-Closed）：
不在资产主数据中的标的、``execution_eligible=False`` 的标的、以及权限为
OBSERVATION_ONLY / DISABLED 的标的，一律拒单。闸门无绕过开关。
"""
from __future__ import annotations
from dataclasses import dataclass, field; from datetime import datetime; from typing import Any, Mapping
import pandas as pd
from quant.domain.assets import Asset
from quant.domain.enums import OrderStatus, OrderSide, Environment
from quant.domain.orders import OrderIntent
from quant.exe.cost_model import estimate_etf_cost, estimate_fund_cost
from quant.exe.permission_gate import check_execution_permission, resolve_registry

@dataclass
class OrderGenerationResult:
    orders: list[OrderIntent] = field(default_factory=list)
    rejected_reasons: list[str] = field(default_factory=list)
    skipped_symbols: list[str] = field(default_factory=list)
    total_estimated_cost: float = 0.0
    def to_dict(self) -> dict[str, Any]:
        return {"order_count": len(self.orders), "rejected_count": len(self.rejected_reasons), "skipped_count": len(self.skipped_symbols), "total_estimated_cost": round(self.total_estimated_cost, 4), "rejected_reasons": list(self.rejected_reasons)}

def _next_trading_day(as_of: str, offset: int = 1) -> str:
    dt = pd.Timestamp(as_of); days = 0
    while days < offset:
        dt += pd.Timedelta(days=1)
        if dt.weekday() < 5: days += 1
    return str(dt.date())

def generate_etf_orders(decision, portfolio_value: float, current_prices: dict[str, float] | None = None, environment: Environment = Environment.LIVE, account_id: str = "MAIN", asset_registry: Mapping[str, Asset] | None = None) -> OrderGenerationResult:
    result = OrderGenerationResult(); prices = current_prices or {}; as_of = decision.as_of_date
    registry = resolve_registry(asset_registry)
    for symbol, tw in decision.target_weights.items():
        if symbol == "CASH": continue
        # 交易权限闸门（Fail-Closed）：未知资产/仅观察/已禁用一律拒单
        gate = check_execution_permission(symbol, registry.get(symbol), environment)
        if not gate.allowed: result.rejected_reasons.append(gate.reason); continue
        price = prices.get(symbol)
        if price is None or price <= 0: result.rejected_reasons.append(f"{symbol}: 无可用价格"); continue
        target_amount = tw * portfolio_value; qty = int(target_amount / (price * 100)) * 100
        if qty <= 0: result.skipped_symbols.append(symbol); continue
        amount = qty * price; cost = estimate_etf_cost(amount, price)
        if cost.exceeds_max_ratio: result.rejected_reasons.append(f"{symbol}: 成本占比 {cost.cost_ratio:.4%} 超限"); continue
        result.orders.append(OrderIntent(order_id=f"ETF-{as_of}-{symbol}-{datetime.now().strftime('%H%M%S')}", account_id=account_id, strategy_id="TACTICAL_ETF", symbol=symbol, side=OrderSide.BUY if tw > 0 else OrderSide.SELL, target_amount=amount, estimated_quantity=float(qty), signal_date=as_of, earliest_execution_date=_next_trading_day(as_of), expiration_date=_next_trading_day(as_of, 5), estimated_cost=cost.estimated_total_cost, status=OrderStatus.DRAFT, environment=environment, reasons=(f"战术ETF {symbol}: 权重 {tw:.2%}",)))
        result.total_estimated_cost += cost.estimated_total_cost
    return result

def generate_fund_orders(decision, portfolio_value: float, current_navs: dict[str, float] | None = None, environment: Environment = Environment.LIVE, account_id: str = "MAIN", asset_registry: Mapping[str, Asset] | None = None) -> OrderGenerationResult:
    """生成场外基金订单意向（按金额下单，严格遵守「未知价」原则）。

    场外基金申购/赎回按**未知价**成交：下单当日无法得知成交净值（T+1 公布净值、
    T+2 确认份额），因此**绝不用当日净值反推份额**。基金订单一律按金额下单，
    ``estimated_quantity`` 恒为 0（份额留待确认阶段回填）。``current_navs`` 仅作
    数据质量校验（若显式提供且 <=0 视为坏数据拒单），不参与份额估算。

    注意：本函数是 V4.5.1 遗留的简化下单路径；场外基金的权威执行路径是
    ``quant/fund_orders`` 11 态状态机（含两阶段人工审批与 T+2 份额确认）。
    """
    result = OrderGenerationResult(); navs = current_navs or {}; as_of = decision.as_of_date
    registry = resolve_registry(asset_registry)
    for symbol, tw in decision.target_weights.items():
        if symbol == "CASH": continue
        # 交易权限闸门（Fail-Closed）：未知资产/仅观察/已禁用一律拒单
        gate = check_execution_permission(symbol, registry.get(symbol), environment)
        if not gate.allowed: result.rejected_reasons.append(gate.reason); continue
        nav = navs.get(symbol)
        # 未知价原则：当日净值不参与份额估算；仅当显式提供且非正时判定为坏数据拒单。
        if nav is not None and nav <= 0: result.rejected_reasons.append(f"{symbol}: 净值数据异常({nav})"); continue
        target_amount = tw * portfolio_value
        if target_amount <= 0: result.skipped_symbols.append(symbol); continue
        side = OrderSide.BUY if tw > 0 else OrderSide.SELL; cost = estimate_fund_cost(target_amount, side=side.value)
        result.orders.append(OrderIntent(order_id=f"FND-{as_of}-{symbol}-{datetime.now().strftime('%H%M%S')}", account_id=account_id, strategy_id="CORE_FUND", symbol=symbol, side=side, target_amount=target_amount, estimated_quantity=0.0, signal_date=as_of, earliest_execution_date=_next_trading_day(as_of), expiration_date=_next_trading_day(as_of, 10), estimated_cost=cost.estimated_total_cost, status=OrderStatus.DRAFT, environment=environment, reasons=(f"核心基金 {symbol}: 权重 {tw:.2%}", "未知价：份额待 T+2 确认，不预估")))
        result.total_estimated_cost += cost.estimated_total_cost
    return result
