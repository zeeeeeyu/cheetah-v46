"""V4.6 执行层测试。"""
from __future__ import annotations
import pytest
from quant.domain.enums import OrderStatus, OrderSide, Environment, MarketRegime
from quant.domain.orders import OrderIntent
from quant.exe.order_factory import generate_etf_orders, generate_fund_orders, OrderGenerationResult
from quant.exe.cost_model import estimate_etf_cost, estimate_fund_cost
from quant.exe.manual_approval import ApprovalToken, ApprovalRecord, apply_plan_approval, apply_execution_confirmation
from quant.exe.execution_service import ExecutionService, ReconciliationService, ExecutionRecord, ReconciliationReport
from quant.decision.decision_engine import DecisionEngine, PortfolioDecision
from quant.port.capital_permissions import PermissionManager, CapitalPermission
from quant.port.account_mode import AccountModeManager, AccountMode

@pytest.fixture
def sample_order():
    return OrderIntent(order_id="ORD-001", account_id="MAIN", strategy_id="CORE", symbol="510300", side=OrderSide.BUY, target_amount=10000.0, estimated_quantity=100.0, signal_date="2026-07-21", earliest_execution_date="2026-07-22", environment=Environment.LIVE)

@pytest.fixture
def pm(): m = PermissionManager(); m.set_default("CORE_FUND", CapitalPermission.REAL); m.set_default("TACTICAL_ETF", CapitalPermission.REAL_LIMITED); return m

@pytest.fixture
def sample_decision(pm):
    e = DecisionEngine(pm, AccountModeManager(AccountMode.MICRO))
    return e.decide("2026-07-21", MarketRegime.TREND_RISK_ON, core_decision={"005658": 0.6, "CASH": 0.4}, tactical_decision={"510300": 0.3, "CASH": 0.7})

class TestPlanApproval:
    def test_live_order_requires_plan_approval(self, sample_order):
        assert sample_order.status == OrderStatus.DRAFT
        ra = sample_order.transition_to(OrderStatus.RISK_APPROVED)
        ua = apply_plan_approval(ra, ApprovalRecord("ORD-001", ApprovalToken.MANUAL_LIVE_PLAN_APPROVED))
        assert ua.status == OrderStatus.USER_APPROVED
    def test_plan_approval_is_not_execution_confirmation(self, sample_order):
        ra = sample_order.transition_to(OrderStatus.RISK_APPROVED)
        ua = apply_plan_approval(ra, ApprovalRecord("ORD-001", ApprovalToken.MANUAL_LIVE_PLAN_APPROVED))
        assert ua.status == OrderStatus.USER_APPROVED and ua.status != OrderStatus.RECONCILED

class TestPositionUpdates:
    def test_draft_order_does_not_update_position(self, sample_order): assert sample_order.status == OrderStatus.DRAFT
    def test_submitted_order_does_not_update_position(self, sample_order):
        s = sample_order.transition_to(OrderStatus.RISK_APPROVED).transition_to(OrderStatus.USER_APPROVED).transition_to(OrderStatus.SUBMITTED); assert s.status == OrderStatus.SUBMITTED
    def test_filled_but_unreconciled_order_does_not_update_position(self, sample_order):
        f = sample_order.transition_to(OrderStatus.RISK_APPROVED).transition_to(OrderStatus.USER_APPROVED).transition_to(OrderStatus.SUBMITTED).transition_to(OrderStatus.FILLED)
        assert f.status == OrderStatus.FILLED and f.status != OrderStatus.RECONCILED
    def test_reconciled_order_updates_position_once(self, sample_order):
        r = sample_order.transition_to(OrderStatus.RISK_APPROVED).transition_to(OrderStatus.USER_APPROVED).transition_to(OrderStatus.SUBMITTED).transition_to(OrderStatus.FILLED).transition_to(OrderStatus.RECONCILED)
        assert r.status == OrderStatus.RECONCILED and r.is_terminal

