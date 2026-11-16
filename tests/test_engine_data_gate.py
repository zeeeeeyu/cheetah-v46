"""战术 ETF 引擎数据闸门测试（GAP-2 / REQ-DATA）。

核心不变量：基准行情不可信时，引擎必须 Fail-Closed 转 RISK_OFF，
绝不能退化为 NEUTRAL（NEUTRAL 会放行正常仓位 = 在脏数据上下单）。
"""

from __future__ import annotations

import inspect

import numpy as np
import pandas as pd
import pytest

from quant.datahub import staleness_calendar_days
from quant.models import Regime
from quant.strategies.tactical_etf.eligibility import EligibilityCriteria
from quant.strategies.tactical_etf.engine import (
    build_eligibility_criteria, resolve_market_regime, run_tactical_etf,
)


BENCHMARK = "510300"


def _settings(max_staleness: int = 0, **overrides) -> dict:
    base = {
        "benchmark_symbol": BENCHMARK,
        "minimum_history_days": 126,
        "data": {
            "require_ohlcv": True,
            "max_staleness_trading_days": max_staleness,
            "min_median_turnover_20d": 100_000_000,
            "max_single_day_return_abs": 0.20,
        },
        "regime": {
            "atr_window": 20,
            "chandelier_multiple": 3.0,
            "hmm_shadow_only": True,
            "hmm_min_history_days": 252,
        },
    }
    base.update(overrides)
    return base


def _frame(end: str, periods: int = 400) -> pd.DataFrame:
    """构造一段以 `end` 为最新交易日的合法 OHLCV 序列。"""
    np.random.seed(7)
    last_business_day = pd.offsets.BDay().rollback(pd.Timestamp(end))
    dates = pd.bdate_range(end=last_business_day, periods=periods)
    close = np.cumprod(1 + np.random.randn(periods) * 0.01) * 4.0
    return pd.DataFrame({
        "date": dates,
        "close": close,
        "open": close * 0.999,
        "high": close * 1.01,
        "low": close * 0.99,
        "volume": np.full(periods, 1e7),
        "turnover": np.full(periods, 5e8),
        "source_symbol": BENCHMARK,
    })


# =========================================================================
# 缺陷 A：基准数据缺失 / 为空
# =========================================================================

class TestBenchmarkMissing:
    def test_empty_dataframe_forces_risk_off(self):
        """基准 frame 为空 DataFrame → RISK_OFF，且 reasons 说明是数据原因。"""
        regime, reasons = resolve_market_regime(
            {BENCHMARK: pd.DataFrame()}, _settings(), pd.Timestamp("2026-07-20"),
        )
        assert regime is Regime.RISK_OFF
        assert reasons, "被数据闸门降级时必须留下原因，否则无法追溯"
        assert any("缺失" in r for r in reasons)
        assert any("Fail-Closed" in r for r in reasons)

    def test_symbol_absent_from_frames_forces_risk_off(self):
        """基准 symbol 根本不在 frames 里 → 同样 RISK_OFF。"""
        regime, reasons = resolve_market_regime(
            {"515000": _frame("2026-07-20")}, _settings(), pd.Timestamp("2026-07-20"),
        )
        assert regime is Regime.RISK_OFF
        assert any(BENCHMARK in r for r in reasons)

    def test_never_returns_neutral_on_missing_data(self):
        """回归锁定：数据缺失绝不能得到 NEUTRAL（这是修复前的 Fail-Open 行为）。"""
        for frames in ({}, {BENCHMARK: pd.DataFrame()}, {BENCHMARK: None}):
            regime, _ = resolve_market_regime(frames, _settings(), pd.Timestamp("2026-07-20"))
            assert regime is not Regime.NEUTRAL
            assert regime is Regime.RISK_OFF


# =========================================================================
# 缺陷 B：数据陈旧度阈值
# =========================================================================

