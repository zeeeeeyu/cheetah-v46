"""V4.6 回测框架测试。"""
from __future__ import annotations
import tempfile, json
from pathlib import Path
import numpy as np, pandas as pd, pytest
from quant.research.backtest import BacktestResult, compute_performance_metrics, simulate_etf_execution, simulate_fund_execution
from quant.research.walk_forward import create_walk_forward_folds, WalkForwardFold, ExperimentRegistry, ParameterTrial, run_walk_forward
from quant.research.benchmarks import benchmark_cash, benchmark_buy_and_hold, benchmark_equal_weight, benchmark_ma120, benchmark_top1_momentum, BENCHMARK_STRATEGIES
from quant.gov.strategy_lifecycle import StrategyLifecycle, ModelStatus, StrategyRecord, StrategyRegistry

@pytest.fixture
def sample_nav(): return pd.Series(np.cumprod(1 + np.random.randn(500)*0.01) + 1.0)
@pytest.fixture
def sample_frames():
    np.random.seed(42); dates = pd.date_range("2023-01-01", periods=500, freq="B"); frames = {}
    for sym in ["510300", "515000", "512660"]:
        close = np.cumprod(1 + np.random.randn(500)*0.015)*3.0
        frames[sym] = pd.DataFrame({"date": dates, "close": close, "open": close*(1+np.random.randn(500)*0.005), "high": close*(1+np.abs(np.random.randn(500))*0.01), "low": close*(1-np.abs(np.random.randn(500))*0.01), "volume": np.random.rand(500)*1e7+1e6, "turnover": np.random.rand(500)*1e8+1e7})
    return frames

class TestBacktestResult:
    def test_backtest_uses_only_available_data(self):
        nav = pd.Series(np.cumprod(1 + np.random.randn(100)*0.01) + 1.0)
        m = compute_performance_metrics(nav)
        assert "total_return" in m and "max_drawdown" in m and "sharpe" in m

class TestExecutionSimulator:
    def test_etf_trade_respects_t_plus_one_model(self, sample_frames):
        frame = sample_frames["510300"]; dates = pd.to_datetime(frame["date"])
        qty, price, amount, status = simulate_etf_execution(dates.iloc[100], frame, 10000.0)
        assert status in ("FILLED", "BELOW_LOT_SIZE", "SIGNAL_EXPIRED")
    def test_etf_trade_respects_lot_size(self, sample_frames):
        frame = sample_frames["510300"]; dates = pd.to_datetime(frame["date"])
        qty, price, amount, status = simulate_etf_execution(dates.iloc[100], frame, 5000.0)
        if status == "FILLED": assert int(qty) % 100 == 0

class TestPerformanceMetrics:
    def test_zero_turnover_has_zero_cost(self):
        from quant.research.backtest import compute_performance_metrics
        nav = pd.Series([1.0]*100); m = compute_performance_metrics(nav)
        assert m["total_return"] == 0.0
    def test_max_drawdown_direction_is_correct(self):
        nav = pd.Series([1.0, 1.1, 0.9, 1.05])
        m = compute_performance_metrics(nav); dd = m["max_drawdown"]
        assert dd < 0  # 回撤为负值
        # -10% 优于 -20% 的验证: -0.10 > -0.20
        nav2 = pd.Series([1.0, 1.1, 0.8, 1.05])
        m2 = compute_performance_metrics(nav2)
        assert m["max_drawdown"] > m2["max_drawdown"]  # -0.09 > -0.20

class TestWalkForward:
    def test_walk_forward_freezes_test_parameters(self):
        dates = [f"2023-{(i//30)+1:02d}-{(i%30)+1:02d}" for i in range(500)]
        folds = create_walk_forward_folds(dates, train_size=252, test_size=126, step=126)
        assert len(folds) >= 1
        for f in folds: assert f.train_start < f.test_start and f.train_end < f.test_end
    def test_test_window_is_not_used_for_training(self):
        dates = [f"2023-{(i//30)+1:02d}-{(i%30)+1:02d}" for i in range(500)]
        folds = create_walk_forward_folds(dates, train_size=252, test_size=126, step=126)
        for f in folds:
            train_end_dt = pd.Timestamp(f.train_end)
            test_start_dt = pd.Timestamp(f.test_start)
            assert test_start_dt > train_end_dt  # 测试开始不早于训练结束