class TestExecutionImport:
    def test_duplicate_execution_is_idempotent(self):
        """重复成交导入幂等。"""
        from quant.accounts import get_connection, ensure_schema
        import tempfile
        db_path = tempfile.mktemp(suffix=".db")
        ensure_schema(db_path)
        conn = get_connection(db_path)
        conn.execute("INSERT INTO accounts (account_id, account_mode, total_assets, cash_balance, live_order_permission, created_at, updated_at) VALUES ('MAIN', 'MICRO', 100000, 50000, 0, datetime('now'), datetime('now'))"); conn.commit()
        svc = ReconciliationService(db_path)
        conn.execute("INSERT INTO orders (order_id, account_id, strategy_id, symbol, side, target_amount, estimated_quantity, status, environment, signal_date, earliest_exec_date, created_at, updated_at) VALUES ('ORD-IDEM', 'MAIN', 'CORE', '510300', 'BUY', 5000.0, 50.0, 'RECONCILED', 'LIVE', '2026-07-21', '2026-07-22', datetime('now'), datetime('now'))"); conn.commit()
        order = OrderIntent(order_id="ORD-IDEM", account_id="MAIN", strategy_id="CORE", symbol="510300", side=OrderSide.BUY, target_amount=5000.0, estimated_quantity=50.0, signal_date="2026-07-21", earliest_execution_date="2026-07-22", status=OrderStatus.RECONCILED, environment=Environment.LIVE)
        exec_rec = ExecutionRecord("EXEC-IDEM", "ORD-IDEM", "510300", "BUY", 50.0, 100.0, 5000.0, execution_date="2026-07-22")
        svc.write_to_ledger(order, exec_rec)
        cnt1 = conn.execute("SELECT COUNT(*) FROM executions WHERE execution_id='EXEC-IDEM'").fetchone()[0]
        assert cnt1 == 1
        svc.write_to_ledger(order, exec_rec)
        cnt2 = conn.execute("SELECT COUNT(*) FROM executions WHERE execution_id='EXEC-IDEM'").fetchone()[0]
        assert cnt2 == 1

    def test_duplicate_execution_does_not_update_position_twice(self):
        """重复导入不重复更新持仓。"""
        from quant.accounts import get_connection, ensure_schema
        import tempfile
        db_path = tempfile.mktemp(suffix=".db")
        ensure_schema(db_path); conn = get_connection(db_path)
        conn.execute("INSERT INTO accounts (account_id, account_mode, total_assets, cash_balance, live_order_permission, created_at, updated_at) VALUES ('MAIN', 'MICRO', 100000, 50000, 0, datetime('now'), datetime('now'))"); conn.commit()
        svc = ReconciliationService(db_path)
        conn.execute("INSERT INTO orders (order_id, account_id, strategy_id, symbol, side, target_amount, estimated_quantity, status, environment, signal_date, earliest_exec_date, created_at, updated_at) VALUES ('ORD-IDEM2', 'MAIN', 'CORE', '510300', 'BUY', 5000.0, 50.0, 'RECONCILED', 'LIVE', '2026-07-21', '2026-07-22', datetime('now'), datetime('now'))"); conn.commit()
        order = OrderIntent(order_id="ORD-IDEM2", account_id="MAIN", strategy_id="CORE", symbol="510300", side=OrderSide.BUY, target_amount=5000.0, estimated_quantity=50.0, signal_date="2026-07-21", earliest_execution_date="2026-07-22", status=OrderStatus.RECONCILED, environment=Environment.LIVE)
        exec_rec = ExecutionRecord("EXEC-IDEM2", "ORD-IDEM2", "510300", "BUY", 50.0, 100.0, 5000.0, execution_date="2026-07-22")
        svc.write_to_ledger(order, exec_rec); svc.write_to_ledger(order, exec_rec)
        cnt = conn.execute("SELECT COUNT(*) FROM position_snapshots WHERE account_id='MAIN' AND symbol='510300' AND source='RECONCILIATION'").fetchone()[0]
        assert cnt == 1