class TestStaleness:
    @pytest.mark.parametrize("stale_days", [1, 5, 17, 40])
    def test_stale_benchmark_forces_risk_off(self, stale_days):
        """阈值为 0 时，任何陈旧都必须 Fail-Closed，并报出陈旧天数。"""
        as_of = pd.Timestamp("2026-07-20")  # 周一
        frame = _frame(str((as_of - pd.Timedelta(days=stale_days)).date()))
        regime, reasons = resolve_market_regime(
            {BENCHMARK: frame}, _settings(max_staleness=0), as_of,
        )
        actual_stale = staleness_calendar_days(frame, as_of)
        assert regime is Regime.RISK_OFF
        assert any(str(actual_stale) in r and "陈旧" in r for r in reasons), reasons
        assert any("阈值 0" in r for r in reasons), reasons

    def test_threshold_is_actually_read_from_settings(self):
        """同一份数据，阈值放宽后应当放行——证明配置真的被读取，而非硬编码。"""
        as_of = pd.Timestamp("2026-07-20")
        frames = {BENCHMARK: _frame("2026-07-16")}  # 陈旧 4 天

        strict, strict_reasons = resolve_market_regime(frames, _settings(max_staleness=0), as_of)
        loose, loose_reasons = resolve_market_regime(frames, _settings(max_staleness=10), as_of)

        assert strict is Regime.RISK_OFF and strict_reasons
        assert not loose_reasons, "阈值内的数据不应被数据闸门拦截"

    def test_fresh_benchmark_uses_normal_regime_logic(self):
        """基准新鲜 → 不因数据原因强制 RISK_OFF，走原有 core_regime 逻辑。"""
        as_of = pd.Timestamp("2026-07-20")
        frames = {BENCHMARK: _frame("2026-07-20")}
        regime, reasons = resolve_market_regime(frames, _settings(max_staleness=0), as_of)

        assert reasons == [], "新鲜数据不应产生任何数据闸门原因"
        assert regime in {Regime.RISK_ON, Regime.NEUTRAL, Regime.RISK_OFF}

        from quant.regime import core_regime
        assert regime is core_regime(frames[BENCHMARK], _settings()["regime"]).regime


# =========================================================================
# 硬约束：不存在任何绕过开关
# =========================================================================

class TestNoBypassSwitch:
    @pytest.mark.parametrize("func", [run_tactical_etf, resolve_market_regime, build_eligibility_criteria])
    @pytest.mark.parametrize("banned", ["bypass", "skip", "enforce", "force", "ignore", "disable"])
    def test_public_entry_has_no_bypass_parameter(self, func, banned):
        """数据闸门不允许有任何可关闭的开关（参考反面教材 throwOnInvalid=false）。"""
        for name in inspect.signature(func).parameters:
            assert banned not in name.lower(), f"{func.__name__} 出现疑似绕过参数：{name}"

    def test_engine_source_has_no_bypass_keyword(self):
        """源码层面也不允许出现绕过分支或环境变量开关。"""
        import quant.strategies.tactical_etf.engine as engine_module

        source = inspect.getsource(engine_module)
        code_lines = [
            line for line in source.splitlines()
            if not line.strip().startswith("#") and "bypass / skip" not in line
        ]
        code = "\n".join(code_lines).lower()
        # 注意：不能裸匹配 "environ"——`Environment as DomainEnv` 这类合法导入会误报。
        # 这里只拦截真正的环境变量读取与绕过标识。
        for banned in ("bypass", "skip_staleness", "os.getenv", "os.environ", "enforce="):
            assert banned not in code, f"engine.py 出现疑似绕过入口：{banned}"


# =========================================================================
# 缺陷 C：EligibilityCriteria 全字段贯通
# =========================================================================

