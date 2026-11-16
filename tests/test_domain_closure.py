"""D-02 / Wave 1：领域闭环（TargetPortfolio → DecisionProposal → OrderIntent）。

验证 V4.6 三本账的"模型目标 → 决策计划 → 订单意图"领域对象能正确闭环，
且领域对象自身不可变、不触碰真实持仓账（物理隔离的第一/二道墙）。
"""

from __future__ import annotations

import pytest

from quant.domain.enums import Environment, OrderSide, OrderStatus
from quant.domain.orders import OrderIntent
from quant.domain.portfolios import TargetPortfolio
from quant.domain.proposals import DecisionProposal


def _target() -> TargetPortfolio:
    return TargetPortfolio(
        portfolio_id="TP1",
        account_id="ACC1",
        strategy_id="S1",
        as_of="2024-01-01",
        environment=Environment.PAPER,
        target_weights={"600000": 0.6, "CASH": 0.4},
    )


def _order_intent() -> OrderIntent:
    return OrderIntent(
        order_id="O1",
        account_id="ACC1",
        strategy_id="S1",
        symbol="600000",
        side=OrderSide.BUY,
        target_amount=60_000.0,
        estimated_quantity=0.0,
        signal_date="2024-01-01",
        earliest_execution_date="2024-01-02",
    )


def test_target_portfolio_total_weight():
    tp = _target()
    assert abs(tp.total_weight - 1.0) < 1e-9


def test_target_to_proposal_to_orderintent_closure():
    tp = _target()
    oi = _order_intent()
    dp = DecisionProposal(
        proposal_id="DP1",
        account_id="ACC1",
        as_of="2024-01-01",
        environment=Environment.PAPER,
        source_portfolio_id=tp.portfolio_id,
        order_intents=(oi,),
    )
    # 血缘：决策提案回溯到目标组合
    assert dp.source_portfolio_id == tp.portfolio_id
    assert dp.order_ids == ["O1"]


def test_order_intent_state_machine_immutable():
    """合法路径：DRAFT → RISK_APPROVED → USER_APPROVED → SUBMITTED，且每步返回新实例。"""
    oi = _order_intent()
    advanced = (
        oi.transition_to(OrderStatus.RISK_APPROVED)
        .transition_to(OrderStatus.USER_APPROVED)
        .transition_to(OrderStatus.SUBMITTED)
    )
    # 原实例不可变
    assert oi.status == OrderStatus.DRAFT
    assert advanced.status == OrderStatus.SUBMITTED
    assert advanced is not oi


def test_order_intent_illegal_transition_rejected():
    """跳过风控审批直接人工批准属于非法路径，必须 Fail-Closed。"""
    oi = _order_intent()
    with pytest.raises(ValueError):
        oi.transition_to(OrderStatus.USER_APPROVED)
    # 状态未被污染
    assert oi.status == OrderStatus.DRAFT


def test_domain_objects_serializable():
    tp = _target()
    restored = TargetPortfolio.from_dict(tp.to_dict())
    assert restored == tp
    # DecisionProposal / OrderIntent 可序列化为 dict（不变量：不写入任何账本）
    dp = DecisionProposal(proposal_id="DP2", account_id="ACC1", as_of="2024-01-01",
                          environment=Environment.PAPER, order_intents=(_order_intent(),))
    assert isinstance(dp.to_dict()["order_intents"], list)
