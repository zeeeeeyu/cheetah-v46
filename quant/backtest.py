from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from .features import compute_signal_features
from .guard import signal_confidence, stage_target_weights, weights_turnover
from .models import CheckResult, DataStatus, Regime
from .portfolio import (
    apply_regime_caps,
    apply_sector_cap,
    cap_single_weights,
    hrp_weights,
    inverse_downside_vol_weights,
    select_assets,
)
from .regime import core_regime
from .research.backtest import verify_no_signal_day_fill
from .universe import Instrument, build_signals


@dataclass
class BacktestResult:
    status: str
    reason: str
    metrics: dict[str, float | int | str]
    curve: pd.DataFrame | None
    rebalance_log: pd.DataFrame | None


def validate_backtest_coverage(frames: dict[str, pd.DataFrame], min_days: int = 252) -> tuple[bool, str]:
    if not frames:
        return False, "没有数据"
    common: set[pd.Timestamp] | None = None
    for frame in frames.values():
        dates = set(pd.to_datetime(frame["date"]).dt.normalize())
        common = dates if common is None else common & dates
    count = len(common or [])
    if count < min_days:
        return False, f"共同可用交易日仅 {count} 天；可信回测至少需要 {min_days} 天，不输出绩效结论。"
    return True, "OK"


def _portfolio_target(
    instruments: list[Instrument],
    frames: dict[str, pd.DataFrame],
    settings: dict,
) -> tuple[dict[str, float], list, Regime]:
    checks = {symbol: CheckResult(DataStatus.PASS_) for symbol in frames}
    regime_result = core_regime(frames[settings["benchmark_symbol"]], settings["regime"])
    signals = build_signals(instruments, frames, checks, settings)
    selected, _ = select_assets(
        signals=signals,
        frames=frames,
        regime=regime_result.regime,
        max_selected=int(settings["portfolio"]["max_selected_assets"]),
        max_pair_corr=float(settings["portfolio"]["max_pair_correlation"]),
    )
    cash = settings["portfolio"]["cash_symbol"]
    instrument_map = {item.symbol: item.__dict__ for item in instruments}
    target = inverse_downside_vol_weights(selected, float(settings["portfolio"]["max_single_weight"]), cash)
    target = cap_single_weights(target, float(settings["portfolio"]["max_single_weight"]), cash)
    target = apply_sector_cap(target, instrument_map, float(settings["portfolio"]["max_sector_weight"]), cash)
    target = apply_regime_caps(target, instrument_map, regime_result.regime, settings["portfolio"])
    return target, signals, regime_result.regime


def _drift_weights(
    weights: dict[str, float], daily_factors: dict[str, float], cash_symbol: str) -> tuple[dict[str, float], float]:
    values: dict[str, float] = {}
    for symbol, weight in weights.items():
        factor = 1.0 if symbol == cash_symbol else daily_factors.get(symbol, 1.0)
        values[symbol] = float(weight) * factor
    total = sum(values.values())
    if total <= 0:
        return {cash_symbol: 1.0}, 0.0
    return {symbol: value / total for symbol, value in values.items()}, total


def _performance_metrics(curve: pd.DataFrame) -> dict[str, float]:
    nav = curve["nav"]
    returns = nav.pct_change(fill_method=None).dropna()
    years = max(len(returns) / 252.0, 1.0 / 252.0)
    annualized = float((nav.iloc[-1] / nav.iloc[0]) ** (1.0 / years) - 1.0)
    roll_max = nav.cummax()
    max_dd = float((nav / roll_max - 1.0).min())
    std = float(returns.std(ddof=0))
    downside = returns.clip(upper=0)
    downside_std = float(np.sqrt(np.mean(np.square(downside))))
    sharpe = float(returns.mean() / std * np.sqrt(252)) if std > 0 else np.nan
    sortino = float(returns.mean() / downside_std * np.sqrt(252)) if downside_std > 0 else np.nan
    calmar = float(annualized / abs(max_dd)) if max_dd < 0 else np.nan
    return {
        "total_return": float(nav.iloc[-1] / nav.iloc[0] - 1.0),
        "annualized_return": annualized,
        "max_drawdown": max_dd,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
    }


