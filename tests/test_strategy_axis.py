import pandas as pd
from types import SimpleNamespace

from quant.domain.assets import Asset
from quant.domain.enums import AssetType, CapitalPermission, Environment
from quant.exe.order_factory import generate_fund_orders
from quant.models import Regime
from quant.strategies.tactical_etf.trend import run_tactical_trend


def _frame(n=120, drift=1.0, start=100.0):
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    return pd.DataFrame(
        {
            "date": idx,
            "close": [start + drift * i for i in range(n)],
            "volume": [1e6] * n,
            "turnover": [0.01] * n,
        }
    )


def _frames(symbols, bullish=True):
    return {s: _frame(drift=(1.0 if bullish else -1.0)) for s in symbols}


def test_target_weights_normalized_with_cash():
    res = run_tactical_trend(
        _frames(["A", "B"]), ["A", "B"], regime=Regime.RISK_ON,
        max_positions=1, max_single_weight=0.45,
    )
    assert abs(sum(res.target_weights.values()) - 1.0) < 1e-9
    assert "CASH" in res.target_weights


def test_risk_off_all_cash():
    res = run_tactical_trend({}, [], regime=Regime.RISK_OFF)
    assert res.target_weights == {"CASH": 1.0}


def test_selected_subset_of_pool():
    res = run_tactical_trend(
        _frames(["A", "B", "C"]), ["A", "B", "C"], regime=Regime.RISK_ON, max_positions=2
    )
    assert set(res.selected_symbols).issubset({"A", "B", "C"})


def test_concentration_cap_enforced():
    res = run_tactical_trend(
        _frames(["A", "B", "C", "D", "E"]),
        ["A", "B", "C", "D", "E"], regime=Regime.RISK_ON,
        max_positions=5, max_single_weight=0.2,
    )
    non_cash = [w for k, w in res.target_weights.items() if k != "CASH"]
    assert all(w <= 0.2 + 1e-6 for w in non_cash)


def test_fund_order_unknown_price():
    decision = SimpleNamespace(
        as_of_date="2026-07-31", target_weights={"FUND_X": 0.1, "CASH": 0.9}
    )
    registry = {
        "FUND_X": Asset(
            symbol="FUND_X", name="X", asset_type=AssetType.ETF,
            execution_eligible=True, permission=CapitalPermission.PAPER,
        )
    }
    res = generate_fund_orders(
        decision, portfolio_value=1_000_000,
        environment=Environment.PAPER, asset_registry=registry,
    )
    assert res.orders, "基金订单应被生成"
    for o in res.orders:
        assert o.estimated_quantity == 0.0
