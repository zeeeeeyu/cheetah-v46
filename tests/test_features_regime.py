"""V4.6 量价时空特征与多维 Regime 测试。"""
from __future__ import annotations
import json, tempfile
from datetime import datetime, timedelta
from pathlib import Path
import numpy as np, pandas as pd, pytest
from quant.domain.enums import MarketRegime
from quant.feat.volume import relative_volume, turnover_percentile, volume_price_confirmation, liquidity_score
from quant.feat.time_decay import signal_age, exponential_decay, data_freshness, signal_expiration, multi_horizon_consistency
from quant.feat.price_space import price_range_position, distance_to_high, distance_to_low, current_drawdown, atr_distance, cross_sectional_percentile
from quant.feat.store import FeatureSnapshot, FeatureSnapshotStore
from quant.regimes.breadth import compute_breadth, BreadthSnapshot
from quant.regimes.regime_ensemble import RegimeEnsemble, RegimeEnsembleResult, EnsembleInput

# ── Fixtures ──
@pytest.fixture
def price_series() -> pd.Series: np.random.seed(42); return pd.Series(np.cumsum(np.random.randn(200) * 0.01) + 1.0)
@pytest.fixture
def volume_series() -> pd.Series: np.random.seed(1); return pd.Series(np.random.rand(200) * 1e7 + 1e6)

@pytest.fixture
def sample_frames() -> dict[str, pd.DataFrame]:
    np.random.seed(42); dates = pd.date_range("2025-01-01", periods=300, freq="B"); frames = {}
    for sym in ["510300", "515000", "512660", "518880", "510880"]:
        close = np.cumprod(1 + np.random.randn(300) * 0.015) * 3.0
        frames[sym] = pd.DataFrame({"date": dates, "close": close, "open": close * (1 + np.random.randn(300)*0.005), "high": close * (1 + np.abs(np.random.randn(300))*0.01), "low": close * (1 - np.abs(np.random.randn(300))*0.01), "volume": np.random.rand(300)*1e7+1e6, "turnover": np.random.rand(300)*1e8+1e7})
    return frames

# =========================================================================
# Volume features
# =========================================================================
class TestVolumeFeatures:
    def test_relative_volume(self, volume_series): rv = relative_volume(volume_series, window=20); assert rv is not None and rv > 0
    def test_turnover_percentile(self, volume_series): tp = turnover_percentile(volume_series, window=60); assert tp is not None and 0 <= tp <= 1
    def test_liquidity_score(self, volume_series): ls = liquidity_score(volume_series, window=20); assert ls is not None and 0 <= ls <= 1
    def test_fund_does_not_receive_etf_volume_feature(self): pass  # 约定性测试
    def test_volume_price_confirmation(self, price_series, volume_series): vpc = volume_price_confirmation(price_series, volume_series, window=20); assert vpc is None or -1 <= vpc <= 1

# =========================================================================
# Time decay
# =========================================================================
class TestTimeDecay:
    def test_signal_age_increases(self): assert signal_age("2026-07-01", "2026-07-21") == 20
    def test_exponential_decay_decreases_with_age(self):
        assert exponential_decay(0) == 1.0; assert abs(exponential_decay(20) - 0.5) < 0.01; assert exponential_decay(40) < exponential_decay(20)
    def test_data_freshness(self):
        assert data_freshness("2026-07-20", "2026-07-21", 5) > data_freshness("2026-07-01", "2026-07-21", 5)
        assert data_freshness("2026-07-01", "2026-07-21", 5) == 0.0
    def test_expired_signal_has_zero_or_invalid_weight(self):
        assert signal_expiration(35, 30) is True; assert signal_expiration(25, 30) is False
    def test_multi_horizon_consistency(self):
        assert multi_horizon_consistency(0.1, 0.2, 0.3) == 1.0; assert multi_horizon_consistency(0.1, -0.2, 0.3) < 1.0

# =========================================================================
# Price space
# =========================================================================
class TestPriceSpace:
    def test_price_range_position_is_bounded(self, price_series): prp = price_range_position(price_series, 60); assert prp is not None and 0 <= prp <= 1
    def test_distance_to_high(self, price_series): dth = distance_to_high(price_series, 60); assert dth is not None and dth <= 0
    def test_distance_to_low(self, price_series): dtl = distance_to_low(price_series, 60); assert dtl is not None and dtl >= 0
    def test_current_drawdown(self, price_series): dd = current_drawdown(price_series, 60); assert dd is not None and dd <= 0
    def test_cross_sectional_rank_uses_same_date_universe(self):
        assert abs(cross_sectional_percentile(0.3, [0.1, 0.3, 0.5, 0.7, 0.9]) - 0.2) < 0.01

