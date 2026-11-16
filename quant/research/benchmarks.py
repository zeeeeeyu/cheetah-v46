"""V4.6 基准策略库和压力测试。"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Callable
import numpy as np, pandas as pd
from quant.research.backtest import BacktestResult, compute_performance_metrics, simulate_etf_execution, simulate_fund_execution

BenchmarkFn = Callable[[dict[str, pd.DataFrame], dict], BacktestResult]

def benchmark_cash(frames: dict[str, pd.DataFrame], settings: dict) -> BacktestResult:
    return BacktestResult(strategy_id="CASH", strategy_version="1.0", total_return=0.0, annual_return=0.0, max_drawdown=0.0, sharpe=0.0, sortino=0.0, calmar=0.0, status="READY")

def benchmark_buy_and_hold(frames: dict[str, pd.DataFrame], settings: dict) -> BacktestResult:
    sym = settings.get("benchmark_symbol", list(frames.keys())[0]) if frames else ""
    if not sym or sym not in frames: return BacktestResult(strategy_id="BUY_AND_HOLD", status="NO_DATA")
    close = frames[sym]["close"].astype(float) if "close" in frames[sym].columns else frames[sym].iloc[:, 0]
    nav = close / close.iloc[0]; metrics = compute_performance_metrics(nav)
    return BacktestResult(strategy_id="BUY_AND_HOLD", **metrics, status="READY")


def benchmark_equal_weight(frames: dict[str, pd.DataFrame], settings: dict) -> BacktestResult:
    """Initial equal-weight buy-and-hold across all usable ETFs."""
    series = []
    for symbol, frame in frames.items():
        if frame is None or frame.empty or "date" not in frame.columns or "close" not in frame.columns:
            continue
        temp = frame[["date", "close"]].copy()
        temp["date"] = pd.to_datetime(temp["date"])
        temp = temp.dropna().drop_duplicates("date").set_index("date")["close"].astype(float)
        if len(temp) < 2 or temp.iloc[0] <= 0:
            continue
        series.append((temp / temp.iloc[0]).rename(symbol))
    if not series:
        return BacktestResult(strategy_id="EQUAL_WEIGHT", status="NO_DATA")
    aligned = pd.concat(series, axis=1, join="inner").dropna()
    if len(aligned) < 2:
        return BacktestResult(strategy_id="EQUAL_WEIGHT", status="INSUFFICIENT_DATA")
    nav = aligned.mean(axis=1)
    metrics = compute_performance_metrics(nav)
    return BacktestResult(strategy_id="EQUAL_WEIGHT", **metrics, status="READY")

def benchmark_ma120(frames: dict[str, pd.DataFrame], settings: dict) -> BacktestResult:
    sym = settings.get("benchmark_symbol", list(frames.keys())[0]) if frames else ""
    if not sym or sym not in frames: return BacktestResult(strategy_id="MA120", status="NO_DATA")
    close = frames[sym]["close"].astype(float); dates = pd.to_datetime(frames[sym]["date"])
    if len(close) < 120: return BacktestResult(strategy_id="MA120", status="INSUFFICIENT_DATA")
    ma120 = close.rolling(120).mean(); nav_values = [1.0]; nav = 1.0; position = 1.0
    for i in range(120, len(close)):
        prev_close = float(close.iloc[i-1]); cur_close = float(close.iloc[i]); prev_ma = float(ma120.iloc[i-1])
        position = 1.0 if cur_close > prev_ma else 0.0
        nav *= (1.0 + position * (cur_close / prev_close - 1.0)); nav_values.append(nav)
    nav_s = pd.Series(nav_values); metrics = compute_performance_metrics(nav_s)
    return BacktestResult(strategy_id="MA120", **metrics, status="READY")

def benchmark_v46_tactical_trend(frames: dict[str, pd.DataFrame], settings: dict) -> BacktestResult:
    """V4.6 TACTICAL_TREND 策略基准。"""
    from quant.strategies.tactical_etf.trend import run_tactical_trend
    from quant.models import Regime
    symbols = [s for s in frames if s in frames and frames[s] is not None and not frames[s].empty and "close" in frames[s].columns]
    if len(symbols) < 1: return BacktestResult(strategy_id="V46_TACTICAL_TREND", status="NO_DATA")
    common_dates = sorted(set.intersection(*(set(pd.to_datetime(frames[s]["date"])) for s in symbols)))
    if len(common_dates) < 60: return BacktestResult(strategy_id="V46_TACTICAL_TREND", status="INSUFFICIENT_DATA")
    nav = 1.0; nav_values = [1.0]; rebalance_interval = 20; trades = 0; total_cost = 0.0
    weights = {"CASH": 1.0}; one_way_cost = 0.001; cfg = {"short_window": 10, "medium_window": 20, "long_window": 60, "fi_window": 60}
    for idx in range(1, len(common_dates)):
        curr, prev = common_dates[idx], common_dates[idx-1]
        new_w = {}
        for sym, w in weights.items():
            if sym == "CASH": new_w[sym] = w
            elif sym in frames:
                f = frames[sym]; pc = f[f["date"] == prev]["close"].values; cc = f[f["date"] == curr]["close"].values
                if len(pc) > 0 and len(cc) > 0 and pc[0] > 0: new_w[sym] = w * float(cc[0]/pc[0])
                else: new_w[sym] = w
        tot = sum(new_w.values())
        if tot > 0: weights = {k: v/tot for k,v in new_w.items()}
        if idx % rebalance_interval == 0:
            result = run_tactical_trend(frames, symbols, cfg, Regime.RISK_ON, 3, 0.5)
            tw = result.target_weights
            turn = 0.5 * sum(abs(tw.get(k,0)-weights.get(k,0)) for k in set(tw)|set(weights))
            cost = nav * turn * one_way_cost; nav -= cost; total_cost += cost
            weights = tw; trades += 1
        nav_values.append(nav)
    nav_s = pd.Series(nav_values); metrics = compute_performance_metrics(nav_s)
    return BacktestResult(strategy_id="V46_TACTICAL_TREND", **metrics, status="READY", total_cost=total_cost, trade_count=trades)



def _common_dates(frames: dict[str, pd.DataFrame], symbols: list[str]) -> list[pd.Timestamp]:
    sets = []
    for symbol in symbols:
        frame = frames[symbol]
        sets.append(set(pd.to_datetime(frame["date"])))
    return sorted(set.intersection(*sets)) if sets else []


def _portfolio_drift(
    frames: dict[str, pd.DataFrame],
    weights: dict[str, float],
    prev: pd.Timestamp,
    curr: pd.Timestamp,
) -> tuple[float, dict[str, float]]:
    gross = 0.0
    values: dict[str, float] = {}
    for symbol, weight in weights.items():
        if symbol == "CASH":
            values[symbol] = weight
            gross += weight
            continue
        frame = frames.get(symbol)
        if frame is None or frame.empty:
            values[symbol] = weight
            gross += weight
            continue
        idx = frame.assign(date=pd.to_datetime(frame["date"])).set_index("date")
        if prev not in idx.index or curr not in idx.index:
            values[symbol] = weight
            gross += weight
            continue
        p0 = float(idx.loc[prev, "close"])
        p1 = float(idx.loc[curr, "close"])
        ratio = p1 / p0 if p0 > 0 else 1.0
        values[symbol] = weight * ratio
        gross += values[symbol]
    if gross <= 0:
        return 1.0, {"CASH": 1.0}
    return gross, {k: v / gross for k, v in values.items()}


def benchmark_top1_momentum(frames: dict[str, pd.DataFrame], settings: dict) -> BacktestResult:
    """Top-1 momentum with real daily NAV drift and explicit rebalancing cost."""
    symbols = [s for s, f in frames.items() if f is not None and not f.empty and {"date", "close"} <= set(f.columns)]
    if not symbols:
        return BacktestResult(strategy_id="TOP1_MOMENTUM", status="NO_DATA")
    dates = _common_dates(frames, symbols)
    if len(dates) < 61:
        return BacktestResult(strategy_id="TOP1_MOMENTUM", status="INSUFFICIENT_DATA")
    nav = 1.0
    nav_values = [nav]
    weights = {"CASH": 1.0}
    current_pick = None
    total_cost = 0.0
    trades = 0
    interval = int(settings.get("scheduled_rebalance_interval_trading_days", 20))
    one_way_cost = 0.001
    for i in range(1, len(dates)):
        factor, weights = _portfolio_drift(frames, weights, dates[i-1], dates[i])
        nav *= factor
        if i >= 60 and i % interval == 0:
            best_sym, best_r = None, float("-inf")
            for symbol in symbols:
                frame = frames[symbol].copy()
                frame["date"] = pd.to_datetime(frame["date"])
                history = frame[frame["date"] <= dates[i]].sort_values("date")
                if len(history) < 61:
                    continue
                close = history["close"].astype(float)
                momentum = float(close.iloc[-1] / close.iloc[-61] - 1.0)
                if momentum > best_r:
                    best_r, best_sym = momentum, symbol
            target = {best_sym: 1.0} if best_sym else {"CASH": 1.0}
            turnover = 0.5 * sum(abs(target.get(k, 0.0) - weights.get(k, 0.0)) for k in set(target) | set(weights))
            if turnover > 1e-12:
                cost = nav * turnover * one_way_cost
                nav -= cost
                total_cost += cost
                trades += 1
            weights = target
            current_pick = best_sym
        nav_values.append(nav)
    metrics = compute_performance_metrics(pd.Series(nav_values))
    return BacktestResult(
        strategy_id="TOP1_MOMENTUM", **metrics, status="READY",
        total_cost=total_cost, trade_count=trades,
    )


def benchmark_v46_tactical_trend(frames: dict[str, pd.DataFrame], settings: dict) -> BacktestResult:
    """V4.6 tactical trend with point-in-time data slicing and daily NAV drift."""
    from quant.strategies.tactical_etf.trend import run_tactical_trend
    from quant.models import Regime
    symbols = [s for s, f in frames.items() if f is not None and not f.empty and {"date", "close"} <= set(f.columns)]
    if not symbols:
        return BacktestResult(strategy_id="V46_TACTICAL_TREND", status="NO_DATA")
    dates = _common_dates(frames, symbols)
    if len(dates) < 61:
        return BacktestResult(strategy_id="V46_TACTICAL_TREND", status="INSUFFICIENT_DATA")
    nav = 1.0
    nav_values = [nav]
    weights = {"CASH": 1.0}
    total_cost = 0.0
    trades = 0
    interval = int(settings.get("scheduled_rebalance_interval_trading_days", 20))
    one_way_cost = 0.001
    cfg = {"short_window": 10, "medium_window": 20, "long_window": 60, "fi_window": 60}
    for i in range(1, len(dates)):
        factor, weights = _portfolio_drift(frames, weights, dates[i-1], dates[i])
        nav *= factor
        if i >= 60 and i % interval == 0:
            point_in_time = {}
            for symbol in symbols:
                frame = frames[symbol].copy()
                frame["date"] = pd.to_datetime(frame["date"])
                point_in_time[symbol] = frame[frame["date"] <= dates[i]].copy()
            result = run_tactical_trend(point_in_time, symbols, cfg, Regime.RISK_ON, 3, 0.5)
            target = result.target_weights
            turnover = 0.5 * sum(abs(target.get(k, 0.0) - weights.get(k, 0.0)) for k in set(target) | set(weights))
            if turnover > 1e-12:
                cost = nav * turnover * one_way_cost
                nav -= cost
                total_cost += cost
                trades += 1
            weights = target
        nav_values.append(nav)
    metrics = compute_performance_metrics(pd.Series(nav_values))
    return BacktestResult(
        strategy_id="V46_TACTICAL_TREND", **metrics, status="READY",
        total_cost=total_cost, trade_count=trades,
    )

BENCHMARK_STRATEGIES: dict[str, BenchmarkFn] = {
    "CASH": benchmark_cash, "BUY_AND_HOLD": benchmark_buy_and_hold,
    "EQUAL_WEIGHT": benchmark_equal_weight, "MA120": benchmark_ma120,
    "TOP1_MOMENTUM": benchmark_top1_momentum,
    "V46_TACTICAL_TREND": benchmark_v46_tactical_trend,
}

@dataclass
class StressTestScenario:
    name: str; description: str; date: str; expected_regime: str

STRESS_SCENARIOS = [
    StressTestScenario("2024-01-26 极端压力", "沪深300跌破所有均线，微盘踩踏", "2024-01-26", "STRESS_RISK_OFF"),
    StressTestScenario("2020-03-19 疫情冲击", "全球疫情恐慌低点", "2020-03-19", "STRESS_RISK_OFF"),
    StressTestScenario("2015-07-08 股灾", "A股熔断股灾", "2015-07-08", "STRESS_RISK_OFF"),
]

def run_stress_test(strategy_fn: BenchmarkFn, frames: dict[str, pd.DataFrame], settings: dict, scenario: StressTestScenario) -> BacktestResult:
    """在指定压力场景下运行策略。"""
    cutoff = pd.Timestamp(scenario.date)
    stress_frames = {}
    for s, f in frames.items():
        ff = f[pd.to_datetime(f["date"]) <= cutoff].copy() if "date" in f.columns else f
        stress_frames[s] = ff
    return strategy_fn(stress_frames, settings)
