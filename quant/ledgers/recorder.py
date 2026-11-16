"""W2-2：把策略产出记入模型目标账（第一本账）。

背景（`docs/audit/V46_PRODUCTION_WIRING_AUDIT.md`）：三本账的物理隔离守卫是真的，
但生产中只有第三本（真实持仓账）被写入——`model_targets` 与 `decision_plans` 恒为空表。
「三本账并行记账」这一能力实际只兑现了三分之一。

本模块负责第一本账通电：把每日策略输出（`quant.models.Decision` 或战术 ETF 决策）
转换为不可变的 `TargetPortfolio` 并落账。

边界（必须守住，否则三本账隔离形同虚设）：
- 只写「想要什么」（权重）。绝不写 quantity / filled / order_id / 成交价。
- 写入模型目标账**永远不改变真实持仓**——由 `tests/test_ledger_isolation.py` 负向测试锁死。
- 来源固定为 ``STRATEGY``，经 `guards.assert_write_allowed` 白名单校验。
- 落账**不参与决策**：它是审计记录，不回流影响任何权重。

幂等：``portfolio_id`` 为 ``{strategy_id}-{environment}-{as_of}``，同日重跑覆盖而非堆积。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

from ..domain.enums import Environment, TrustLevel
from ..domain.orders import OrderIntent
from ..domain.portfolios import TargetPortfolio
from ..domain.proposals import DecisionProposal
from .decision_plans import write_decision_proposal
from .model_targets import write_target_portfolio

CASH_KEYS = {"CASH", "cash", "现金"}


def build_target_portfolio(
    *,
    strategy_id: str,
    account_id: str,
    as_of: str,
    target_weights: Mapping[str, float],
    environment: Environment = Environment.PAPER,
    reasons: tuple[str, ...] = (),
    trust_level: TrustLevel = TrustLevel.L6_PROVEN,
    metadata: dict[str, Any] | None = None,
) -> TargetPortfolio:
    """由策略权重构造 TargetPortfolio，自动分离 CASH 为 cash_weight。"""
    assets: dict[str, float] = {}
    cash = 0.0
    for symbol, weight in (target_weights or {}).items():
        if symbol in CASH_KEYS:
            cash += float(weight)
        else:
            assets[symbol] = float(weight)

    return TargetPortfolio(
        portfolio_id=f"{strategy_id}-{environment.value}-{as_of}",
        account_id=account_id,
        strategy_id=strategy_id,
        as_of=as_of,
        environment=environment,
        target_weights=assets,
        cash_weight=cash,
        trust_level=trust_level,
        reasons=tuple(reasons),
        metadata=metadata or {},
    )


def record_target_portfolio(
    *,
    strategy_id: str,
    account_id: str,
    as_of: str,
    target_weights: Mapping[str, float],
    environment: Environment = Environment.PAPER,
    reasons: tuple[str, ...] = (),
    metadata: dict[str, Any] | None = None,
    db_path: Path | str | None = None,
) -> str:
    """构造并落账，返回 portfolio_id。来源固定 STRATEGY（受守卫白名单约束）。"""
    portfolio = build_target_portfolio(
        strategy_id=strategy_id,
        account_id=account_id,
        as_of=as_of,
        target_weights=target_weights,
        environment=environment,
        reasons=reasons,
        metadata=metadata,
    )
    return write_target_portfolio(portfolio, source="STRATEGY", db_path=db_path)


def record_daily_decisions(
    *,
    as_of: str,
    account_id: str,
    decisions: Mapping[str, Mapping[str, float] | None],
    environment: Environment = Environment.PAPER,
    db_path: Path | str | None = None,
) -> dict[str, str]:
    """批量记录当日各策略的目标权重。

    ``decisions``：strategy_id -> target_weights（None 或空则跳过，不写空目标）。
    返回 strategy_id -> portfolio_id 的映射，仅含实际写入的项。
    """
    written: dict[str, str] = {}
    for strategy_id, weights in decisions.items():
        if not weights:
            continue
        written[strategy_id] = record_target_portfolio(
            strategy_id=strategy_id,
            account_id=account_id,
            as_of=as_of,
            target_weights=weights,
            environment=environment,
            reasons=(f"{strategy_id} 每日运行产出",),
            db_path=db_path,
        )
    return written


# ── W2-3 / W2-4：决策计划账（第二本账）与订单链生产接线 ──────────────────
#
# W2-3：把每日各策略的「决策」记入决策计划账，打通「模型目标账 → 决策计划账」
#       的血缘（source_portfolio_id 指回 W2-2 的 TargetPortfolio.portfolio_id）。
# W2-4：决策提案经 order_factory + permission_gate 生成订单意图（DRAFT，不提交），
#       让此前零生产调用方的 order_factory 真正接入运行链路。
#
# 边界（必须守住，否则三本账隔离形同虚设）：
# - 写入决策计划账**永远不改变真实持仓**——由 tests/test_ledger_isolation.py 负向测试锁死。
# - 订单意图 = DRAFT，绝不经此路径提交；提交须走人工审批 + 执行服务。
# - 来源固定为 ``DECISION_ENGINE``，经 guards.assert_write_allowed 白名单校验。
# - 落账**不参与决策**：它是审计记录，不回流影响任何权重。


def build_decision_proposal(
    *,
    strategy_id: str,
    account_id: str,
    as_of: str,
    environment: Environment = Environment.PAPER,
    target_weights: Mapping[str, float] | None = None,
    source_portfolio_id: str | None = None,
    order_intents: tuple[OrderIntent, ...] = (),
    reasons: tuple[str, ...] = (),
    metadata: dict[str, Any] | None = None,
) -> DecisionProposal:
    """由策略决策构造不可变的 DecisionProposal（第二本账核心对象）。"""
    return DecisionProposal(
        proposal_id=f"DP-{strategy_id}-{environment.value}-{as_of}",
        account_id=account_id,
        as_of=as_of,
        environment=environment,
        source_portfolio_id=source_portfolio_id,
        order_intents=tuple(order_intents),
        approval_required=True,
        approval_status="PENDING",
        risk_metrics={},
        reasons=tuple(reasons),
        metadata=dict(metadata or {}),
    )


def _generate_orders_for(
    *,
    strategy_id: str,
    as_of: str,
    environment: Environment,
    target_weights: Mapping[str, float],
    portfolio_value: float,
    account_id: str,
    asset_registry: Mapping[str, Any] | None = None,
    current_prices: Mapping[str, float] | None = None,
    current_navs: Mapping[str, float] | None = None,
):
    """W2-4：经 order_factory + permission_gate 生成订单意图（不提交、不改动持仓）。

    此处是 order_factory 首次拥有生产调用方——它原本是零生产可达的孤岛模块。
    permission_gate 对每个标的 Fail-Closed 校验，未知/仅观察/禁用标的被拒。
    """
    from quant.exe.order_factory import generate_etf_orders, generate_fund_orders

    decision = SimpleNamespace(as_of_date=as_of, target_weights=target_weights)
    if strategy_id == "TACTICAL_ETF":
        return generate_etf_orders(
            decision, portfolio_value, current_prices=current_prices,
            environment=environment, account_id=account_id,
            asset_registry=asset_registry,
        )
    # CORE_FUND 及其它策略：按基金订单生成（未知价原则，estimated_quantity=0）
    return generate_fund_orders(
        decision, portfolio_value, current_navs=current_navs,
        environment=environment, account_id=account_id,
        asset_registry=asset_registry,
    )


def record_daily_proposals(
    *,
    as_of: str,
    account_id: str,
    decisions: Mapping[str, Mapping[str, float] | None],
    environment: Environment = Environment.PAPER,
    generate_orders: bool = False,
    portfolio_value: float | None = None,
    asset_registry: Mapping[str, Any] | None = None,
    current_prices: Mapping[str, float] | None = None,
    current_navs: Mapping[str, float] | None = None,
    db_path: Path | str | None = None,
) -> dict[str, str]:
    """批量记录当日各策略的决策提案（第二本账）。

    血缘：``source_portfolio_id = {strategy_id}-{environment}-{as_of}``，与 W2-2
    写入模型目标账的 ``TargetPortfolio.portfolio_id`` 完全一致，从而把「目标 → 决策」
    串成可追溯链路。

    ``generate_orders=True``（W2-4）时，每个提案附带经 order_factory + permission_gate
    生成的订单意图（DRAFT 状态，不提交）。不传 ``portfolio_value`` 则跳过订单生成。

    返回 strategy_id -> proposal_id 的映射，仅含实际写入的项。
    """
    written: dict[str, str] = {}
    for strategy_id, weights in decisions.items():
        if weights is None:
            continue
        source_portfolio_id = f"{strategy_id}-{environment.value}-{as_of}"
        order_intents: tuple[OrderIntent, ...] = ()
        meta: dict[str, Any] = {"target_weights": dict(weights)}
        if generate_orders and portfolio_value:
            gen = _generate_orders_for(
                strategy_id=strategy_id, as_of=as_of, environment=environment,
                target_weights=weights, portfolio_value=portfolio_value,
                account_id=account_id, asset_registry=asset_registry,
                current_prices=current_prices, current_navs=current_navs,
            )
            order_intents = tuple(gen.orders)
            meta["order_count"] = len(gen.orders)
            meta["rejected_reasons"] = list(gen.rejected_reasons)
            meta["skipped_symbols"] = list(gen.skipped_symbols)
        proposal = build_decision_proposal(
            strategy_id=strategy_id, account_id=account_id, as_of=as_of,
            environment=environment, target_weights=weights,
            source_portfolio_id=source_portfolio_id, order_intents=order_intents,
            reasons=(f"{strategy_id} 每日决策（{as_of}）",),
            metadata=meta,
        )
        written[strategy_id] = write_decision_proposal(
            proposal, source="DECISION_ENGINE", db_path=db_path
        )
    return written