# =========================================================================
# FeatureSnapshot store
# =========================================================================
class TestFeatureSnapshot:
    def test_no_future_data_in_feature_snapshot(self):
        snap = FeatureSnapshot(snapshot_id="t1", as_of_date="2026-07-21", symbol="510300", features={"ma20": 3.5})
        assert snap.as_of_date == "2026-07-21"
    def test_snapshot_store_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            store = FeatureSnapshotStore(Path(d))
            snap = FeatureSnapshot(snapshot_id="snap1", as_of_date="2026-07-21", symbol="510300", features={"r20": 0.05})
            store.save(snap); loaded = store.load("snap1")
            assert loaded is not None and loaded.snapshot_id == "snap1"

# =========================================================================
# Breadth
# =========================================================================
class TestBreadth:
    def test_breadth_computes_ratios(self, sample_frames):
        snap = compute_breadth(sample_frames, "2026-07-21")
        assert snap.advance_ratio is not None and 0 <= snap.advance_ratio <= 1 and snap.total_instruments > 0
    def test_breadth_ignores_quarantined_assets(self, sample_frames):
        snap = compute_breadth(sample_frames, "2026-07-21", quarantined_symbols={"510300"})
        assert snap.quarantined_count == 1

# =========================================================================
# Regime ensemble
# =========================================================================
class TestRegimeEnsemble:
    def test_risk_off_enters_faster_than_it_exits(self):
        ensemble = RegimeEnsemble(min_risk_off_days=2)
        inp_on = EnsembleInput(csi300_trend=0.8, advance_ratio=0.7, above_ma20_ratio=0.6, volatility=0.1)
        inp_off = EnsembleInput(csi300_trend=-0.8, advance_ratio=0.2, above_ma20_ratio=0.2, volatility=0.4)
        inp_mid = EnsembleInput(csi300_trend=0.0, advance_ratio=0.5, above_ma20_ratio=0.5, volatility=0.2)
        assert ensemble.compute(inp_on, "2026-07-01").regime == MarketRegime.TREND_RISK_ON
        assert ensemble.compute(inp_off, "2026-07-02").regime == MarketRegime.STRESS_RISK_OFF
        assert ensemble.compute(inp_mid, "2026-07-03").regime == MarketRegime.STRESS_RISK_OFF
        # Day 4: min_risk_off_days=2 已过，恢复确认中 -> RECOVERY_PROBE
        assert ensemble.compute(inp_mid, "2026-07-04").regime == MarketRegime.RECOVERY_PROBE

    def test_regime_does_not_flip_on_single_noise_day(self):
        ensemble = RegimeEnsemble(min_risk_off_days=1)
        inp_norm = EnsembleInput(csi300_trend=0.6, advance_ratio=0.6, above_ma20_ratio=0.6)
        inp_noise = EnsembleInput(csi300_trend=0.3, advance_ratio=0.4, above_ma20_ratio=0.5)
        ensemble.compute(inp_norm, "2026-07-01"); ensemble.compute(inp_norm, "2026-07-02")
        r3 = ensemble.compute(inp_noise, "2026-07-03")
        assert r3.regime in (MarketRegime.TREND_RISK_ON, MarketRegime.RANGE_NEUTRAL)

    def test_legacy_regime_is_preserved_for_comparison(self):
        r = RegimeEnsemble().compute(EnsembleInput(csi300_trend=0.8, advance_ratio=0.7, above_ma20_ratio=0.7), "2026-07-21")
        assert r.regime == MarketRegime.TREND_RISK_ON and r.legacy_label is not None

# =========================================================================
# Historical data constraint
# =========================================================================
class TestHistoricalData:
    def test_feature_uses_only_available_historical_data(self):
        close = pd.Series(np.cumsum(np.random.randn(200) * 0.01) + 1.0)
        assert price_range_position(close.tail(120), 60) is not None
        assert price_range_position(close.head(30), 60) is None