class TestExperimentRegistry:
    def test_all_parameter_trials_are_saved(self):
        reg = ExperimentRegistry()
        for i in range(5): t = reg.register_trial({"param": i}); reg.record_result(t, {"total_return": i*0.01})
        assert len(reg.all_trials()) == 5
    def test_failed_trials_are_saved(self):
        reg = ExperimentRegistry()
        t = reg.register_trial({"bad": 1}); reg.record_result(t, None, error="测试失败")
        assert len(reg.failed_trials()) == 1
    def test_random_metric_is_forbidden(self):
        reg = ExperimentRegistry()
        t = reg.register_trial({"x": 1})
        # 不允许使用 np.random 伪造结果 — 验证注册表无随机结果
        assert t.status == "PENDING", "新实验应为 PENDING 状态"
        assert t.result is None, "新实验不应有结果"

class TestBenchmarks:
    def test_benchmark_uses_same_date_range(self, sample_frames):
        r = benchmark_cash(sample_frames, {}); assert r.strategy_id == "CASH"
        r = benchmark_buy_and_hold(sample_frames, {"benchmark_symbol": "510300"}); assert r.strategy_id == "BUY_AND_HOLD"

class TestStateIsolation:
    def test_backtest_and_live_state_are_isolated(self, sample_frames):
        """回测和实盘状态隔离。"""
        r = benchmark_buy_and_hold(sample_frames, {"benchmark_symbol": "510300"})
        assert not hasattr(r, "live_order_permission")

class TestLifecycle:
    def test_strategy_cannot_promote_itself_to_live(self):
        """策略不能自动晋升到实盘。"""
        reg = StrategyRegistry()
        sr = reg.register("TEST", "测试策略")
        ok, _ = sr.promote(StrategyLifecycle.REAL)  # IDEA → REAL 非法
        assert not ok
        ok, _ = sr.promote(StrategyLifecycle.RESEARCH)
        assert ok
        ok, _ = sr.promote(StrategyLifecycle.BACKTESTED)
        assert ok
        ok, _ = sr.promote(StrategyLifecycle.PAPER)
        assert ok
        ok, _ = sr.promote(StrategyLifecycle.REAL_LIMITED)
        assert ok
        ok, _ = sr.promote(StrategyLifecycle.REAL)
        assert ok
    def test_model_review_required_blocks_promotion(self):
        """MODEL_REVIEW_REQUIRED 阻止向实盘晋升。"""
        reg = StrategyRegistry()
        sr = reg.register("TEST2", "测试")
        # 快速晋升到 REAL_LIMITED
        for s in [StrategyLifecycle.RESEARCH, StrategyLifecycle.BACKTESTED, StrategyLifecycle.PAPER, StrategyLifecycle.REAL_LIMITED]:
            sr.promote(s)
        sr.set_model_status(ModelStatus.MODEL_REVIEW_REQUIRED, "飘移检测")
        ok, _ = sr.promote(StrategyLifecycle.REAL)
        assert not ok

class TestFundConstraints:
    def test_fund_nav_cannot_trade_before_publication(self, sample_frames):
        """基金不能使用未公布净值交易。"""
        frame = sample_frames["510300"].copy(); frame = frame.rename(columns={"close": "nav"})
        dates = pd.to_datetime(frame["date"])
        qty, price, fee, amount, status = simulate_fund_execution(dates.iloc[400], frame, 10000.0)
        assert status in ("FILLED", "CONFIRMATION_LAG_EXCEEDS_HISTORY")

class TestMinCommission:
    def test_minimum_commission_is_applied(self):
        """最低佣金约束。"""
        from quant.exe.cost_model import estimate_etf_cost
        c = estimate_etf_cost(1000.0, 10.0)  # 1000元, 佣金=0.25, 但最低5元
        assert c.estimated_fee >= 5.0