class TestEligibilityCriteriaWiring:
    def test_five_extra_fields_come_from_settings(self):
        """5 个此前失效的字段现在必须真的从 settings 生效。"""
        settings = _settings()
        settings["eligibility"] = {
            "min_fund_size": 123_000_000,
            "max_bid_ask_spread": 0.008,
            "max_premium_discount": 0.03,
            "max_lot_amount": 77_000,
            "min_data_completeness": 0.88,
        }
        criteria = build_eligibility_criteria(settings)

        assert criteria.min_fund_size == 123_000_000
        assert criteria.max_bid_ask_spread == 0.008
        assert criteria.max_premium_discount == 0.03
        assert criteria.max_lot_amount == 77_000
        assert criteria.min_data_completeness == 0.88

    def test_missing_keys_fall_back_to_dataclass_defaults(self):
        """settings 无对应键时沿用 dataclass 默认值，不得硬编码新数字。"""
        defaults = EligibilityCriteria()
        criteria = build_eligibility_criteria(_settings())

        assert criteria.min_fund_size == defaults.min_fund_size
        assert criteria.max_bid_ask_spread == defaults.max_bid_ask_spread
        assert criteria.max_premium_discount == defaults.max_premium_discount
        assert criteria.max_lot_amount == defaults.max_lot_amount
        assert criteria.min_data_completeness == defaults.min_data_completeness

    def test_all_seven_fields_are_wired(self):
        """判据对象的每个字段都必须可被配置覆盖，防止将来新增字段又被漏掉。"""
        settings = _settings()
        settings["eligibility"] = {
            "min_fund_size": 1.0, "max_bid_ask_spread": 1.0, "max_premium_discount": 1.0,
            "max_lot_amount": 1.0, "min_data_completeness": 1.0,
        }
        settings["minimum_history_days"] = 1
        settings["data"]["min_median_turnover_20d"] = 1.0

        criteria = build_eligibility_criteria(settings)
        for field_name in EligibilityCriteria.__dataclass_fields__:
            assert getattr(criteria, field_name) == 1, f"{field_name} 未从配置贯通"

    def test_known_config_divergence_is_visible(self):
        """配置分叉（126 vs 252、1亿 vs 1000万）必须可见——生效的是配置值。"""
        from quant.config import load_json

        real = load_json("config/settings.json")
        criteria = build_eligibility_criteria(real)
        defaults = EligibilityCriteria()

        assert criteria.min_history_days == int(real["minimum_history_days"])
        assert criteria.min_history_days != defaults.min_history_days
        assert criteria.min_median_turnover_20d == float(real["data"]["min_median_turnover_20d"])
        assert criteria.min_median_turnover_20d != defaults.min_median_turnover_20d


# =========================================================================
# 端到端：真实配置下的生产入口
# =========================================================================

class TestEndToEnd:
    def test_engine_surfaces_gate_reason_in_result(self, monkeypatch):
        """基准数据缺失时，run_tactical_etf 的 reasons 必须带出数据原因。"""
        import quant.strategies.tactical_etf.engine as engine_module

        class _EmptyRepo:
            def load(self, symbol):
                raise FileNotFoundError(symbol)

        monkeypatch.setattr(engine_module, "DataRepository", _EmptyRepo)
        result = run_tactical_etf(as_of_date="2026-07-20")

        assert result.regime_label == Regime.RISK_OFF.value
        assert any("Fail-Closed" in r for r in result.reasons), result.reasons
        assert any("Fail-Closed" in r for r in result.to_dict()["reasons"])

    def test_engine_with_stale_local_data_is_risk_off(self, monkeypatch, tmp_path):
        """本地合成数据截止 2026-07-03，以 07-20 运行必然陈旧。"""
        from quant.datahub import DataRepository
        from quant.strategies.tactical_etf import engine as engine_module

        repository = DataRepository(tmp_path)
        repository.save(BENCHMARK, _frame("2026-07-03"))
        monkeypatch.setattr(engine_module, "DataRepository", lambda: repository)
        result = run_tactical_etf(as_of_date="2026-07-20")
        assert result.regime_label == Regime.RISK_OFF.value
        assert any("陈旧" in r for r in result.reasons), result.reasons