def run_event_backtest(
    instruments: list[Instrument],
    frames: dict[str, pd.DataFrame],
    settings: dict,
    start_date: str | None = None,
    end_date: str | None = None,
) -> BacktestResult:
    """T close signal -> T+1 open simulated execution -> T+1 close mark.

    This engine is intentionally conservative: it refuses short common histories and does not use
    same-day close execution. It models only daily OHLC data, one-way estimated costs and cash at 0%.
    """
    warmup = int(settings["minimum_history_days"])
    ok, reason = validate_backtest_coverage(frames, min_days=max(252, warmup + 126))
    if not ok:
        return BacktestResult("BLOCKED", reason, {}, None, None)

    indexed = {symbol: frame.copy().assign(date=pd.to_datetime(frame["date"]).dt.normalize()).set_index("date").sort_index() for symbol, frame in frames.items()}
    common_dates = sorted(set.intersection(*(set(frame.index) for frame in indexed.values())))
    common_dates = [pd.Timestamp(item) for item in common_dates]
    if start_date:
        common_dates = [item for item in common_dates if item >= pd.Timestamp(start_date)]
    if end_date:
        common_dates = [item for item in common_dates if item <= pd.Timestamp(end_date)]
    if len(common_dates) < warmup + 2:
        return BacktestResult("BLOCKED", "所选回测区间不足以完成热身与 T+1 执行。", {}, None, None)

    cash = settings["portfolio"]["cash_symbol"]
    interval = int(settings["scheduled_rebalance_interval_trading_days"])
    one_way_cost = float(settings["execution"]["estimated_one_way_cost"])
    max_turnover = float(settings["execution"]["max_non_emergency_turnover"])
    weights: dict[str, float] = {cash: 1.0}
    pending_target: dict[str, float] | None = None
    pending_signal_date: pd.Timestamp | None = None
    previous_signals = None
    last_signal_index = -interval
    nav = 1.0
    records: list[dict[str, Any]] = []
    rebalances: list[dict[str, Any]] = []
    total_cost = 0.0
    total_turnover = 0.0

    for idx, current_date in enumerate(common_dates):
        # Need a previous close to form T+1 open and intraday factors.
        if idx == 0:
            records.append({"date": current_date, "nav": nav, "benchmark": 1.0, "weights": weights.copy()})
            continue
        previous_date = common_dates[idx - 1]
        previous_close = {symbol: float(indexed[symbol].loc[previous_date, "close"]) for symbol in indexed}
        today_open = {symbol: float(indexed[symbol].loc[current_date, "open"]) for symbol in indexed}
        today_close = {symbol: float(indexed[symbol].loc[current_date, "close"]) for symbol in indexed}

        # Overnight move belongs to holdings that existed at the previous close.
        overnight_factors = {symbol: today_open[symbol] / previous_close[symbol] for symbol in indexed}
        weights, overnight_factor = _drift_weights(weights, overnight_factors, cash)
        nav *= overnight_factor

        if pending_target is not None:
            turnover = weights_turnover(weights, pending_target)
            cost = nav * turnover * one_way_cost
            nav -= cost
            total_cost += cost
            total_turnover += turnover
            weights = pending_target.copy()
            rebalances.append(
                {
                    "signal_date": str(pending_signal_date.date()),
                    "execution_date": str(current_date.date()),
                    "turnover": turnover,
                    "cost": cost,
                    "target_weights": pending_target.copy(),
                }
            )
            pending_target = None
            pending_signal_date = None

        intraday_factors = {symbol: today_close[symbol] / today_open[symbol] for symbol in indexed}
        weights, intraday_factor = _drift_weights(weights, intraday_factors, cash)
        nav *= intraday_factor

        # Signals are observed only after the T close, and their execution is queued for T+1 open.
        if idx >= warmup:
            sliced = {
                symbol: indexed[symbol].loc[:current_date].reset_index()
                for symbol in indexed
            }
            target, signals, regime = _portfolio_target(instruments, sliced, settings)
            confidence, _ = signal_confidence(signals, previous_signals, settings["signals"])
            scheduled = (idx - last_signal_index) >= interval
            risk_override = regime == Regime.RISK_OFF
            proposed_turnover = weights_turnover(weights, target)
            can_queue = (scheduled or risk_override) and (risk_override or confidence == "STRONG")
            if can_queue:
                if not risk_override and proposed_turnover > max_turnover:
                    target = stage_target_weights(weights, target, max_turnover)
                pending_target = target
                pending_signal_date = current_date
                last_signal_index = idx
            previous_signals = signals

        benchmark_nav = today_close[settings["benchmark_symbol"]] / float(indexed[settings["benchmark_symbol"]].loc[common_dates[0], "close"])
        records.append({"date": current_date, "nav": nav, "benchmark": benchmark_nav, "weights": weights.copy()})

    # c55：返回前强制「信号日绝不成交」不变量。此前 T+1 只是注释里的意图，
    # 没有任何代码守着它；一旦被改回当日成交，回测会静默返回被高估的绩效。
    fill_ok, fill_reason = verify_no_signal_day_fill(rebalances)
    if not fill_ok:
        return BacktestResult("BLOCKED", f"回测执行时序违约：{fill_reason}", {}, None, None)

    curve = pd.DataFrame(records).set_index("date")
    log = pd.DataFrame(rebalances)
    metrics = _performance_metrics(curve)
    metrics.update(
        {
            "executed_rebalances": int(len(rebalances)),
            "total_turnover": float(total_turnover),
            "total_estimated_cost": float(total_cost),
            "benchmark_total_return": float(curve["benchmark"].iloc[-1] / curve["benchmark"].iloc[0] - 1.0),
        }
    )
    return BacktestResult("READY", "已按 T 日收盘信号、T+1 开盘执行运行；结果仍需样本外与纸面交易验证。", metrics, curve, log)


def t_plus_one_skeleton(frames: dict[str, pd.DataFrame], warmup_days: int = 126) -> BacktestResult:
    # Kept as a compatibility/readiness function used by the simple CLI.
    ok, reason = validate_backtest_coverage(frames, min_days=max(252, warmup_days + 126))
    if not ok:
        return BacktestResult("BLOCKED", reason, {}, None, None)
    return BacktestResult("READY", "数据覆盖通过，可调用 run_event_backtest。", {}, None, None)