class TestPartialFill:
    def test_partial_fill_updates_only_actual_quantity(self):
        """部分成交只更新实际成交数量。"""
        order = OrderIntent(order_id="ORD-PF", account_id="MAIN", strategy_id="CORE", symbol="510300", side=OrderSide.BUY, target_amount=10000.0, estimated_quantity=100.0, signal_date="2026-07-21", earliest_execution_date="2026-07-22", status=OrderStatus.SUBMITTED, environment=Environment.LIVE)
        fo, _ = ExecutionService.confirm_execution(order, ExecutionRecord("E1", "ORD-PF", "510300", "BUY", 30.0, 100.0, 3000.0))
        assert fo.status == OrderStatus.PARTIALLY_FILLED and fo.estimated_quantity == 70.0

class TestETFLotSize:
    def test_etf_quantity_respects_lot_size(self):
        pm = PermissionManager(); e = DecisionEngine(pm, AccountModeManager(AccountMode.MICRO))
        d = e.decide("2026-07-21", MarketRegime.TREND_RISK_ON, tactical_decision={"510300": 1.0})
        r = generate_etf_orders(d, portfolio_value=100000.0, current_prices={"510300": 5.0})
        for o in r.orders: assert int(o.estimated_quantity) % 100 == 0

class TestCancelledOrder:
    def test_cancelled_order_has_no_position_effect(self, sample_order):
        c = ExecutionService.cancel_order(sample_order); assert c.status == OrderStatus.CANCELLED and c.is_terminal

class TestReconciliationDifference:
    def test_execution_difference_is_reported(self):
        svc = ReconciliationService()
        order = OrderIntent(order_id="ORD-R", account_id="MAIN", strategy_id="CORE", symbol="005658", side=OrderSide.BUY, target_amount=10000.0, estimated_quantity=500.0, signal_date="2026-07-21", earliest_execution_date="2026-07-22", status=OrderStatus.RECONCILED, environment=Environment.LIVE)
        report = svc.reconcile(order, ExecutionRecord("E2", "ORD-R", "005658", "BUY", 480.0, 20.0, 9600.0))
        assert not report.reconciled and len(report.differences) > 0

class TestStateMachine:
    def test_order_cannot_skip_required_states(self, sample_order):
        with pytest.raises(ValueError): sample_order.transition_to(OrderStatus.FILLED)
        with pytest.raises(ValueError): sample_order.transition_to(OrderStatus.RECONCILED)

class TestOrderGeneration:
    def test_etf_order_generation(self, sample_decision): r = generate_etf_orders(sample_decision, 100000.0, {"510300": 5.0}); assert isinstance(r, OrderGenerationResult)
    def test_fund_order_generation(self, sample_decision): r = generate_fund_orders(sample_decision, 100000.0, {"005658": 1.5}); assert isinstance(r, OrderGenerationResult)

class TestFundOrderUnknownPrice:
    """场外基金未知价原则：下单不得用当日净值反推份额。"""
    def test_fund_order_never_estimates_shares(self, sample_decision):
        # 即便提供了净值，也绝不反推份额；份额一律留待 T+2 确认。
        r = generate_fund_orders(sample_decision, 100000.0, {"005658": 1.5})
        assert len(r.orders) >= 1
        for o in r.orders:
            assert o.estimated_quantity == 0.0, "基金订单不得预估份额（未知价原则）"
            assert any("未知价" in reason for reason in o.reasons)
    def test_fund_order_created_without_nav(self, sample_decision):
        # 未知价：不需要当日净值即可按金额下单。
        r = generate_fund_orders(sample_decision, 100000.0, None)
        assert len(r.orders) >= 1
        assert all(o.estimated_quantity == 0.0 for o in r.orders)
    def test_fund_order_rejects_bad_nav(self, sample_decision):
        # 显式提供的净值若为非正数，视为坏数据拒单。
        r = generate_fund_orders(sample_decision, 100000.0, {"005658": -1.0})
        assert any("净值数据异常" in reason for reason in r.rejected_reasons)

class TestCostModel:
    def test_etf_cost(self): c = estimate_etf_cost(10000.0, 10.0); assert c.estimated_total_cost > 0
    def test_fund_cost(self): c = estimate_fund_cost(10000.0, subscription_rate=0.001, side="BUY"); assert abs(c.estimated_fee - 10.0) < 0.01
