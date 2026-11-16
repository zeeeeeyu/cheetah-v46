"""V4.6 交易权限闸门测试（REQ-ASSET-01/02/03）。

背景：审计发现订单生成路径**完全不校验资产权限**，等价于
「谁能改配置，谁就能下单」。本测试锁死修复后的 Fail-Closed 行为。
"""
from __future__ import annotations

import inspect

import pytest

from quant.assets import AssetMasterLoader
from quant.decision.decision_engine import DecisionEngine
from quant.decision.risk_veto import RiskVetoEngine
from quant.domain.assets import Asset
from quant.domain.enums import AssetType, CapitalPermission, Environment, MarketRegime
from quant.exe.order_factory import generate_etf_orders, generate_fund_orders
from quant.exe.permission_gate import check_execution_permission, resolve_registry
from quant.port.account_mode import AccountMode, AccountModeManager
from quant.port.capital_permissions import PermissionManager


def _asset(symbol: str, *, execution_eligible: bool = True,
           permission: CapitalPermission = CapitalPermission.REAL) -> Asset:
    return Asset(
        symbol=symbol, name=f"测试-{symbol}", asset_type=AssetType.ETF,
        execution_eligible=execution_eligible, permission=permission,
    )


@pytest.fixture
def pm():
    m = PermissionManager()
    m.set_default("CORE_FUND", CapitalPermission.REAL)
    m.set_default("TACTICAL_ETF", CapitalPermission.REAL_LIMITED)
    return m


@pytest.fixture
def sample_decision(pm):
    e = DecisionEngine(pm, AccountModeManager(AccountMode.MICRO))
    return e.decide(
        "2026-07-21", MarketRegime.TREND_RISK_ON,
        core_decision={"005658": 0.6, "CASH": 0.4},
        tactical_decision={"510300": 0.3, "CASH": 0.7},
    )


class TestGateFailClosed:
    """闸门的 Fail-Closed 语义。"""

    def test_unknown_asset_is_rejected(self):
        """不在主数据中的标的一律拒绝 —— 这是本次修复的核心。"""
        r = check_execution_permission("999999", None)
        assert not r.allowed
        assert "无交易授权" in r.reason

    def test_execution_ineligible_is_rejected(self):
        r = check_execution_permission("510300", _asset("510300", execution_eligible=False))
        assert not r.allowed
        assert "execution_eligible=False" in r.reason

    def test_observation_only_is_rejected(self):
        r = check_execution_permission(
            "510300", _asset("510300", permission=CapitalPermission.OBSERVATION_ONLY))
        assert not r.allowed
        assert "OBSERVATION_ONLY" in r.reason

    def test_disabled_is_rejected(self):
        r = check_execution_permission(
            "510300", _asset("510300", permission=CapitalPermission.DISABLED))
        assert not r.allowed

    def test_real_is_allowed(self):
        assert check_execution_permission("510300", _asset("510300")).allowed

    def test_real_limited_is_allowed(self):
        r = check_execution_permission(
            "510300", _asset("510300", permission=CapitalPermission.REAL_LIMITED))
        assert r.allowed


class TestEnvironmentSensitivity:
    """PAPER 权限的资产不得生成 LIVE 订单。"""

    def test_paper_permission_blocked_in_live(self):
        r = check_execution_permission(
            "510300", _asset("510300", permission=CapitalPermission.PAPER),
            environment=Environment.LIVE)
        assert not r.allowed
        assert "LIVE" in r.reason

    def test_paper_permission_allowed_in_paper(self):
        r = check_execution_permission(
            "510300", _asset("510300", permission=CapitalPermission.PAPER),
            environment=Environment.PAPER)
        assert r.allowed


