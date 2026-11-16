from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from .backtest import BacktestResult, _performance_metrics
from .fund_features import nav_frames_as_close
from .fund_regime import fund_nav_regime
from .fund_universe import FundInstrument, build_fund_signals
from .guard import signal_confidence, stage_target_weights, weights_turnover
from .models import CheckResult, DataStatus, Regime
from .portfolio import apply_regime_caps, apply_sector_cap, cap_single_weights, inverse_downside_vol_weights, select_assets
from .research.backtest import verify_no_signal_day_fill


def validate_fund_backtest_coverage(frames: dict[str, pd.DataFrame], min_days: int) -> tuple[bool, str]:
    if not frames:
        return False, "没有场外基金净值数据"
    common: set[pd.Timestamp] | None = None
    for frame in frames.values():
        if frame is None or frame.empty:
            return False, "存在空场外基金净值序列"
        dates = set(pd.to_datetime(frame["date"]).dt.normalize())
        common = dates if common is None else common & dates
    count = len(common or [])
    if count < min_days:
        return False, f"场外基金共同可用净值日仅 {count} 天；至少需要 {min_days} 天才允许条件性研究回测。"
    return True, "OK"


def _assumed_tradeability(instruments: list[FundInstrument]) -> dict[str, Any]:
    return {
        "as_of_date": "HISTORICAL_ASSUMPTION",
        "funds": {
            item.symbol: {
                "purchase_status": "OPEN",
                "redemption_status": "OPEN",
                "subscription_fee_rate": 0.0,
                "redemption_fee_rate": 0.0,
                "fee_verified": True,
            }
            for item in instruments
        },
    }


