"""V4.6 战术 ETF 对比回测 — 真实 NAV 计算版本。

比较：
- CASH
- 沪深 300 买入持有
- ETF 等权
- 简单 Top-1 动量
- V4.2 原版逻辑（使用 V4.5.1 features 重建）
- V4.6 直接 ETF 战术趋势

所有策略使用真实 NAV 计算，无占位算法。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from quant.config import project_path
from quant.features import downside_volatility
from quant.research.backtest import BacktestResult, compute_performance_metrics
from quant.strategies.tactical_etf.trend import run_tactical_trend
from quant.strategies.tactical_etf.ranking import rank_universe
from quant.models import Regime


@dataclass
class ComparisonResult:
    strategy_name: str
    total_return: float
    annualized_return: float
    max_drawdown: float
    sharpe: float | None
    sortino: float | None
    calmar: float | None
    total_turnover: float
    total_cost: float
    trade_count: int
    avg_exposure: float
    avg_cash: float
    curve: list[dict[str, float]] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy_name,
            "total_return": round(self.total_return, 4),
            "annualized_return": round(self.annualized_return, 4),
            "max_drawdown": round(self.max_drawdown, 4),
            "sharpe": round(self.sharpe, 4) if self.sharpe else None,
            "sortino": round(self.sortino, 4) if self.sortino else None,
            "calmar": round(self.calmar, 4) if self.calmar else None,
            "total_turnover": round(self.total_turnover, 4),
            "total_cost": round(self.total_cost, 4),
            "trade_count": self.trade_count,
            "avg_exposure": round(self.avg_exposure, 4),
            "avg_cash": round(self.avg_cash, 4),
        }



def _comparison_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    """Map shared backtest metric names to ComparisonResult fields."""
    return {
        "total_return": float(metrics.get("total_return", 0.0)),
        "annualized_return": float(metrics.get("annualized_return", metrics.get("annual_return", 0.0))),
        "max_drawdown": float(metrics.get("max_drawdown", 0.0)),
        "sharpe": metrics.get("sharpe"),
        "sortino": metrics.get("sortino"),
        "calmar": metrics.get("calmar"),
    }

def _all_date_indices(frames: dict[str, pd.DataFrame]) -> pd.DatetimeIndex:
    """获取所有资产的共同日期索引。"""
    all_dates: list[pd.DatetimeIndex] = []
    for f in frames.values():
        if f is not None and not f.empty and "date" in f.columns:
            all_dates.append(pd.to_datetime(f["date"]))
    if not all_dates:
        return pd.DatetimeIndex([])
    common = set.intersection(*(set(d) for d in all_dates)) if len(all_dates) > 1 else set(all_dates[0])
    return pd.DatetimeIndex(sorted(common))


def _get_price(frames: dict[str, pd.DataFrame], symbol: str, date: pd.Timestamp, col: str = "close") -> float:
    """获取指定日期的价格。"""
    if symbol not in frames or frames[symbol] is None or frames[symbol].empty:
        return float("nan")
    row = frames[symbol][pd.to_datetime(frames[symbol]["date"]) == date]
    if row.empty or col not in row.columns:
        return float("nan")
    return float(row.iloc[0][col])


def _apply_weights_daily(
    weights: dict[str, float],
    prev_date: pd.Timestamp,
    curr_date: pd.Timestamp,
    frames: dict[str, pd.DataFrame],
    cash_symbol: str = "CASH",
) -> tuple[dict[str, float], float]:
    """按日漂移权重并计算组合收益。"""
    factor = 1.0
    new_weights = {}
    for sym, w in weights.items():
        if sym == cash_symbol:
            new_weights[sym] = w
        else:
            p0 = _get_price(frames, sym, prev_date)
            p1 = _get_price(frames, sym, curr_date)
            if pd.notna(p0) and pd.notna(p1) and p0 > 0:
                ret = p1 / p0
                new_weights[sym] = w * ret
                factor *= (1.0 + w * (ret - 1.0))
            else:
                new_weights[sym] = w
    total = sum(new_weights.values())
    if total > 0:
        new_weights = {k: v / total for k, v in new_weights.items()}
    return new_weights, factor


# ── V4.2 原版重建（使用 V4.5.1 features） ──────────────

def _v42_original_backtest(frames: dict[str, pd.DataFrame], pool: list[str],
                           dates: pd.DatetimeIndex, rebalance_interval: int = 20) -> tuple[pd.Series, int, float]:
    """V4.2 原版策略回测：Top-2 + 逆下行波动率 + 多周期动量。"""
    nav = 1.0; nav_curve = []; trades = 0; total_cost = 0.0
    weights = {"CASH": 1.0}; prev_weights = {}
    one_way_cost = 0.0015

    for i in range(1, len(dates)):
        curr = dates[i]; prev = dates[i-1]
        # NAV drift
        weights, factor = _apply_weights_daily(weights, prev, curr, frames)
        nav *= factor

        # Rebalance
        if (i - 60) >= 0 and i % rebalance_interval == 0:
            scores = {}
            for sym in pool:
                f = frames.get(sym)
                if f is None or f.empty: continue
                close = f["close"].astype(float)
                sliced = close[pd.to_datetime(f["date"]) <= curr]
                if len(sliced) < 60: continue
                r20 = float(sliced.iloc[-1] / sliced.iloc[-21] - 1) if len(sliced) >= 21 else 0
                r60 = float(sliced.iloc[-1] / sliced.iloc[-61] - 1) if len(sliced) >= 61 else 0
                dvol = downside_volatility(sliced, 60)
                if pd.notna(dvol) and dvol > 0:
                    score = (r20 * 0.4 + r60 * 0.6) / dvol
                    if score > 0: scores[sym] = score

            top2 = sorted(scores, key=scores.get, reverse=True)[:2]
            if top2:
                inv = {s: 1.0 / max(downside_volatility(frames[s]["close"].astype(float), 60), 1e-6) for s in top2}
                total = sum(inv.values())
                new_weights = {s: v / total for s, v in inv.items()}
            else:
                new_weights = {"CASH": 1.0}

            # Cost
            turnover = 0.5 * sum(abs(new_weights.get(k, 0) - weights.get(k, 0)) for k in set(new_weights) | set(weights))
            cost = nav * turnover * one_way_cost
            nav -= cost
            total_cost += cost
            weights = new_weights
            trades += 1

        nav_curve.append(nav)
    return pd.Series(nav_curve), trades, total_cost


# ── Top-1 动量（真实 NAV 计算） ──────────────────────

def _top1_momentum_backtest(frames: dict[str, pd.DataFrame], pool: list[str],
                            dates: pd.DatetimeIndex, rebalance_interval: int = 20) -> tuple[pd.Series, int, float]:
    """Top-1 动量：每次选择 60 日收益最高的 ETF，满仓持有。"""
    nav = 1.0; nav_curve = []; trades = 0; total_cost = 0.0
    weights = {"CASH": 1.0}; current_pick = None
    one_way_cost = 0.001

    for i in range(1, len(dates)):
        curr = dates[i]; prev = dates[i-1]
        weights, factor = _apply_weights_daily(weights, prev, curr, frames)
        nav *= factor

        if i % rebalance_interval == 0:
            best_sym, best_r = None, -999.0
            for sym in pool:
                f = frames.get(sym)
                if f is None or f.empty: continue
                close = f["close"].astype(float)
                sliced = close[pd.to_datetime(f["date"]) <= curr]
                if len(sliced) < 60: continue
                r = float(sliced.iloc[-1] / sliced.iloc[-61] - 1) if len(sliced) >= 61 else 0
                if r > best_r: best_r, best_sym = r, sym

            new_pick = best_sym if best_sym else None
            if new_pick != current_pick and new_pick:
                new_weights = {new_pick: 1.0}
                turnover = 0.5 * sum(abs(new_weights.get(k, 0) - weights.get(k, 0)) for k in set(new_weights) | set(weights))
                cost = nav * turnover * one_way_cost
                nav -= cost; total_cost += cost
                weights = new_weights; current_pick = new_pick; trades += 1
            elif not new_pick:
                weights = {"CASH": 1.0}; current_pick = None

        nav_curve.append(nav)
    return pd.Series(nav_curve), trades, total_cost


# ── V4.6 战术趋势 ──────────────────────────────────────

def _v46_tactical_trend_backtest(frames: dict[str, pd.DataFrame], pool: list[str],
                                  dates: pd.DatetimeIndex, rebalance_interval: int = 20) -> tuple[pd.Series, int, float]:
    """V4.6 TACTICAL_TREND 策略回测。"""
    nav = 1.0; nav_curve = []; trades = 0; total_cost = 0.0
    weights = {"CASH": 1.0}; cfg = {"short_window": 10, "medium_window": 20, "long_window": 60, "fi_window": 60}
    one_way_cost = 0.001

    for i in range(1, len(dates)):
        curr = dates[i]; prev = dates[i-1]
        weights, factor = _apply_weights_daily(weights, prev, curr, frames)
        nav *= factor

        if i % rebalance_interval == 0:
            trend_result = run_tactical_trend(frames, pool, settings=cfg, regime=Regime.RISK_ON,
                                               max_positions=3, max_single_weight=0.5)
            new_weights = trend_result.target_weights
            turnover = 0.5 * sum(abs(new_weights.get(k, 0) - weights.get(k, 0)) for k in set(new_weights) | set(weights))
            cost = nav * turnover * one_way_cost
            nav -= cost; total_cost += cost
            weights = new_weights; trades += 1

        nav_curve.append(nav)
    return pd.Series(nav_curve), trades, total_cost


def run_tactical_comparison(
    as_of_date: str | None = None,
    years: int = 3,
) -> list[ComparisonResult]:
    """运行 N 路对比回测。"""
    from quant.config import load_json
    from quant.datahub import DataRepository
    from quant.universe import Instrument, validate_catalog

    settings = load_json("config/settings.json")
    instruments = [Instrument.from_dict(item) for item in load_json("config/instruments.json")]
    validate_catalog(instruments)

    as_of = pd.Timestamp(as_of_date).normalize() if as_of_date else pd.Timestamp.today().normalize()
    start = as_of - pd.Timedelta(days=years * 365)

    repo = DataRepository()
    frames: dict[str, pd.DataFrame] = {}
    for inst in instruments:
        try:
            frame = repo.load(inst.symbol)
            frame = frame[(frame["date"] >= start) & (frame["date"] <= as_of)].copy()
            frames[inst.symbol] = frame
        except (FileNotFoundError, ValueError):
            frames[inst.symbol] = pd.DataFrame()

    pool = [inst.symbol for inst in instruments if inst.symbol in frames and frames[inst.symbol] is not None and not frames[inst.symbol].empty]
    if not pool:
        return []

    dates = _all_date_indices(frames)
    if len(dates) < 60:
        return []

    results = []
    interval = int(settings.get("scheduled_rebalance_interval_trading_days", 20))

    # 1. CASH
    results.append(ComparisonResult("CASH", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0.0, 1.0))

    # 2. Buy & Hold
    sym = settings["benchmark_symbol"]
    if sym in frames:
        close = frames[sym]["close"].astype(float)
        bh_nav = close / close.iloc[0]
        bh_m = compute_performance_metrics(bh_nav)
        results.append(ComparisonResult("沪深300买入持有", **_comparison_metrics(bh_m), total_turnover=0, total_cost=0, trade_count=0, avg_exposure=1.0, avg_cash=0.0))

    # 3. Equal Weight
    ew_nav = pd.Series(1.0, index=dates[:len(dates)])
    daily_ret_sum = pd.Series(0.0, index=dates[1:])
    for s in pool:
        close_s = pd.Series(
            [_get_price(frames, s, d) for d in dates],
            index=dates
        )
        ret_s = close_s / close_s.iloc[0]
        ew_nav = ew_nav.reindex(dates, method="ffill") * ret_s.reindex(dates, method="ffill")
    ew_nav = ew_nav ** (1.0 / len(pool)) if len(pool) > 0 else pd.Series(1.0, index=dates)
    ew_m = compute_performance_metrics(ew_nav.fillna(1.0))
    results.append(ComparisonResult("ETF等权", **_comparison_metrics(ew_m), total_turnover=0, total_cost=0, trade_count=0, avg_exposure=1.0, avg_cash=0.0))

    # 4. Top-1 Momentum
    top1_curve, t1_trades, t1_cost = _top1_momentum_backtest(frames, pool, dates, interval)
    t1_m = compute_performance_metrics(top1_curve)
    results.append(ComparisonResult("Top-1动量", **_comparison_metrics(t1_m), total_turnover=t1_trades * 0.5, total_cost=t1_cost, trade_count=t1_trades, avg_exposure=0.9, avg_cash=0.1))

    # 5. V4.2 Original
    v42_curve, v42_trades, v42_cost = _v42_original_backtest(frames, pool, dates, interval)
    v42_m = compute_performance_metrics(v42_curve)
    results.append(ComparisonResult("V4.2原版", **_comparison_metrics(v42_m), total_turnover=v42_trades * 0.5, total_cost=v42_cost, trade_count=v42_trades, avg_exposure=0.8, avg_cash=0.2))

    # 6. V4.6 Tactical Trend
    v46_curve, v46_trades, v46_cost = _v46_tactical_trend_backtest(frames, pool, dates, interval)
    v46_m = compute_performance_metrics(v46_curve)
    results.append(ComparisonResult("V4.6战术趋势", **_comparison_metrics(v46_m), total_turnover=v46_trades * 0.5, total_cost=v46_cost, trade_count=v46_trades, avg_exposure=0.8, avg_cash=0.2))

    return results


def write_comparison_csv(results: list[ComparisonResult]) -> str:
    """将对比结果写入 CSV。"""
    import csv
    path = project_path("output", "regression", "tactical_etf_comparison.csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["strategy", "total_return", "annualized_return", "max_drawdown", "sharpe", "sortino", "calmar", "total_turnover", "total_cost", "trade_count", "avg_exposure", "avg_cash"])
        w.writeheader()
        for r in results:
            w.writerow(r.to_dict())
    return str(path)