class TestStricterOfTwoFlags:
    """execution_eligible 与 permission 冲突时取更严格者。"""

    def test_eligible_true_but_observation_only_is_still_rejected(self):
        a = _asset("004813", execution_eligible=True,
                   permission=CapitalPermission.OBSERVATION_ONLY)
        assert not check_execution_permission("004813", a).allowed

    def test_real_master_data_conflict_is_blocked(self):
        """真实主数据中 004813 标注 execution_eligible=true 但权限为 OBSERVATION_ONLY，
        备注明确写「不生成自动订单」。闸门必须拦住它。"""
        registry = {a.symbol: a for a in AssetMasterLoader().load_all()}
        target = registry.get("004813")
        assert target is not None, "主数据应包含 004813"
        assert target.permission == CapitalPermission.OBSERVATION_ONLY
        assert not check_execution_permission("004813", target).allowed


class TestRegistryResolution:
    def test_empty_registry_does_not_fall_back_to_master(self):
        """显式传空 registry 表示「无任何授权」，不得回退到主数据。"""
        assert resolve_registry({}) == {}

    def test_none_registry_loads_master_data(self):
        reg = resolve_registry(None)
        assert "510300" in reg


class TestOrderFactoryEnforcesGate:
    """订单工厂必须真正调用闸门（此前零校验）。"""

    def test_fund_orders_reject_unauthorized_symbol(self, sample_decision):
        r = generate_fund_orders(sample_decision, 100000.0, {"005658": 1.5},
                                 asset_registry={})
        assert r.orders == []
        assert any("无交易授权" in x for x in r.rejected_reasons)

    def test_etf_orders_reject_unauthorized_symbol(self, sample_decision):
        r = generate_etf_orders(sample_decision, 100000.0, {"510300": 5.0},
                                asset_registry={})
        assert r.orders == []
        assert any("无交易授权" in x for x in r.rejected_reasons)

    def test_fund_orders_allow_authorized_symbol(self, sample_decision):
        reg = {"005658": _asset("005658")}
        r = generate_fund_orders(sample_decision, 100000.0, {"005658": 1.5},
                                 asset_registry=reg)
        assert len(r.orders) >= 1

    def test_observation_only_asset_gets_no_order(self, sample_decision):
        reg = {"005658": _asset("005658",
                                permission=CapitalPermission.OBSERVATION_ONLY)}
        r = generate_fund_orders(sample_decision, 100000.0, {"005658": 1.5},
                                 asset_registry=reg)
        assert r.orders == []
        assert any("OBSERVATION_ONLY" in x for x in r.rejected_reasons)


class TestNoBypassSwitch:
    """参照影响矩阵 S-X1：不得存在「可选的严格」。"""

    @pytest.mark.parametrize("fn", [generate_etf_orders, generate_fund_orders])
    def test_no_enforce_toggle_parameter(self, fn):
        params = set(inspect.signature(fn).parameters)
        forbidden = {"enforce", "enforce_permissions", "skip_permission_check",
                     "bypass", "check_permissions"}
        assert not (params & forbidden), f"{fn.__name__} 不得提供权限校验旁路开关"


class TestRiskVetoWiring:
    """check_observation_only 此前零调用方，现须真正接入 run_all_checks。"""

    def test_observation_only_triggers_veto(self):
        engine = RiskVetoEngine()
        vetos = engine.run_all_checks(
            proposed_weights={"004813": 0.5, "CASH": 0.5},
            asset_permissions={"004813": CapitalPermission.OBSERVATION_ONLY},
        )
        assert any(v.vetoed and "004813" in v.reason for v in vetos)

    def test_real_permission_does_not_trigger_veto(self):
        engine = RiskVetoEngine()
        vetos = engine.run_all_checks(
            proposed_weights={"005658": 0.5, "CASH": 0.5},
            asset_permissions={"005658": CapitalPermission.REAL},
        )
        assert not any(v.vetoed_by == "RISK_BUDGET" for v in vetos)

    def test_cash_is_never_permission_checked(self):
        engine = RiskVetoEngine()
        vetos = engine.run_all_checks(
            proposed_weights={"CASH": 1.0},
            asset_permissions={"CASH": CapitalPermission.OBSERVATION_ONLY},
        )
        assert not any("CASH" in v.reason for v in vetos)