def _fund_target(
    instruments: list[FundInstrument],
    nav_frames: dict[str, pd.DataFrame],
    settings: dict[str, Any],
) -> tuple[dict[str, float], list, Regime]:
    checks = {symbol: CheckResult(DataStatus.PASS_) for symbol in nav_frames}
    tradeability = _assumed_tradeability(instruments)
    regime_result = fund_nav_regime(nav_frames[settings["benchmark_symbol"]])
    signals = build_fund_signals(instruments, nav_frames, checks, settings, tradeability)
    close_frames = nav_frames_as_close(nav_frames)
    selected, _ = select_assets(
        signals=signals,
        frames=close_frames,
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


def _drift_weights(weights: dict[str, float], factors: dict[str, float], cash_symbol: str) -> tuple[dict[str, float], float]:
    values = {symbol: float(weight) * (1.0 if symbol == cash_symbol else factors.get(symbol, 1.0)) for symbol, weight in weights.items()}
    total = sum(values.values())
    if total <= 0:
        return {cash_symbol: 1.0}, 0.0
    return {symbol: value / total for symbol, value in values.items()}, total


def _fund_trade_cost(current: dict[str, float], target: dict[str, float], subscription_fee: float, redemption_fee: float, cash_symbol: str) -> float:
    cost = 0.0
    for symbol in set(current) | set(target):
        if symbol == cash_symbol:
            continue
        delta = float(target.get(symbol, 0.0)) - float(current.get(symbol, 0.0))
        if delta > 0:
            cost += delta * subscription_fee
        elif delta < 0:
            cost += abs(delta) * redemption_fee
    return cost


def run_fund_event_backtest(
    instruments: list[FundInstrument],
    frames: dict[str, pd.DataFrame],
    settings: dict[str, Any],
    start_date: str | None = None,
    end_date: str | None = None,
    allow_research_assumptions: bool = False,
    assumed_subscription_fee: float | None = None,
    assumed_redemption_fee: float | None = None,
) -> BacktestResult:
    """Conditional fund-NAV backtest.

    Signal is observed after T NAV disclosure; order is submitted on a future dealing day and priced
    at that future day's NAV.  Without a historical dataset of subscription/redemption states and
    fee ladders, the function refuses performance output by default.  Research-assumption mode is
    explicit and labelled conditional, never a production validation.
    """
    warmup = int(settings["minimum_history_days"])
    min_days = max(int(settings["backtest"]["minimum_common_days"]), warmup + 126)
    ok, reason = validate_fund_backtest_coverage(frames, min_days)
    if not ok:
        return BacktestResult("BLOCKED", reason, {}, None, None)
    if not allow_research_assumptions:
        return BacktestResult(
            "BLOCKED",
            "缺少历史申购/赎回状态、销售平台费率与确认时滞记录。默认不输出场外基金绩效结论；仅可显式开启研究假设模式。",
            {}, None, None,
        )
    if assumed_subscription_fee is None or assumed_redemption_fee is None:
        return BacktestResult(
            "BLOCKED",
            "研究假设模式仍需显式提供订阅费率和赎回费率；系统不会猜测或默认 0 费率。",
            {}, None, None,
        )
    if assumed_subscription_fee < 0 or assumed_redemption_fee < 0:
        return BacktestResult("BLOCKED", "研究假设费率不能为负", {}, None, None)

    indexed = {
        symbol: frame.copy().assign(date=pd.to_datetime(frame["date"]).dt.normalize()).set_index("date").sort_index()
        for symbol, frame in frames.items()
    }
    common_dates = [pd.Timestamp(day) for day in sorted(set.intersection(*(set(frame.index) for frame in indexed.values())))]
    if start_date:
        common_dates = [day for day in common_dates if day >= pd.Timestamp(start_date)]
    if end_date:
        common_dates = [day for day in common_dates if day <= pd.Timestamp(end_date)]
    lag = int(settings["execution"]["order_submission_lag_trading_days"])
    if len(common_dates) < warmup + lag + 2:
        return BacktestResult("BLOCKED", "所选区间不足以完成热身和基金净值执行滞后。", {}, None, None)

    cash = settings["portfolio"]["cash_symbol"]
    interval = int(settings["scheduled_rebalance_interval_trading_days"])
    max_turnover = float(settings["execution"]["max_non_emergency_turnover"])
    weights: dict[str, float] = {cash: 1.0}
    pending: dict[str, Any] | None = None
    previous_signals = None
    last_signal_index = -interval
    nav = 1.0
    records: list[dict[str, Any]] = []
    rebalances: list[dict[str, Any]] = []
    total_cost = 0.0
    total_turnover = 0.0

    for idx, date in enumerate(common_dates):
        if idx == 0:
            records.append({"date": date, "nav": nav, "benchmark": 1.0, "weights": weights.copy()})
            continue
        prev = common_dates[idx - 1]
        factors = {symbol: float(indexed[symbol].loc[date, "nav"]) / float(indexed[symbol].loc[prev, "nav"]) for symbol in indexed}
        weights, daily_factor = _drift_weights(weights, factors, cash)
        nav *= daily_factor

        if pending is not None and pending["execution_idx"] == idx:
            target = pending["target"]
            turnover = weights_turnover(weights, target)
            cost_weight = _fund_trade_cost(weights, target, assumed_subscription_fee, assumed_redemption_fee, cash)
            cost = nav * cost_weight
            nav -= cost
            total_cost += cost
            total_turnover += turnover
            weights = target.copy()
            rebalances.append(
                {
                    "signal_date": str(pending["signal_date"].date()),
                    "submission_date": str(common_dates[pending["submission_idx"]].date()),
                    "pricing_nav_date": str(date.date()),
                    "confirmation_lag_trading_days": max(
                        int(settings["execution"]["purchase_confirmation_lag_trading_days"]),
                        int(settings["execution"]["redemption_confirmation_lag_trading_days"]),
                    ),
                    "turnover": turnover,
                    "cost_weight": cost_weight,
                    "cost": cost,
                    "target_weights": target.copy(),
                    "assumption": settings["backtest"]["research_assumption_label"],
                }
            )
            pending = None

        # Signal appears only after the date's NAV is known. It can affect a later NAV, never today's NAV.
        if idx >= warmup:
            sliced = {symbol: indexed[symbol].loc[:date].reset_index() for symbol in indexed}
            target, signals, regime = _fund_target(instruments, sliced, settings)
            confidence, _ = signal_confidence(signals, previous_signals, settings["signals"])
            scheduled = (idx - last_signal_index) >= interval
            risk_override = regime == Regime.RISK_OFF
            proposed_turnover = weights_turnover(weights, target)
            if (scheduled or risk_override) and (risk_override or confidence == "STRONG") and idx + lag < len(common_dates):
                if not risk_override and proposed_turnover > max_turnover:
                    target = stage_target_weights(weights, target, max_turnover)
                pending = {"target": target, "signal_date": date, "submission_idx": idx + 1, "execution_idx": idx + lag}
                last_signal_index = idx
            previous_signals = signals

        benchmark_nav = float(indexed[settings["benchmark_symbol"]].loc[date, "nav"]) / float(indexed[settings["benchmark_symbol"]].loc[common_dates[0], "nav"])
        records.append({"date": date, "nav": nav, "benchmark": benchmark_nav, "weights": weights.copy()})

    # c55：基金链路的成交日是「定价净值日」——同样不得等于信号日，
    # 否则等于假设申赎当天就按已知净值成交（T-X2 明确拒绝的假设）。
    fill_ok, fill_reason = verify_no_signal_day_fill(
        rebalances, signal_key="signal_date", execution_key="pricing_nav_date",
    )
    if not fill_ok:
        return BacktestResult("BLOCKED", f"基金回测执行时序违约：{fill_reason}", {}, None, None)

    curve = pd.DataFrame(records).set_index("date")
    log = pd.DataFrame(rebalances)
    metrics = _performance_metrics(curve)
    metrics.update(
        {
            "executed_rebalances": int(len(rebalances)),
            "total_turnover": float(total_turnover),
            "total_assumed_cost": float(total_cost),
            "assumed_subscription_fee": float(assumed_subscription_fee),
            "assumed_redemption_fee": float(assumed_redemption_fee),
            "benchmark_total_return": float(curve["benchmark"].iloc[-1] / curve["benchmark"].iloc[0] - 1.0),
        }
    )
    return BacktestResult(
        "READY_WITH_LIMITATIONS",
        "条件性研究回测：使用直接场外基金累计净值，T 日信号、后续净值日定价；历史申赎状态和费率被显式假设，结果不能作为实盘绩效证明。",
        metrics,
        curve,
        log,
    )
