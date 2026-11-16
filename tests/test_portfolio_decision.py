"""V4.6 组合风险预算与冲突仲裁测试。"""
from __future__ import annotations
import pytest
from quant.domain.enums import MarketRegime, Environment
from quant.port.capital_permissions import PermissionManager, CapitalPermission
from quant.port.account_mode import AccountModeManager, AccountMode
from quant.port.risk_budget import aggregate_strategy_budgets, apply_position_limits
from quant.decision.conflict_resolver import ConflictResolver, StrategyDirection
from quant.decision.risk_veto import RiskVetoEngine, RiskVeto
from quant.decision.decision_engine import DecisionEngine, PortfolioDecision

@pytest.fixture
def pm():
    m = PermissionManager(); m.set_default("CORE_FUND", CapitalPermission.REAL)
    m.set_default("TACTICAL_ETF", CapitalPermission.REAL_LIMITED)
    m.set_default("EXPERIMENTAL", CapitalPermission.PAPER)
    m.set_default("004813_WATCH", CapitalPermission.OBSERVATION_ONLY); return m
@pytest.fixture
def am(): return AccountModeManager(AccountMode.MICRO)

class TestPermissions:
    def test_defaults(self, pm):
        assert pm.get_or_default("CORE_FUND").permission == CapitalPermission.REAL
        assert pm.get_or_default("EXPERIMENTAL").permission == CapitalPermission.PAPER
        assert pm.get_or_default("004813_WATCH").permission == CapitalPermission.OBSERVATION_ONLY
    def test_observation_only_no_budget(self, pm):
        assert not pm.get_or_default("004813_WATCH").can_generate_orders()
    def test_experimental_no_real(self, pm):
        assert not pm.get_or_default("EXPERIMENTAL").is_real()

class TestAccountMode:
    def test_micro_limits(self, am):
        assert am.constraints.max_total_positions == 4; assert am.constraints.max_tactical_positions == 2
    def test_no_auto_upgrade(self, am):
        ok, _ = am.can_upgrade_to(AccountMode.RETAIL); assert not ok

class TestRiskBudget:
    def test_aggregate(self, pm):
        c = aggregate_strategy_budgets([("CORE_FUND", {"005658": 0.6})], {"CORE_FUND": pm.get_or_default("CORE_FUND")})
        assert "005658" in c
    def test_budget_not_write_ledger(self):
        from quant.port.risk_budget import PortfolioRiskSnapshot
        s = PortfolioRiskSnapshot(account_id="T", as_of_date="2026-07-21")
        assert not hasattr(s, "write_ledger")
    def test_cash_floor(self, am):
        w = apply_position_limits({"510300": 0.98, "CASH": 0.02}, am.constraints)
        assert w.get("CASH", 0) >= am.constraints.min_cash_floor - 0.01

class TestConflictResolution:
    def test_a_tactical_reduce_when_core_bullish(self):
        r = ConflictResolver().resolve(MarketRegime.TREND_RISK_ON, StrategyDirection.BULLISH, StrategyDirection.BEARISH)
        assert r.tactical_can_reduce and r.core_review_required
    def test_b_tactical_add_limited_when_core_bearish(self):
        r = ConflictResolver().resolve(MarketRegime.STRESS_RISK_OFF, StrategyDirection.BEARISH, StrategyDirection.BULLISH)
        assert r.max_tactical_weight <= 0.15
    def test_c_both_bullish(self):
        r = ConflictResolver().resolve(MarketRegime.TREND_RISK_ON, StrategyDirection.BULLISH, StrategyDirection.BULLISH)
        assert r.core_allowed and r.tactical_allowed
    def test_d_both_bearish(self):
        r = ConflictResolver().resolve(MarketRegime.STRESS_RISK_OFF, StrategyDirection.BEARISH, StrategyDirection.BEARISH)
        assert not r.tactical_can_add and r.cash_floor_override >= 0.10

class TestVeto:
    def test_data_veto_highest(self):
        v = RiskVetoEngine().run_all_checks(data_quality_ok=False, failed_symbols=["510300"], reconciliation_ok=False)
        assert v[0].vetoed_by == "DATA_QUALITY"
    def test_unreconciled_blocks(self):
        v = RiskVetoEngine().run_all_checks(data_quality_ok=True, reconciliation_ok=False)
        assert any(x.vetoed_by == "ACCOUNT_MISMATCH" for x in v)
    def test_rejected_has_reason(self):
        v = RiskVeto(vetoed=True, reason="test", vetoed_by="CLUSTER_LIMIT")
        assert v.reason == "test"

class TestEngine:
    def test_produces_decision(self, pm, am):
        d = DecisionEngine(pm, am).decide("2026-07-21", MarketRegime.TREND_RISK_ON, core_decision={"005658": 0.6, "CASH": 0.4}, tactical_decision={"510300": 0.3})
        assert isinstance(d, PortfolioDecision)
    def test_reproducible(self, pm, am):
        e = DecisionEngine(pm, am)
        assert e.decide("2026-07-21", MarketRegime.TREND_RISK_ON, core_decision={"005658": 0.6}).target_weights == e.decide("2026-07-21", MarketRegime.TREND_RISK_ON, core_decision={"005658": 0.6}).target_weights
    def test_not_write_ledger(self, pm, am):
        d = DecisionEngine(pm, am).decide("2026-07-21", MarketRegime.TREND_RISK_ON, core_decision={"CASH": 1.0})
        assert not hasattr(d, "write_ledger")
    def test_micro_max_positions(self, pm, am):
        d = DecisionEngine(pm, am).decide("2026-07-21", MarketRegime.TREND_RISK_ON, core_decision={"A": 0.2, "B": 0.2, "C": 0.2, "D": 0.2, "E": 0.2}, tactical_decision={"F": 0.5, "G": 0.5})
        nc = len([k for k in d.target_weights if k != "CASH"])
        assert nc <= am.constraints.max_total_positions
