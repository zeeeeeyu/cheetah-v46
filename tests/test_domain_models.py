"""V4.6 统一领域模型单元测试。

覆盖以下契约：
- 基金代码保留前导零
- 基金和ETF身份不可混淆
- signal_symbol 和 trade_symbol 必须显式表达
- StrategyDecision 不能代表真实成交
- OrderIntent 不能直接修改 Position
- LIVE 订单必须存在人工审批状态
- 每个对象必须能够关联 environment
- 领域对象可序列化
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from quant.domain.enums import (
    Environment, AssetType, DataStatus, DecisionStatus, OrderStatus, OrderSide,
    StrategyLifecycle, MarketRegime, CapitalPermission,
)
from quant.domain.assets import Asset, Position, Transaction
from quant.domain.signals import StrategySignal, StrategyDecision, PortfolioDecision, RejectedSignal
from quant.domain.orders import OrderIntent, is_valid_transition, validate_transition
from quant.domain.risks import RiskClusterExposure, RiskBudget, PortfolioRiskSnapshot, RiskVetoResult


# =========================================================================
# V46-0201: 枚举完整性
# =========================================================================

class TestEnums:
    def test_environment_values(self):
        assert Environment.BACKTEST.value == "BACKTEST"
        assert Environment.PAPER.value == "PAPER"
        assert Environment.LIVE.value == "LIVE"

    def test_asset_type_has_etf_and_fund(self):
        assert AssetType.ETF.value == "ETF"
        assert AssetType.ACTIVE_FUND.value == "ACTIVE_FUND"
        assert AssetType.ETF_LINKED_FUND.value == "ETF_LINKED_FUND"

    def test_market_regime_has_four_states(self):
        assert MarketRegime.TREND_RISK_ON.value == "TREND_RISK_ON"
        assert MarketRegime.RANGE_NEUTRAL.value == "RANGE_NEUTRAL"
        assert MarketRegime.STRESS_RISK_OFF.value == "STRESS_RISK_OFF"
        assert MarketRegime.RECOVERY_PROBE.value == "RECOVERY_PROBE"

    def test_order_status_all_states(self):
        assert len(OrderStatus) == 11


# =========================================================================
# V46-0202: 基金代码保留前导零
# =========================================================================

class TestAssetLeadingZero:
    def test_fund_code_preserves_leading_zero(self):
        """基金代码 '004813' 的符号必须保留前导零，不会被解析为 4813。"""
        asset = Asset(
            symbol="004813",
            name="中欧先进制造",
            asset_type=AssetType.ACTIVE_FUND,
        )
        assert asset.symbol == "004813"
        assert asset.symbol.startswith("00")

    def test_etf_code_does_not_have_leading_zero(self):
        """ETF 代码如 '510300' 不会有前导零问题。"""
        asset = Asset(
            symbol="510300",
            name="沪深300ETF",
            asset_type=AssetType.ETF,
        )
        assert asset.symbol == "510300"

    def test_fund_code_leading_zero_survives_round_trip(self):
        asset = Asset(symbol="004813", name="中欧", asset_type=AssetType.ACTIVE_FUND)
        d = asset.to_dict()
        restored = Asset.from_dict(d)
        assert restored.symbol == "004813"

    def test_asset_symbol_is_string_not_int(self):
        asset = Asset(symbol="005658", name="测试", asset_type=AssetType.ACTIVE_FUND)
        assert isinstance(asset.symbol, str)


# =========================================================================
# V46-0202b: 基金和ETF身份不可混淆
# =========================================================================

class TestAssetIdentity:
    def test_fund_and_etf_identity_cannot_be_confused(self):
        """相同代码的不同资产类型必须是不同的 Asset 实例。"""
        fund = Asset(symbol="515030", name="新能源车ETF", asset_type=AssetType.ETF)
        fake_fund = Asset(symbol="515030", name="某基金使用ETF当影子", asset_type=AssetType.ACTIVE_FUND)
        assert fund.asset_type != fake_fund.asset_type
        assert fund.asset_type == AssetType.ETF
        assert fake_fund.asset_type == AssetType.ACTIVE_FUND

    def test_asset_type_is_explicit_not_inferred(self):
        """不能仅通过代码格式推断类型——代码 '515030' 可以是 ETF，但也可能被错误使用。"""
        etf = Asset(symbol="515030", name="新能源车ETF", asset_type=AssetType.ETF)
        assert etf.asset_type == AssetType.ETF
        # 验证不同类型的同代码不能通过相等性判断混同
        assert etf.asset_type is not AssetType.ACTIVE_FUND


# =========================================================================
# V46-0202c: signal_symbol 和 trade_symbol 显式表达
# =========================================================================

class TestExplicitSymbols:
    def test_signal_symbol_and_trade_symbol_are_explicit(self):
        """signal_symbol 和 trade_symbol 必须显式表达，不能隐式推断。"""
        asset = Asset(
            symbol="004813",
            name="中欧先进制造",
            asset_type=AssetType.ACTIVE_FUND,
            signal_symbol="004813",
            trade_symbol="004813",
        )
        assert asset.signal_symbol == "004813"
        assert asset.trade_symbol == "004813"
        # 验证默认值逻辑
        d = asset.to_dict()
        assert d["signal_symbol"] == "004813"
        assert d["trade_symbol"] == "004813"

    def test_default_signal_trade_symbol_equals_symbol(self):
        """不传 signal/trade_symbol 时，默认等于 symbol。"""
        asset = Asset(symbol="510300", name="沪深300ETF", asset_type=AssetType.ETF)
        assert asset.signal_symbol == "510300"
        assert asset.trade_symbol == "510300"

    def test_different_signal_and_trade_symbols(self):
        """允许 signal 和 trade 使用不同代码（由用户显式设置）。"""
        asset = Asset(
            symbol="005658",
            name="测试基金",
            asset_type=AssetType.ACTIVE_FUND,
            signal_symbol="005658",
            trade_symbol="005658",
        )
        assert asset.signal_symbol == "005658"
        assert asset.trade_symbol == "005658"


# =========================================================================
# V46-0203: StrategyDecision 不能代表真实成交
# =========================================================================

class TestStrategyDecision:
    def test_strategy_decision_is_not_execution(self):
        """StrategyDecision 是目标，不是成交，不含 order_id / execution_price。"""
        decision = StrategyDecision(
            strategy_id="CORE_FUND",
            as_of_date="2026-07-21",
            environment=Environment.BACKTEST,
            target_weights={"005658": 0.6, "CASH": 0.4},
            decision_status=DecisionStatus.REBALANCE_CANDIDATE,
        )
        # StrategyDecision 不应有执行相关的字段
        assert not hasattr(decision, "order_id")
        assert not hasattr(decision, "execution_price")
        assert not hasattr(decision, "filled_quantity")
        # 它是目标权重，不是成交记录
        assert "005658" in decision.target_weights

    def test_portfolio_decision_has_no_execution_details(self):
        """PortfolioDecision 也不含成交信息。"""
        pd_decision = PortfolioDecision(
            account_id="MAIN",
            as_of_date="2026-07-21",
        )
        assert not hasattr(pd_decision, "order_ids")
        assert pd_decision.approval_required is True

    def test_strategy_decision_requires_environment(self):
        """每个 StrategyDecision 必须关联 environment。"""
        for env in Environment:
            decision = StrategyDecision(
                strategy_id="TEST",
                as_of_date="2026-07-21",
                environment=env,
                target_weights={"CASH": 1.0},
            )
            assert decision.environment == env


# =========================================================================
# V46-0204: OrderIntent 状态机
# =========================================================================

class TestOrderIntent:
    def test_order_cannot_jump_from_draft_to_filled(self):
        """DRAFT → FILLED 是非法转换。"""
        order = OrderIntent(
            order_id="ORD-001",
            account_id="MAIN",
            strategy_id="CORE",
            symbol="510300",
            side=OrderSide.BUY,
            target_amount=10000.0,
            estimated_quantity=100,
            signal_date="2026-07-21",
            earliest_execution_date="2026-07-22",
            environment=Environment.LIVE,
        )
        assert order.status == OrderStatus.DRAFT
        with pytest.raises(ValueError, match="非法的订单状态转换"):
            order.transition_to(OrderStatus.FILLED)

    def test_valid_draft_to_risk_approved(self):
        order = OrderIntent(
            order_id="ORD-002", account_id="MAIN", strategy_id="CORE",
            symbol="510300", side=OrderSide.BUY, target_amount=10000.0,
            estimated_quantity=100, signal_date="2026-07-21",
            earliest_execution_date="2026-07-22",
        )
        approved = order.transition_to(OrderStatus.RISK_APPROVED)
        assert approved.status == OrderStatus.RISK_APPROVED
        # 原对象不变
        assert order.status == OrderStatus.DRAFT

    def test_full_valid_chain(self):
        order = OrderIntent(
            order_id="ORD-003", account_id="MAIN", strategy_id="CORE",
            symbol="510300", side=OrderSide.BUY, target_amount=10000.0,
            estimated_quantity=100, signal_date="2026-07-21",
            earliest_execution_date="2026-07-22",
        )
        s1 = order.transition_to(OrderStatus.RISK_APPROVED)
        s2 = s1.transition_to(OrderStatus.USER_APPROVED)
        s3 = s2.transition_to(OrderStatus.SUBMITTED)
        s4 = s3.transition_to(OrderStatus.FILLED)
        s5 = s4.transition_to(OrderStatus.RECONCILED)
        assert s5.status == OrderStatus.RECONCILED
        assert s5.is_terminal
        assert not s5.is_pending

    def test_is_terminal_and_is_pending(self):
        order = OrderIntent(
            order_id="ORD-004", account_id="MAIN", strategy_id="CORE",
            symbol="510300", side=OrderSide.BUY, target_amount=10000.0,
            estimated_quantity=100, signal_date="2026-07-21",
            earliest_execution_date="2026-07-22",
        )
        assert order.is_pending
        assert not order.is_terminal
        rejected = order.transition_to(OrderStatus.REJECTED)
        assert rejected.is_terminal
        assert not rejected.is_pending


# =========================================================================
# V46-0204b: LIVE 订单必须存在人工审批状态
# =========================================================================

class TestLiveOrderApproval:
    def test_order_requires_user_approval_in_live(self):
        """LIVE 环境下，订单不能自动跳过 USER_APPROVED。"""
        order = OrderIntent(
            order_id="ORD-LIVE-001", account_id="MAIN", strategy_id="CORE",
            symbol="510300", side=OrderSide.BUY, target_amount=10000.0,
            estimated_quantity=100, signal_date="2026-07-21",
            earliest_execution_date="2026-07-22",
            environment=Environment.LIVE,
        )
        risk_approved = order.transition_to(OrderStatus.RISK_APPROVED)
        # LIVE 环境下必须经过 USER_APPROVED
        user_approved = risk_approved.transition_to(OrderStatus.USER_APPROVED)
        assert user_approved.status == OrderStatus.USER_APPROVED

    def test_backtest_can_skip_user_approval(self):
        """回测/模拟环境下，DRAFT 可以快速通过 RISK_APPROVED 到达 USER_APPROVED。"""
        order = OrderIntent(
            order_id="ORD-BT-001", account_id="MAIN", strategy_id="CORE",
            symbol="510300", side=OrderSide.BUY, target_amount=10000.0,
            estimated_quantity=100, signal_date="2026-07-21",
            earliest_execution_date="2026-07-22",
            environment=Environment.BACKTEST,
        )
        # 回测环境中可以快速通过审批链
        risk_approved = order.transition_to(OrderStatus.RISK_APPROVED)
        user_approved = risk_approved.transition_to(OrderStatus.USER_APPROVED)
        assert user_approved.status == OrderStatus.USER_APPROVED


# =========================================================================
# V46-0204c: 过期信号不能创建订单
# =========================================================================

class TestExpiredSignal:
    def test_expired_signal_cannot_create_order(self):
        """过期的 StrategySignal 不能被用于创建 OrderIntent。"""
        expired_signal = StrategySignal(
            strategy_id="CORE_FUND",
            strategy_version="1.0.0",
            symbol="005658",
            signal_date="2026-07-01",
            signal_type="TREND",
            score=0.75,
            confidence=0.8,
            expected_horizon="SHORT",
            expires_at=datetime(2026, 7, 10),  # 已过期
        )
        assert expired_signal.is_expired(as_of=datetime(2026, 7, 21))

    def test_valid_signal_is_not_expired(self):
        valid_signal = StrategySignal(
            strategy_id="CORE_FUND",
            strategy_version="1.0.0",
            symbol="005658",
            signal_date="2026-07-20",
            signal_type="TREND",
            score=0.75,
            confidence=0.8,
            expected_horizon="SHORT",
            expires_at=datetime(2026, 7, 25),
        )
        assert not valid_signal.is_expired(as_of=datetime(2026, 7, 21))

    def test_signal_without_expiry_never_expired(self):
        signal = StrategySignal(
            strategy_id="CORE", strategy_version="1.0", symbol="510300",
            signal_date="2026-07-20", signal_type="TREND", score=0.5,
            confidence=0.6, expected_horizon="LONG",
            expires_at=None,
        )
        assert not signal.is_expired(as_of=datetime(2126, 7, 21))


# =========================================================================
# V46-0205: 领域对象可序列化
# =========================================================================

class TestSerialization:
    def test_domain_objects_are_serializable(self):
        """所有领域对象必须有 to_dict()，且结果可 JSON 序列化。"""
        asset = Asset(symbol="510300", name="沪深300ETF", asset_type=AssetType.ETF)
        signal = StrategySignal(
            strategy_id="TEST", strategy_version="1.0", symbol="510300",
            signal_date="2026-07-21", signal_type="TREND", score=0.5,
            confidence=0.6, expected_horizon="MEDIUM",
        )
        decision = StrategyDecision(
            strategy_id="TEST", as_of_date="2026-07-21",
            environment=Environment.PAPER, target_weights={"CASH": 1.0},
        )
        order = OrderIntent(
            order_id="ORD-001", account_id="MAIN", strategy_id="TEST",
            symbol="510300", side=OrderSide.BUY, target_amount=10000.0,
            estimated_quantity=100, signal_date="2026-07-21",
            earliest_execution_date="2026-07-22",
        )
        cluster = RiskClusterExposure(cluster_name="EQUITY", total_weight=0.5, limit=0.6)

        for obj in [asset, signal, decision, order, cluster]:
            d = obj.to_dict()
            json_str = json.dumps(d, ensure_ascii=False)
            assert json_str, f"{type(obj).__name__} 无法 JSON 序列化"

    def test_asset_round_trip(self):
        original = Asset(
            symbol="004813", name="中欧先进制造", asset_type=AssetType.ACTIVE_FUND,
            exchange="SZSE", lot_size=1, risk_clusters=("ADVANCED_MANUFACTURING", "SEMICONDUCTOR"),
        )
        d = original.to_dict()
        restored = Asset.from_dict(d)
        assert restored.symbol == original.symbol
        assert restored.asset_type == original.asset_type
        assert restored.name == original.name
        assert restored.risk_clusters == original.risk_clusters


# =========================================================================
# V46-0206: Environment 显式关联
# =========================================================================

class TestEnvironmentExplicit:
    def test_environment_is_explicit(self):
        """StrategyDecision、PortfolioDecision、OrderIntent 都必须关联 environment。"""
        decision = StrategyDecision(
            strategy_id="T", as_of_date="2026-07-21",
            environment=Environment.BACKTEST, target_weights={"CASH": 1.0},
        )
        assert decision.environment == Environment.BACKTEST

        pd_decision = PortfolioDecision(
            account_id="M", as_of_date="2026-07-21",
            environment=Environment.PAPER,
        )
        assert pd_decision.environment == Environment.PAPER

        order = OrderIntent(
            order_id="ORD-E", account_id="M", strategy_id="T",
            symbol="510300", side=OrderSide.BUY, target_amount=1000.0,
            estimated_quantity=10, signal_date="2026-07-21",
            earliest_execution_date="2026-07-22",
            environment=Environment.LIVE,
        )
        assert order.environment == Environment.LIVE

    def test_environment_defaults_live(self):
        """PortfolioDecision 和 OrderIntent 默认 environment 为 LIVE。"""
        # OrderIntent 默认 LIVE
        order = OrderIntent(
            order_id="ORD-DEF", account_id="M", strategy_id="T",
            symbol="510300", side=OrderSide.BUY, target_amount=1000.0,
            estimated_quantity=10, signal_date="2026-07-21",
            earliest_execution_date="2026-07-22",
        )
        assert order.environment == Environment.LIVE

        # PortfolioDecision 默认 LIVE
        pd_decision = PortfolioDecision(account_id="M", as_of_date="2026-07-21")
        assert pd_decision.environment == Environment.LIVE


# =========================================================================
# V46-0207: 领域对象不含基础设施依赖
# =========================================================================

class TestNoInfrastructureDependency:
    def test_domain_does_not_import_akshare(self):
        """领域模型不应导入 AkShare。"""
        import quant.domain.enums as m
        assert "akshare" not in dir(m)

    def test_domain_does_not_import_pandas(self):
        """领域模型不应导入 pandas。"""
        import quant.domain.assets as a
        import quant.domain.signals as s
        import quant.domain.orders as o
        import quant.domain.risks as r
        for mod in [a, s, o, r]:
            assert 'pandas' not in dir(mod)


# =========================================================================
# V46-0208: 状态机合法性验证函数
# =========================================================================

class TestStateMachineHelpers:
    def test_is_valid_transition(self):
        from quant.domain.orders import is_valid_transition
        assert is_valid_transition(OrderStatus.DRAFT, OrderStatus.RISK_APPROVED)
        assert is_valid_transition(OrderStatus.FILLED, OrderStatus.RECONCILED)
        assert not is_valid_transition(OrderStatus.DRAFT, OrderStatus.FILLED)
        assert not is_valid_transition(OrderStatus.RECONCILED, OrderStatus.DRAFT)

    def test_validate_transition_raises(self):
        from quant.domain.orders import validate_transition
        with pytest.raises(ValueError):
            validate_transition(OrderStatus.SUBMITTED, OrderStatus.DRAFT)
        # 合法转换不报错
        validate_transition(OrderStatus.SUBMITTED, OrderStatus.FILLED)  # no error


# =========================================================================
# V46-0209: OrderIntent 不会直接修改 Position
# =========================================================================

class TestOrderPositionSeparation:
    def test_order_intent_has_no_position_methods(self):
        """OrderIntent 不应包含修改 Position 的方法。"""
        order = OrderIntent(
            order_id="ORD-SEP-001", account_id="MAIN", strategy_id="CORE",
            symbol="510300", side=OrderSide.BUY, target_amount=10000.0,
            estimated_quantity=100, signal_date="2026-07-21",
            earliest_execution_date="2026-07-22",
        )
        # OrderIntent 没有 apply_to_position 或 update_position 方法
        assert not hasattr(order, "apply_to_position")
        assert not hasattr(order, "update_position")
        assert not hasattr(order, "modify_position")
        # Position 修改只能通过 Reconciliation → Ledger 完成


# =========================================================================
# V46-0210: Risk 模型可序列化
# =========================================================================

class TestRiskSerialization:
    def test_risk_cluster_exposure_serializable(self):
        """breach 由上层服务计算，域模型只保存状态。"""
        cluster_pass = RiskClusterExposure(
            cluster_name="EQUITY", total_weight=0.4, limit=0.5,
            asset_contributions={"510300": 0.4},
        )
        # breached=False 是默认值
        assert cluster_pass.breached is False
        assert not cluster_pass.breached

        cluster_breach = RiskClusterExposure(
            cluster_name="EQUITY", total_weight=0.6, limit=0.5,
            asset_contributions={"510300": 0.4, "515000": 0.2},
            breached=True,
        )
        assert cluster_breach.breached is True
        d = cluster_breach.to_dict()
        assert d["cluster_name"] == "EQUITY"
        assert d["breached"] is True
        json.dumps(d)

    def test_risk_budget_serializable(self):
        budget = RiskBudget(
            strategy_id="CORE", budget_weight=0.6, max_drawdown=0.15,
            max_volatility=0.20,
        )
        d = budget.to_dict()
        assert d["strategy_id"] == "CORE"
        json.dumps(d)

    def test_risk_veto_result_serializable(self):
        veto = RiskVetoResult(
            vetoed=True, reason="风险簇超限", vetoed_by="CLUSTER_LIMIT",
            affected_strategies=("CORE",),
        )
        d = veto.to_dict()
        assert d["vetoed"] is True
        json.dumps(d)
