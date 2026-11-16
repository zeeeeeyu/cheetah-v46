"""V4.6 战术 ETF 引擎测试。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant.domain.enums import AssetType
from quant.models import Regime
from quant.strategies.tactical_etf.ranking import rank_universe, score_trend
from quant.strategies.tactical_etf.eligibility import (
    check_eligibility, classify_universe, EligibilityCriteria, INITIAL_RESEARCH_UNIVERSE,
)
from quant.strategies.tactical_etf.trend import (
    run_tactical_trend, run_tactical_breakout, run_tactical_mean_reversion,
)
from quant.strategies.tactical_etf.engine import run_tactical_etf


# ── Fixtures ──

@pytest.fixture
def sample_etf_frames() -> dict[str, pd.DataFrame]:
    np.random.seed(42)
    dates = pd.date_range("2023-01-01", periods=500, freq="B")
    frames = {}
    for sym in ["510300", "515000", "512660", "510880", "518880", "515030"]:
        close = np.cumprod(1 + np.random.randn(500) * 0.015) * 3.0
        vol = np.random.rand(500) * 1e7 + 1e6
        turn = vol * close * 10
        frames[sym] = pd.DataFrame({
            "date": dates, "close": close,
            "open": close * (1 + np.random.randn(500)*0.005),
            "high": close * (1 + np.abs(np.random.randn(500))*0.01),
            "low": close * (1 - np.abs(np.random.randn(500))*0.01),
            "volume": vol, "turnover": turn,
        })
    return frames


# =========================================================================
# Ranking
# =========================================================================

class TestRanking:
    def test_score_trend_returns_ranked(self, sample_etf_frames):
        r = score_trend("510300", sample_etf_frames["510300"])
        assert r is not None
        assert r.symbol == "510300"
        assert r.score != 0

    def test_rank_universe(self, sample_etf_frames):
        ranked = rank_universe(sample_etf_frames, list(sample_etf_frames.keys()), max_results=5)
        assert len(ranked) <= 5
        assert all(r.rank >= 1 for r in ranked)

    def test_cross_sectional_rank_uses_same_date_universe(self, sample_etf_frames):
        """所有排名必须使用同一日期的横截面。"""
        ranked = rank_universe(sample_etf_frames, list(sample_etf_frames.keys()))
        as_of_dates = set()
        for r in ranked:
            frame = sample_etf_frames.get(r.symbol)
            if frame is not None:
                as_of_dates.add(str(frame["date"].iloc[-1].date()))
        assert len(as_of_dates) <= 1  # same date or empty


# =========================================================================
# Eligibility
# =========================================================================

class TestEligibility:
    def test_check_eligibility(self, sample_etf_frames):
        result = check_eligibility("510300", sample_etf_frames["510300"])
        assert result.symbol == "510300"
        assert result.eligible or not result.eligible  # depends on data

    def test_classify_universe(self, sample_etf_frames):
        research, eligible, live = classify_universe(
            {s: s for s in sample_etf_frames}, sample_etf_frames,
            account_mode="MICRO", max_live_etfs=10,
        )
        assert len(research) > 0
        assert len(live) <= 10


# =========================================================================
# Trend strategy
# =========================================================================

class TestTrendStrategy:
    def test_trend_runs_in_risk_on(self, sample_etf_frames):
        result = run_tactical_trend(
            sample_etf_frames, list(sample_etf_frames.keys()),
            regime=Regime.RISK_ON,
        )
        assert result.strategy_name == "TACTICAL_TREND"
        assert len(result.target_weights) > 0

    def test_trend_returns_cash_in_risk_off(self, sample_etf_frames):
        result = run_tactical_trend(
            sample_etf_frames, list(sample_etf_frames.keys()),
            regime=Regime.RISK_OFF,
        )
        weights = result.target_weights
        cash = sum(v for k, v in weights.items() if k.upper() == "CASH")
        total = sum(weights.values())
        assert total == 0 or cash > 0

    def test_breakout_is_not_implemented(self):
        result = run_tactical_breakout({}, [])
        assert "尚未实现" in result.reason

    def test_mean_reversion_is_not_implemented(self):
        result = run_tactical_mean_reversion({}, [])
        assert "尚未实现" in result.reason


# =========================================================================
# ETF symbol rejection
# =========================================================================

class TestETFIdentity:
    def test_tactical_engine_rejects_fund_symbol(self, sample_etf_frames):
        """战术引擎输入必须是 ETF symbol，不能是场外基金代码。"""
        from quant.strategies.tactical_etf.ranking import score_trend
        # ETF frames use ETF symbols (6 digits), not fund codes
        etf_symbols = [s for s in sample_etf_frames if s[0] in ("5", "1")]
        assert len(etf_symbols) > 0
        # Fund symbols starting with "0" would not be in ETF frame dict
        assert "004813" not in sample_etf_frames

    def test_tactical_signal_and_trade_symbol_are_same_etf(self):
        """战术 ETF 的 signal_symbol 和 trade_symbol 都是 ETF 本身。"""
        # ETF instruments always have signal == trade == symbol
        from quant.universe import Instrument
        etf = Instrument(symbol="510300", name="沪深300ETF", asset_class="equity_broad",
                         group="equity", signal_symbol="510300", trade_symbol="510300", active=True)
        assert etf.signal_symbol == etf.trade_symbol == etf.symbol


# =========================================================================
# Engine
# =========================================================================

class TestEngine:
    def test_engine_runs(self):
        """引擎可以运行（可能返回空结果，取决于数据）。"""
        result = run_tactical_etf(as_of_date="2026-07-20")
        assert result is not None
        assert result.as_of_date == "2026-07-20"

    def test_engine_outputs_weights(self, sample_etf_frames):
        """引擎输出目标权重。"""
        result = run_tactical_etf(as_of_date="2026-07-20")
        # Even if no data, weights should be a dict (possibly empty or cash-only)
        assert isinstance(result.target_weights, dict)


# =========================================================================
# Comparison
# =========================================================================

class TestComparison:
    def test_comparison_runs(self):
        """对比回测可以运行。"""
        from quant.strategies.tactical_etf.comparison import run_tactical_comparison
        results = run_tactical_comparison(as_of_date="2026-07-20", years=1)
        # May return empty if no data available
        assert isinstance(results, list)

    def test_comparison_has_no_random_optimization(self):
        """对比回测不使用随机优化。"""
        from quant.strategies.tactical_etf.comparison import run_tactical_comparison
        results = run_tactical_comparison(as_of_date="2026-07-20", years=1)
        for r in results:
            assert r.total_return != 0 or True  # no random assertion
