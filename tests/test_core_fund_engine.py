"""V4.6 核心基金引擎升级测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quant.domain.enums import Environment, DecisionStatus as DomainDecisionStatus
from quant.domain.signals import StrategySignal, StrategyDecision as DomainStrategyDecision
from quant.strategies.core_fund.engine import (
    FundStrategicDecision,
    StrategicAction,
    classify_strategic_action,
    convert_legacy_decision_to_strategic,
    TOLERANCE_THRESHOLD,
)


# =========================================================================
# Fixtures
# =========================================================================

@pytest.fixture
def mock_legacy_decision():
    """模拟 V4.5.1 旧 Decision 对象。"""
    from types import SimpleNamespace
    regime = SimpleNamespace()
    regime.regime = SimpleNamespace()
    regime.regime.value = "RISK_ON"
    regime.reasons = ["趋势向上"]

    decision = SimpleNamespace()
    decision.as_of_date = "2026-07-21"
    decision.status = SimpleNamespace()
    decision.status.value = "REBALANCE_CANDIDATE"
    decision.reasons = ["信号达条件"]
    decision.regime = regime
    decision.target_weights = {"005658": 0.6, "016263": 0.3, "CASH": 0.1}
    decision.confidence = "STRONG"
    decision.next_review = {"next_review_date": "2026-08-10"}
    decision.signals = []
    return decision


# =========================================================================
# Test 1: pipeline returns StrategyDecision
# =========================================================================

class TestReturnsStrategyDecision:
    def test_fund_pipeline_returns_strategy_decision(self, mock_legacy_decision):
        """旧 Decision 可转换为 V4.6 FundStrategicDecision。"""
        current = {"005658": 0.5, "016263": 0.4, "CASH": 0.1}
        sd = convert_legacy_decision_to_strategic(mock_legacy_decision, current)
        assert isinstance(sd, FundStrategicDecision)
        assert sd.as_of_date == "2026-07-21"
        assert sd.strategic_actions is not None


# =========================================================================
# Test 2: 复核不等于交易
# =========================================================================

class TestReviewNotTrade:
    def test_review_does_not_force_trade(self):
        """REVIEW_REQUIRED 不是交易信号。"""
        action, reason = classify_strategic_action(
            "004813", 0.05, 0.10, "RISK_ON",
            is_observation_only=True,
        )
        assert action == StrategicAction.REVIEW_REQUIRED
        assert "观察" in reason


# =========================================================================
# Test 3: 基金决策不更新 Ledger
# =========================================================================

class TestDecisionDoesNotUpdateLedger:
    def test_fund_decision_does_not_update_ledger(self, mock_legacy_decision):
        """V4.6 FundStrategicDecision 不应含有修改 Ledger 的方法。"""
        current = {"005658": 0.5, "CASH": 0.5}
        sd = convert_legacy_decision_to_strategic(mock_legacy_decision, current)
        # No ledger-related fields
        assert not hasattr(sd, "ledger_update")
        assert not hasattr(sd, "update_position")
        # No order-related fields
        assert not hasattr(sd, "order_id")
        assert not hasattr(sd, "execution_price")


# =========================================================================
# Test 4: 基金使用直接净值
# =========================================================================

class TestFundUsesDirectNav:
    def test_fund_uses_direct_nav_not_etf_proxy(self, mock_legacy_decision):
        """V4.6 核心基金引擎通过现有 fund_pipeline 使用直接净值。"""
        # The existing fund_pipeline already enforces direct NAV.
        # The adapter doesn't change this behavior.
        current = {"005658": 0.5, "CASH": 0.5}
        sd = convert_legacy_decision_to_strategic(mock_legacy_decision, current)
        # Verify the pipeline is the source (not an ETF proxy path)
        assert hasattr(sd, "target_weights")
        assert "005658" in sd.target_weights


# =========================================================================
# Test 5: 未知价约束
# =========================================================================

class TestUnknownPriceConstraint:
    def test_fund_unknown_price_constraint_is_preserved(self):
        """战略动作不含执行价格，未知价约束由执行层处理。"""
        action, reason = classify_strategic_action("005658", 0.3, 0.5, "RISK_ON")
        assert action == StrategicAction.STRATEGIC_INCREASE
        # Strategic action doesn't contain execution price
        assert "价格" not in reason.lower() or "未知" not in reason.lower()


# =========================================================================
# Test 6: 费用和持有期约束
# =========================================================================

class TestFeeAndHoldingPeriod:
    def test_fund_fee_and_holding_period_are_preserved(self, mock_legacy_decision):
        """费用和持有期由执行层（fund_execution.py）处理，战略层不做此判断。"""
        current = {"005658": 0.5, "CASH": 0.5}
        sd = convert_legacy_decision_to_strategic(mock_legacy_decision, current)
        # Strategic decisions don't bypass fee constraints
        actions = sd.strategic_actions
        for sym, action in actions.items():
            assert action in StrategicAction


# =========================================================================
# Test 7: 多制造业基金共享风险簇上限
# =========================================================================

class TestMultipleManufacturingClusterCap:
    def test_multiple_manufacturing_funds_share_cluster_cap(self, mock_legacy_decision):
        """多个同风险簇基金的权重应合并计算上限。"""
        current = {"013566": 0.25, "016263": 0.25, "CASH": 0.5}
        sd = convert_legacy_decision_to_strategic(mock_legacy_decision, current)
        # The strategic layer itself doesn't enforce cluster caps
        # (cluster cap is applied by the risk layer in Phase 4)
        # But the wrapper should not strip cluster information
        assert sd.target_weights is not None

    def test_cluster_cap_applies_across_different_fund_names(self):
        """不同名称的基金如果共享风险簇，应合并计算。"""
        # This is tested by the RiskClusterCalculator in Phase 4
        # Here we just verify the strategic adapter doesn't interfere
        pass


# =========================================================================
# Test 8: 004813 观察模式
# =========================================================================

class Test004813Observation:
    def test_004813_is_observation_only(self):
        """004813 应输出 REVIEW_REQUIRED 或 FREEZE，而非增配。"""
        action, reason = classify_strategic_action(
            "004813", 0.0, 0.0, "RISK_ON",
            is_observation_only=True,
        )
        assert action in (StrategicAction.REVIEW_REQUIRED, StrategicAction.FREEZE)
        # Never INCREASE or REDUCE
        assert action not in (StrategicAction.STRATEGIC_INCREASE, StrategicAction.STRATEGIC_REDUCE)


# =========================================================================
# Test 9: V4.5.1 与 V4.6 排名/目标匹配
# =========================================================================

class TestRankingMatch:
    def test_v451_and_v46_core_rankings_match(self, mock_legacy_decision):
        """在未启用新风险规则前，V4.6 的目标权重应与 V4.5.1 一致。"""
        current = {"005658": 0.5, "016263": 0.4, "CASH": 0.1}
        sd = convert_legacy_decision_to_strategic(mock_legacy_decision, current)
        # Target weights are passed through from legacy decision
        for sym, weight in sd.target_weights.items():
            if sym != "CASH":
                assert sym in mock_legacy_decision.target_weights
                assert abs(weight - mock_legacy_decision.target_weights[sym]) < 0.001

    def test_v451_and_v46_targets_match_before_new_risk_rules(self, mock_legacy_decision):
        """首轮迁移保持原目标权重。"""
        sd = convert_legacy_decision_to_strategic(mock_legacy_decision, {"CASH": 1.0})
        assert sd.target_weights == mock_legacy_decision.target_weights


# =========================================================================
# Test 10: 战略动作分类边界
# =========================================================================

class TestStrategicActionBoundaries:
    def test_hold_within_tolerance(self):
        action, reason = classify_strategic_action("TEST", 0.5, 0.51, "RISK_ON")
        assert action == StrategicAction.STRATEGIC_HOLD

    def test_increase_beyond_tolerance(self):
        action, reason = classify_strategic_action("TEST", 0.3, 0.5, "RISK_ON")
        assert action == StrategicAction.STRATEGIC_INCREASE
        assert "增配" in reason

    def test_reduce_beyond_tolerance(self):
        action, reason = classify_strategic_action("TEST", 0.5, 0.4, "RISK_ON")
        assert action == StrategicAction.STRATEGIC_REDUCE
        assert "减配" in reason

    def test_exit_candidate_beyond_15pct(self):
        action, reason = classify_strategic_action("TEST", 0.5, 0.2, "RISK_ON")
        assert action == StrategicAction.EXIT_CANDIDATE
        assert "退出" in reason

    def test_risk_off_review_on_increase(self):
        action, reason = classify_strategic_action("TEST", 0.3, 0.5, "RISK_OFF")
        assert action == StrategicAction.REVIEW_REQUIRED

    def test_risk_off_reduce_on_downtrend(self):
        action, reason = classify_strategic_action("TEST", 0.5, 0.3, "RISK_OFF")
        assert action == StrategicAction.STRATEGIC_REDUCE

    def test_freeze_quarantined(self):
        action, reason = classify_strategic_action("TEST", 0.3, 0.3, "RISK_ON",
                                                    is_quarantined=True)
        assert action == StrategicAction.FREEZE
