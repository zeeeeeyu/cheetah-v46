from __future__ import annotations

"""V4.5.1 strategy-governance comparison for the direct-Fund-NAV branch.

This module compares *three explicitly named research strategies* on the same direct fund NAV
series, same decision dates, same T-day-signal/T+1-NAV execution convention, and per-fund cost
assumptions:

* ``LEGACY_TOP2_RECONSTRUCTED`` – the old system's 20/60 + downside-vol + fragmentation
  philosophy, rebuilt honestly on direct NAV.  It is a research reconstruction, not a byte-for-byte
  replay of old proxy/volume code.
* ``TOP2_DYNAMIC_EXPOSURE`` – the intended practical evolution: the same Top-2 core, with
  explicit risk-on/neutral/risk-off exposure caps.
* ``V42_MULTI_ASSET_CASH`` – current multi-factor, correlation-controlled, up-to-three-fund
  strategy with regime caps.

No strategy can promote itself.  The module reports where cash came from, applies fees per fund,
and produces rolling forward stability slices.  It never fabricates a ten-year conclusion when the
user only has a shorter direct-NAV history.
"""

from dataclasses import dataclass
from typing import Any
import json
import math

import numpy as np
import pandas as pd

from .backtest import _performance_metrics
from .config import project_path
from .fund_datahub import FundNAVRepository, auto_refresh_direct_fund_navs, fund_nav_quality_gate
from .fund_fee_assumptions import FeeAssumption, resolve_research_fee_table
from .fund_features import compute_fund_nav_features, nav_frames_as_close
from .fund_historical_coverage import historical_coverage_report
from .fund_regime import fund_nav_regime
from .fund_universe import FundInstrument, build_fund_signals, validate_fund_catalog
from .models import CheckResult, DataStatus, Regime, Signal
from .portfolio import (
    apply_regime_caps,
    apply_sector_cap,
    cap_single_weights,
    inverse_downside_vol_weights,
    select_assets,
)


STRATEGY_LABELS = {
    "legacy_top2": "旧策略核心重构 Top-2",
    "top2_dynamic": "Top-2 动态仓位",
    "v42_multi_cash": "新版多资产+现金",
}


@dataclass
class StrategyComparisonResult:
    status: str
    reason: str
    as_of_date: str
    data_summary: dict[str, Any]
    assumptions: dict[str, Any]
    legacy_metrics: dict[str, Any]
    dynamic_top2_metrics: dict[str, Any]
    v42_metrics: dict[str, Any]
    comparison: dict[str, Any]
    walk_forward: dict[str, Any]
    curve: pd.DataFrame | None
    rebalance_log: pd.DataFrame | None
    refresh_results: list[dict[str, Any]]
    report_path: str | None = None



def _blocked_result(reason: str, as_of_date: str, data_summary: dict[str, Any] | None = None, assumptions: dict[str, Any] | None = None) -> StrategyComparisonResult:
    return StrategyComparisonResult(
        status="BLOCKED",
        reason=reason,
        as_of_date=as_of_date,
        data_summary=data_summary or {},
        assumptions=assumptions or {},
        legacy_metrics={},
        dynamic_top2_metrics={},
        v42_metrics={},
        comparison={},
        walk_forward={},
        curve=None,
        rebalance_log=None,
        refresh_results=[],
    )

def _normalise(weights: dict[str, float], cash_symbol: str) -> dict[str, float]:
    clean = {key: max(0.0, float(value)) for key, value in weights.items() if float(value) > 1e-12}
    total = sum(clean.values())
    if total <= 1e-12:
        return {cash_symbol: 1.0}
    return {key: value / total for key, value in clean.items()}


def _turnover(current: dict[str, float], target: dict[str, float]) -> float:
    return 0.5 * sum(
        abs(float(current.get(key, 0.0)) - float(target.get(key, 0.0)))
        for key in set(current) | set(target)
    )


def _drift(weights: dict[str, float], factors: dict[str, float], cash_symbol: str) -> tuple[dict[str, float], float]:
    values = {
        symbol: float(weight) * (1.0 if symbol == cash_symbol else float(factors.get(symbol, 1.0)))
        for symbol, weight in weights.items()
    }
    total = float(sum(values.values()))
    if total <= 1e-12:
        return {cash_symbol: 1.0}, 0.0
    return {symbol: value / total for symbol, value in values.items()}, total


def _cost_weight(
    current: dict[str, float],
    target: dict[str, float],
    fee_table: dict[str, FeeAssumption],
    cash_symbol: str,
) -> float:
    value = 0.0
    for symbol in set(current) | set(target):
        if symbol == cash_symbol:
            continue
        delta = float(target.get(symbol, 0.0)) - float(current.get(symbol, 0.0))
        fee = fee_table.get(symbol)
        if fee is None or not fee.usable:
            raise ValueError(f"{symbol} 缺少可用的逐基金研究成本假设")
        if delta > 0:
            value += delta * float(fee.subscription_fee_rate or 0.0)
        elif delta < 0:
            value += abs(delta) * float(fee.redemption_fee_rate or 0.0)
    return float(value)


def _legacy_core_signals(
    instruments: list[FundInstrument],
    nav_frames: dict[str, pd.DataFrame],
    settings: dict[str, Any],
) -> list[Signal]:
    """Direct-NAV reconstruction of the old strategy's observable core.

    It intentionally does not replay ETF proxies or volume hard-vetoes.  Those elements cannot be
    verified on the same direct fund NAV data and would make the comparison unfair.
    """
    cfg = settings["signals"]
    short_window = int(cfg["short_window"])
    medium_window = int(cfg["medium_window"])
    long_window = int(cfg["long_window"])
    output: list[Signal] = []

    for inst in instruments:
        frame = nav_frames.get(inst.symbol)
        if frame is None or len(frame) < long_window:
            output.append(Signal(inst.symbol, inst.name, inst.asset_class, inst.group, False, ["历史不足以计算旧策略核心信号"]))
            continue
        nav = pd.to_numeric(frame["nav"], errors="coerce").astype(float)
        try:
            feature = compute_fund_nav_features(frame, cfg)
        except Exception as exc:
            output.append(Signal(inst.symbol, inst.name, inst.asset_class, inst.group, False, [f"旧策略核心特征计算失败：{exc}"]))
            continue
        r20 = float(nav.iloc[-1] / nav.iloc[-short_window] - 1.0)
        r60 = float(nav.iloc[-1] / nav.iloc[-medium_window] - 1.0)
        ma120 = float(nav.rolling(long_window).mean().iloc[-1])
        dvol = float(feature["downside_vol"])
        fi = float(feature["fragmentation"])
        raw = 0.4 * r20 + 0.6 * r60
        score = float((raw / max(dvol, 1e-6)) * max(0.0, 1.0 - fi))
        reasons: list[str] = []
        if r60 <= float(cfg.get("minimum_absolute_momentum", 0.0)):
            reasons.append("60日绝对动量未达标")
        if float(nav.iloc[-1]) <= ma120:
            reasons.append("累计净值未站上 MA120")
        if not math.isfinite(score) or score <= 0:
            reasons.append("旧策略核心动量得分非正")
        output.append(
            Signal(
                symbol=inst.symbol,
                name=inst.name,
                asset_class=inst.asset_class,
                group=inst.group,
                eligible=not reasons,
                reasons=reasons,
                r20=r20,
                r60=r60,
                r120=float(feature["r120"]),
                downside_vol=dvol,
                fragmentation=fi,
                trend_quality=float(feature["trend_quality"]),
                score=score,
            )
        )
    return output


def _top2_candidates(signals: list[Signal], regime: Regime, *, defensive_only_when_risk_off: bool) -> list[Signal]:
    candidates = [item for item in signals if item.eligible]
    if defensive_only_when_risk_off and regime == Regime.RISK_OFF:
        candidates = [item for item in candidates if item.group in {"defensive", "defensive_equity"}]
    return sorted(candidates, key=lambda item: float(item.score or -math.inf), reverse=True)[:2]


def _scale_to_exposure(weights: dict[str, float], exposure: float, cash_symbol: str) -> dict[str, float]:
    exposure = min(1.0, max(0.0, float(exposure)))
    non_cash = {symbol: weight for symbol, weight in weights.items() if symbol != cash_symbol}
    result = {symbol: weight * exposure for symbol, weight in non_cash.items()}
    result[cash_symbol] = max(0.0, 1.0 - sum(result.values()))
    return _normalise(result, cash_symbol)


def _legacy_top2_target(
    instruments: list[FundInstrument],
    nav_frames: dict[str, pd.DataFrame],
    settings: dict[str, Any],
) -> tuple[dict[str, float], list[Signal], Regime, list[str], str]:
    """Research baseline: Top-2 fully invested unless the rule itself is Risk-Off/no-candidate."""
    cash = str(settings["portfolio"]["cash_symbol"])
    benchmark = nav_frames[str(settings["benchmark_symbol"])]
    regime = fund_nav_regime(benchmark).regime
    signals = _legacy_core_signals(instruments, nav_frames, settings)
    if regime == Regime.RISK_OFF:
        return {cash: 1.0}, signals, regime, [], "RISK_OFF"
    candidates = _top2_candidates(signals, regime, defensive_only_when_risk_off=False)
    if not candidates:
        return {cash: 1.0}, signals, regime, [], "NO_ELIGIBLE_CANDIDATE"
    target = inverse_downside_vol_weights(candidates, max_single_weight=1.0, cash_symbol=cash)
    # Full-invested benchmark: only cash if no eligible candidate.
    return target, signals, regime, [item.symbol for item in candidates], "FULL_EXPOSURE_TOP2"


def _top2_dynamic_target(
    instruments: list[FundInstrument],
    nav_frames: dict[str, pd.DataFrame],
    settings: dict[str, Any],
) -> tuple[dict[str, float], list[Signal], Regime, list[str], str]:
    """Practical Top-2 strong-momentum strategy with explicitly documented dynamic exposure."""
    cfg = settings.get("governance_comparison", {}).get("top2_dynamic_exposure", {})
    cash = str(settings["portfolio"]["cash_symbol"])
    benchmark = nav_frames[str(settings["benchmark_symbol"])]
    regime = fund_nav_regime(benchmark).regime
    signals = _legacy_core_signals(instruments, nav_frames, settings)
    candidates = _top2_candidates(signals, regime, defensive_only_when_risk_off=True)
    if not candidates:
        return {cash: 1.0}, signals, regime, [], "NO_ELIGIBLE_CANDIDATE"
    exposure_key = {
        Regime.RISK_ON: "risk_on_exposure",
        Regime.NEUTRAL: "neutral_exposure",
        Regime.RISK_OFF: "risk_off_exposure",
    }[regime]
    exposure = float(cfg.get(exposure_key, {Regime.RISK_ON: 1.0, Regime.NEUTRAL: 0.75, Regime.RISK_OFF: 0.2}[regime]))
    base = inverse_downside_vol_weights(candidates, max_single_weight=1.0, cash_symbol=cash)
    reason = "FULL_EXPOSURE_TOP2" if exposure >= 0.999 else f"{regime.value}_EXPOSURE_CAP"
    return _scale_to_exposure(base, exposure, cash), signals, regime, [item.symbol for item in candidates], reason


def _v42_multi_asset_cash_target(
    instruments: list[FundInstrument],
    nav_frames: dict[str, pd.DataFrame],
    settings: dict[str, Any],
) -> tuple[dict[str, float], list[Signal], Regime, list[str], str, pd.DataFrame]:
    checks = {symbol: CheckResult(DataStatus.PASS_) for symbol in nav_frames}
    tradeability = {
        "as_of_date": "HISTORICAL_RESEARCH",
        "funds": {item.symbol: {"purchase_status": "OPEN", "redemption_status": "OPEN"} for item in instruments},
    }
    benchmark = nav_frames[str(settings["benchmark_symbol"])]
    regime = fund_nav_regime(benchmark).regime
    signals = build_fund_signals(instruments, nav_frames, checks, settings, tradeability)
    close_frames = nav_frames_as_close(nav_frames)
    selected, corr = select_assets(
        signals=signals,
        frames=close_frames,
        regime=regime,
        max_selected=int(settings["portfolio"]["max_selected_assets"]),
        max_pair_corr=float(settings["portfolio"]["max_pair_correlation"]),
    )
    cash = str(settings["portfolio"]["cash_symbol"])
    instrument_map = {item.symbol: item.to_dict() for item in instruments}
    target = inverse_downside_vol_weights(selected, float(settings["portfolio"]["max_single_weight"]), cash)
    before_caps = dict(target)
    target = cap_single_weights(target, float(settings["portfolio"]["max_single_weight"]), cash)
    target = apply_sector_cap(target, instrument_map, float(settings["portfolio"]["max_sector_weight"]), cash)
    target = apply_regime_caps(target, instrument_map, regime, settings["portfolio"])
    if not selected:
        cash_reason = "NO_ELIGIBLE_CANDIDATE"
    elif regime == Regime.RISK_OFF:
        cash_reason = "RISK_OFF_EXPOSURE_CAP"
    elif regime == Regime.NEUTRAL and target.get(cash, 0.0) > before_caps.get(cash, 0.0) + 1e-12:
        cash_reason = "NEUTRAL_EQUITY_CAP"
    elif target.get(cash, 0.0) > 1e-12:
        cash_reason = "WEIGHT_OR_SECTOR_CAP"
    else:
        cash_reason = "FULLY_INVESTED"
    return target, signals, regime, [item.symbol for item in selected], cash_reason, corr


def _validate_and_prepare(
    instruments: list[FundInstrument],
    frames: dict[str, pd.DataFrame],
    settings: dict[str, Any],
    as_of: pd.Timestamp,
    start: str | None,
    end: str | None,
) -> tuple[dict[str, pd.DataFrame], dict[str, Any], str | None]:
    """Quality-screen series and use exact common dates only among research-eligible funds.

    Exact common dates are a conservative choice for direct fund NAV comparison.  It prevents a
    domestic fund's newer NAV from being compared against an older QDII NAV.  Long-horizon dynamic
    universe support is audited separately; no missing history is backfilled or fabricated here.
    """
    checks: dict[str, CheckResult] = {}
    usable: dict[str, pd.DataFrame] = {}
    excluded: dict[str, list[str]] = {}
    benchmark_symbol = str(settings["benchmark_symbol"])

    for item in instruments:
        frame = frames.get(item.symbol)
        if frame is None or frame.empty:
            checks[item.symbol] = CheckResult(DataStatus.BLOCK, ["无法读取直接累计净值"])
            excluded[item.symbol] = checks[item.symbol].reasons
            continue
        work = frame.copy()
        work["date"] = pd.to_datetime(work["date"], errors="coerce")
        work = work[work["date"] <= as_of].sort_values("date").reset_index(drop=True)
        check = fund_nav_quality_gate(
            frame=work,
            symbol=item.symbol,
            as_of_date=as_of,
            min_history_days=int(settings["minimum_history_days"]),
            max_staleness_calendar_days=int(item.max_staleness_calendar_days or settings["data"]["max_staleness_calendar_days"]),
            max_single_day_return_abs=float(settings["data"]["max_single_day_return_abs"]),
            required_nav_basis=item.nav_basis_required,
            require_direct_symbol_match=bool(settings["data"].get("require_direct_symbol_match", True)),
            quality_window_trading_days=int(item.quality_window_trading_days or settings["data"].get("quality_window_trading_days", settings["minimum_history_days"])),
        )
        checks[item.symbol] = check
        if check.status == DataStatus.BLOCK:
            excluded[item.symbol] = check.reasons
        else:
            usable[item.symbol] = work

    if benchmark_symbol not in usable:
        reason = "基准基金未通过数据质量校验，无法做三套策略的可比回测。"
        return {}, {"checks": {k: v.to_dict() for k, v in checks.items()}, "excluded": excluded, "global_reason": reason}, reason
    included_symbols = [item.symbol for item in instruments if item.symbol in usable]
    if len(included_symbols) < 2:
        reason = "可用于比较的直接基金净值不足两只。"
        return {}, {"checks": {k: v.to_dict() for k, v in checks.items()}, "excluded": excluded, "global_reason": reason}, reason

    indexed = {symbol: frame.set_index("date").sort_index() for symbol, frame in usable.items()}
    common = set.intersection(*(set(frame.index) for frame in indexed.values()))
    dates = sorted(pd.Timestamp(day).normalize() for day in common)
    if start:
        dates = [day for day in dates if day >= pd.Timestamp(start).normalize()]
    if end:
        dates = [day for day in dates if day <= pd.Timestamp(end).normalize()]
    if not dates:
        reason = "指定区间内没有可比较的共同直接净值日期。"
        return {}, {"checks": {k: v.to_dict() for k, v in checks.items()}, "excluded": excluded, "global_reason": reason}, reason

    prepared = {symbol: indexed[symbol].loc[dates].reset_index() for symbol in included_symbols}
    summary = {
        "included_symbols": included_symbols,
        "excluded_symbols": excluded,
        "checks": {key: value.to_dict() for key, value in checks.items()},
        "common_observations": int(len(dates)),
        "common_start_date": str(dates[0].date()),
        "common_end_date": str(dates[-1].date()),
        "benchmark_symbol": benchmark_symbol,
    }
    return prepared, summary, None


def _cash_attribution(curve: pd.DataFrame, prefix: str) -> dict[str, Any]:
    weight_col = f"{prefix}_cash_weight"
    reason_col = f"{prefix}_cash_reason"
    if curve.empty or weight_col not in curve:
        return {}
    view = curve[[weight_col, reason_col]].copy()
    view[reason_col] = view[reason_col].fillna("UNCLASSIFIED")
    denominator = max(float(view[weight_col].sum()), 1e-12)
    grouped = view.groupby(reason_col)[weight_col].sum().sort_values(ascending=False)
    return {
        "average_cash_weight": float(view[weight_col].mean()),
        "cash_weight_share_by_reason": {str(key): float(value / denominator) for key, value in grouped.items()},
        "calendar_days_by_reason": {str(key): int(value) for key, value in view[reason_col].value_counts().items()},
    }


def _strategy_metrics(
    records: list[dict[str, Any]],
    rebalances: list[dict[str, Any]],
    prefix: str,
    strategy_id: str,
    starting_nav: float,
) -> dict[str, Any]:
    curve = pd.DataFrame(records).set_index("date")
    view = pd.DataFrame({"nav": curve[f"{prefix}_nav"]})
    metrics = _performance_metrics(view)
    log = [item for item in rebalances if item["strategy"] == strategy_id]
    total_cost = float(sum(float(item["cost"]) for item in log))
    total_turnover = float(sum(float(item["turnover"]) for item in log))
    metrics.update({
        "executed_rebalances": int(len(log)),
        "total_turnover": total_turnover,
        "total_assumed_cost_nav_units": total_cost,
        "total_assumed_cost_as_starting_nav_pct": total_cost / max(starting_nav, 1e-12),
        "average_cash_weight": float(curve[f"{prefix}_cash_weight"].mean()),
        "ending_nav": float(curve[f"{prefix}_nav"].iloc[-1]),
        "cash_attribution": _cash_attribution(curve.reset_index(), prefix),
    })
    return metrics


def _metric_winner_many(values: dict[str, float | None], *, higher_is_better: bool = True) -> str:
    finite = {key: float(value) for key, value in values.items() if value is not None and np.isfinite(value)}
    if not finite:
        return "无法判断"
    best = max(finite, key=finite.get) if higher_is_better else min(finite, key=finite.get)
    tied = [key for key, value in finite.items() if abs(value - finite[best]) <= 1e-12]
    if len(tied) > 1:
        return "并列"
    return STRATEGY_LABELS[best]


def _comparison_summary(
    legacy: dict[str, Any],
    dynamic: dict[str, Any],
    v42: dict[str, Any],
    maturity: str,
) -> dict[str, Any]:
    pack = {
        "legacy_top2": legacy,
        "top2_dynamic": dynamic,
        "v42_multi_cash": v42,
    }
    return {
        "maturity": maturity,
        "total_return_winner": _metric_winner_many({key: value.get("total_return") for key, value in pack.items()}),
        "annualized_return_winner": _metric_winner_many({key: value.get("annualized_return") for key, value in pack.items()}),
        "max_drawdown_winner": _metric_winner_many({key: value.get("max_drawdown") for key, value in pack.items()}, higher_is_better=True),
        "sortino_winner": _metric_winner_many({key: value.get("sortino") for key, value in pack.items()}),
        "calmar_winner": _metric_winner_many({key: value.get("calmar") for key, value in pack.items()}),
        "lower_turnover_winner": _metric_winner_many({key: value.get("total_turnover") for key, value in pack.items()}, higher_is_better=False),
        "lower_assumed_cost_winner": _metric_winner_many({key: value.get("total_assumed_cost_nav_units") for key, value in pack.items()}, higher_is_better=False),
        "automatic_promotion_allowed": False,
        "promotion_reason": (
            "禁止自动晋级。整体历史和滚动前推只用于治理；仍需足够的真实纸面跟踪、逐基金费用与申赎限制核实。"
            if maturity == "RESEARCH_READY"
            else "共同历史不足以进行策略晋级判断；仅保留探索性对比。"
        ),
    }


def _window_metrics(
    curve: pd.DataFrame,
    rebalances: pd.DataFrame,
    *,
    prefix: str,
    strategy_id: str,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
) -> dict[str, Any]:
    view = curve[(curve["date"] >= start_date) & (curve["date"] <= end_date)].copy()
    if len(view) < 2:
        return {}
    metrics = _performance_metrics(pd.DataFrame({"nav": view[f"{prefix}_nav"].to_numpy()}, index=view["date"]))
    if rebalances.empty:
        logs = rebalances
    else:
        dates = pd.to_datetime(rebalances["execution_nav_date"], errors="coerce")
        logs = rebalances[(rebalances["strategy"] == strategy_id) & (dates > start_date) & (dates <= end_date)]
    metrics["total_turnover"] = float(logs["turnover"].sum()) if not logs.empty else 0.0
    metrics["executed_rebalances"] = int(len(logs))
    return metrics


def _walk_forward_summary(
    curve: pd.DataFrame,
    rebalances: pd.DataFrame,
    settings: dict[str, Any],
) -> dict[str, Any]:
    cfg = settings.get("governance_comparison", {}).get("walk_forward", {})
    train_days = int(cfg.get("train_nav_observations", 252))
    test_days = int(cfg.get("test_nav_observations", 126))
    step_days = int(cfg.get("step_nav_observations", 126))
    min_folds = int(cfg.get("minimum_folds_for_interpretation", 3))
    dates = list(pd.to_datetime(curve["date"]).dt.normalize())
    rows: list[dict[str, Any]] = []
    # Rules are fixed; the preceding train window supplies only the available historical path,
    # not fitted/optimised parameters.  This is a rolling forward stability check, not a claim of
    # machine-learning training.
    for start_idx in range(train_days, len(dates) - test_days + 1, step_days):
        end_idx = start_idx + test_days - 1
        start = dates[start_idx]
        end = dates[end_idx]
        metrics = {
            "legacy_top2": _window_metrics(curve, rebalances, prefix="legacy_top2", strategy_id="legacy_top2", start_date=start, end_date=end),
            "top2_dynamic": _window_metrics(curve, rebalances, prefix="top2_dynamic", strategy_id="top2_dynamic", start_date=start, end_date=end),
            "v42_multi_cash": _window_metrics(curve, rebalances, prefix="v42_multi_cash", strategy_id="v42_multi_cash", start_date=start, end_date=end),
        }
        return_winner = _metric_winner_many({key: item.get("total_return") for key, item in metrics.items()})
        drawdown_winner = _metric_winner_many({key: item.get("max_drawdown") for key, item in metrics.items()}, higher_is_better=True)
        sortino_winner = _metric_winner_many({key: item.get("sortino") for key, item in metrics.items()})
        rows.append({
            "train_window_end": str(dates[start_idx - 1].date()),
            "test_start": str(start.date()),
            "test_end": str(end.date()),
            "legacy_total_return": metrics["legacy_top2"].get("total_return"),
            "dynamic_total_return": metrics["top2_dynamic"].get("total_return"),
            "v42_total_return": metrics["v42_multi_cash"].get("total_return"),
            "legacy_max_drawdown": metrics["legacy_top2"].get("max_drawdown"),
            "dynamic_max_drawdown": metrics["top2_dynamic"].get("max_drawdown"),
            "v42_max_drawdown": metrics["v42_multi_cash"].get("max_drawdown"),
            "total_return_winner": return_winner,
            "max_drawdown_winner": drawdown_winner,
            "sortino_winner": sortino_winner,
        })
    wins = {kind: {} for kind in ["total_return", "max_drawdown", "sortino"]}
    for row in rows:
        for kind in wins:
            label = row[f"{kind}_winner"]
            wins[kind][label] = wins[kind].get(label, 0) + 1
    return {
        "status": "READY" if len(rows) >= min_folds else "INSUFFICIENT_FOLDS",
        "reason": (
            "固定规则的滚动前推稳定性检验已生成；它不使用测试期反向调参。"
            if len(rows) >= min_folds
            else f"仅形成 {len(rows)} 个滚动前推窗口，少于解释门槛 {min_folds}；不据此晋级策略。"
        ),
        "config": {
            "train_nav_observations": train_days,
            "test_nav_observations": test_days,
            "step_nav_observations": step_days,
            "minimum_folds_for_interpretation": min_folds,
        },
        "folds": rows,
        "winner_counts": wins,
    }


def run_fund_strategy_governance_comparison(
    *,
    settings: dict[str, Any],
    instruments: list[FundInstrument],
    frames: dict[str, pd.DataFrame],
    as_of_date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    assumed_subscription_fee: float | None = None,
    assumed_redemption_fee: float | None = None,
    assume_c_class_over_30d: bool = False,
    assumption_note: str = "",
) -> StrategyComparisonResult:
    as_of = pd.Timestamp(as_of_date).normalize() if as_of_date else pd.Timestamp.today().normalize()
    validate_fund_catalog(instruments)
    fee_table, fee_notes, cost_missing = resolve_research_fee_table(
        instruments,
        assume_c_class_over_30d=assume_c_class_over_30d,
        global_subscription_fee=assumed_subscription_fee,
        global_redemption_fee=assumed_redemption_fee,
    )
    gov_cfg = settings.get("governance_comparison", {})
    missing_policy = str(gov_cfg.get("missing_fee_policy", "EXCLUDE_FROM_RESEARCH")).upper()
    eligible_by_cost = [item for item in instruments if fee_table[item.symbol].usable]
    fee_excluded = {symbol: reason for symbol, reason in cost_missing.items() if symbol not in {item.symbol for item in eligible_by_cost}}
    if missing_policy == "BLOCK" and fee_excluded:
        return _blocked_result("逐基金研究费用不完整，且配置要求阻断比较：" + "；".join(fee_excluded.values()), str(as_of.date()))
    if str(settings["benchmark_symbol"]) not in {item.symbol for item in eligible_by_cost}:
        return _blocked_result("基准基金缺少可用研究费用。请配置其费用，或在不改变基准的前提下重新选择研究基准。", str(as_of.date()))
    if len(eligible_by_cost) < 2:
        return _blocked_result("费用可核实的参与基金不足两只，无法进行公平治理对比。", str(as_of.date()))

    prepared, data_summary, failure = _validate_and_prepare(eligible_by_cost, frames, settings, as_of, start_date, end_date)
    coverage = historical_coverage_report(instruments, frames, target_start=str(gov_cfg.get("long_horizon_target_start", "2014-01-01")), target_end=end_date or str(as_of.date()))
    if failure:
        data_summary["historical_coverage"] = coverage
        data_summary["fee_excluded_symbols"] = fee_excluded
        return _blocked_result(failure, str(as_of.date()), data_summary=data_summary)

    active_instruments = [item for item in eligible_by_cost if item.symbol in prepared]
    data_summary["fee_excluded_symbols"] = fee_excluded
    data_summary["historical_coverage"] = coverage
    data_summary["share_classes"] = {item.symbol: item.share_class for item in active_instruments}
    warmup = int(settings["minimum_history_days"])
    min_block = int(gov_cfg.get("minimum_common_days_block", 252))
    min_ready = int(gov_cfg.get("minimum_common_days_research_ready", 504))
    common_days = int(data_summary["common_observations"])
    if common_days < max(min_block, warmup + 3):
        reason = f"共同直接净值日仅 {common_days} 天；至少需要 {max(min_block, warmup + 3)} 天才输出三策略研究对比。"
        return _blocked_result(reason, str(as_of.date()), data_summary=data_summary)

    indexed = {
        symbol: frame.copy().assign(date=pd.to_datetime(frame["date"]).dt.normalize()).set_index("date").sort_index()
        for symbol, frame in prepared.items()
    }
    dates = list(indexed[str(settings["benchmark_symbol"])].index)
    cash = str(settings["portfolio"]["cash_symbol"])
    interval = int(gov_cfg.get("fixed_review_interval_nav_days", settings["scheduled_rebalance_interval_trading_days"]))
    lag = max(1, int(settings["execution"]["order_submission_lag_trading_days"]))
    starting_nav = float(gov_cfg.get("starting_nav", 1.0))
    assumptions = {
        "input_basis": "同基金代码直接累计净值（共同日期对齐）",
        "signal_execution_timing": f"T 日净值信号，T+{lag} 个共同净值日按该日累计净值执行",
        "rebalance_schedule": f"三套策略均使用固定 {interval} 个共同净值观测复核，以隔离策略差异",
        "cash_return_assumption": "0%",
        "fee_mode": "逐基金研究成本；C 类零费率只在用户显式确认时应用，A/LOF 不会继承该假设。",
        "fee_table": {symbol: fee.to_dict() for symbol, fee in fee_table.items()},
        "fee_excluded_symbols": fee_excluded,
        "assumption_note": assumption_note or "策略治理研究假设；不等于历史真实费用审计。",
        "legacy_definition": "旧系统哲学的同基金直接净值重构版：20/60 日动量、下行风险和路径破碎度；不包含 ETF 代理与成交量硬否决。",
        "dynamic_top2_definition": "Top-2 强者恒强 + 市场状态动态仓位；它是实际候选策略，不是永久满仓承诺。",
        "v42_definition": "新版 V4.2 多因子信号 + 最多三只基金 + 相关性/仓位/市场状态现金约束。",
        "not_a_real_world_fee_audit": True,
        "account_mapping_confirmed": bool((settings.get("account_profile") or {}).get("mapping_confirmed", False)),
        "account_mapping_note": str((settings.get("account_profile") or {}).get("note", "")),
    }

    state_template = lambda: {
        "nav": starting_nav,
        "weights": {cash: 1.0},
        "pending": None,
        "last_signal_idx": -interval,
        "previous_signals": None,
        "cash_reason": "WARMUP_CASH",
    }
    states: dict[str, dict[str, Any]] = {
        "legacy_top2": state_template(),
        "top2_dynamic": state_template(),
        "v42_multi_cash": state_template(),
    }
    records: list[dict[str, Any]] = []
    rebalances: list[dict[str, Any]] = []

    for idx, date in enumerate(dates):
        if idx == 0:
            records.append({
                "date": date,
                "legacy_top2_nav": starting_nav,
                "top2_dynamic_nav": starting_nav,
                "v42_multi_cash_nav": starting_nav,
                "benchmark_nav": starting_nav,
                "legacy_top2_cash_weight": 1.0,
                "top2_dynamic_cash_weight": 1.0,
                "v42_multi_cash_cash_weight": 1.0,
                "legacy_top2_cash_reason": "WARMUP_CASH",
                "top2_dynamic_cash_reason": "WARMUP_CASH",
                "v42_multi_cash_cash_reason": "WARMUP_CASH",
            })
            continue

        prev_date = dates[idx - 1]
        factors = {
            symbol: float(indexed[symbol].loc[date, "nav"]) / float(indexed[symbol].loc[prev_date, "nav"])
            for symbol in indexed
        }
        for strategy_id, state in states.items():
            state["weights"], daily_factor = _drift(state["weights"], factors, cash)
            state["nav"] *= daily_factor
            pending = state["pending"]
            if pending is not None and int(pending["execution_idx"]) == idx:
                target = dict(pending["target"])
                turnover = _turnover(state["weights"], target)
                cost_weight = _cost_weight(state["weights"], target, fee_table, cash)
                cost = state["nav"] * cost_weight
                state["nav"] -= cost
                state["weights"] = _normalise(target, cash)
                state["cash_reason"] = str(pending["cash_reason"])
                rebalances.append({
                    "strategy": strategy_id,
                    "strategy_label": STRATEGY_LABELS[strategy_id],
                    "signal_date": str(pd.Timestamp(pending["signal_date"]).date()),
                    "execution_nav_date": str(date.date()),
                    "turnover": turnover,
                    "cost_weight": cost_weight,
                    "cost": cost,
                    "target_weights": json.dumps(target, ensure_ascii=False, sort_keys=True),
                    "selection_note": pending["selection_note"],
                    "cash_reason": pending["cash_reason"],
                })
                state["pending"] = None

        # Signal is only produced from NAV available at end of T; any execution happens later.
        if idx >= warmup and idx + lag < len(dates):
            sliced = {symbol: indexed[symbol].loc[:date].reset_index() for symbol in indexed}

            legacy_target, legacy_signals, legacy_regime, legacy_selected, legacy_reason = _legacy_top2_target(active_instruments, sliced, settings)
            legacy_state = states["legacy_top2"]
            legacy_due = (idx - int(legacy_state["last_signal_idx"])) >= interval
            if legacy_due or legacy_regime == Regime.RISK_OFF:
                legacy_state["pending"] = {
                    "target": legacy_target,
                    "signal_date": date,
                    "execution_idx": idx + lag,
                    "selection_note": f"旧策略核心重构Top-2｜状态={legacy_regime.value}｜候选={','.join(legacy_selected) or 'CASH'}",
                    "cash_reason": legacy_reason,
                }
                legacy_state["last_signal_idx"] = idx

            dynamic_target, dynamic_signals, dynamic_regime, dynamic_selected, dynamic_reason = _top2_dynamic_target(active_instruments, sliced, settings)
            dynamic_state = states["top2_dynamic"]
            dynamic_due = (idx - int(dynamic_state["last_signal_idx"])) >= interval
            if dynamic_due or dynamic_regime == Regime.RISK_OFF:
                dynamic_state["pending"] = {
                    "target": dynamic_target,
                    "signal_date": date,
                    "execution_idx": idx + lag,
                    "selection_note": f"Top-2动态仓位｜状态={dynamic_regime.value}｜候选={','.join(dynamic_selected) or 'CASH'}",
                    "cash_reason": dynamic_reason,
                }
                dynamic_state["last_signal_idx"] = idx

            v42_target, v42_signals, v42_regime, v42_selected, v42_reason, _ = _v42_multi_asset_cash_target(active_instruments, sliced, settings)
            v42_state = states["v42_multi_cash"]
            from .guard import signal_confidence  # avoid import cycle on module load
            confidence, _ = signal_confidence(v42_signals, v42_state["previous_signals"], settings["signals"])
            v42_due = (idx - int(v42_state["last_signal_idx"])) >= interval
            if (v42_due or v42_regime == Regime.RISK_OFF) and (v42_regime == Regime.RISK_OFF or confidence == "STRONG"):
                v42_state["pending"] = {
                    "target": v42_target,
                    "signal_date": date,
                    "execution_idx": idx + lag,
                    "selection_note": f"新版多资产+现金｜状态={v42_regime.value}｜信号={confidence}｜候选={','.join(v42_selected) or 'CASH'}",
                    "cash_reason": v42_reason,
                }
                v42_state["last_signal_idx"] = idx
            v42_state["previous_signals"] = v42_signals

        benchmark_nav = starting_nav * float(indexed[str(settings["benchmark_symbol"])].loc[date, "nav"]) / float(indexed[str(settings["benchmark_symbol"])].loc[dates[0], "nav"])
        records.append({
            "date": date,
            "legacy_top2_nav": float(states["legacy_top2"]["nav"]),
            "top2_dynamic_nav": float(states["top2_dynamic"]["nav"]),
            "v42_multi_cash_nav": float(states["v42_multi_cash"]["nav"]),
            "benchmark_nav": benchmark_nav,
            "legacy_top2_cash_weight": float(states["legacy_top2"]["weights"].get(cash, 0.0)),
            "top2_dynamic_cash_weight": float(states["top2_dynamic"]["weights"].get(cash, 0.0)),
            "v42_multi_cash_cash_weight": float(states["v42_multi_cash"]["weights"].get(cash, 0.0)),
            "legacy_top2_cash_reason": states["legacy_top2"]["cash_reason"],
            "top2_dynamic_cash_reason": states["top2_dynamic"]["cash_reason"],
            "v42_multi_cash_cash_reason": states["v42_multi_cash"]["cash_reason"],
        })

    curve = pd.DataFrame(records)
    rebalance_df = pd.DataFrame(rebalances)
    legacy_metrics = _strategy_metrics(records, rebalances, "legacy_top2", "legacy_top2", starting_nav)
    dynamic_metrics = _strategy_metrics(records, rebalances, "top2_dynamic", "top2_dynamic", starting_nav)
    v42_metrics = _strategy_metrics(records, rebalances, "v42_multi_cash", "v42_multi_cash", starting_nav)
    maturity = "RESEARCH_READY" if common_days >= min_ready else "EXPLORATORY_ONLY"
    comparison = _comparison_summary(legacy_metrics, dynamic_metrics, v42_metrics, maturity)
    walk_forward = _walk_forward_summary(curve, rebalance_df, settings)
    reason = (
        "已完成同资产、同日期、同执行时点、逐基金成本假设下的三策略治理对比。"
        if maturity == "RESEARCH_READY"
        else "共同历史达到最低研究门槛，但不足 504 个共同净值观测；仅允许探索性对比，禁止以此晋级主策略。"
    )
    return StrategyComparisonResult(
        maturity,
        reason,
        str(as_of.date()),
        data_summary,
        assumptions,
        legacy_metrics,
        dynamic_metrics,
        v42_metrics,
        comparison,
        walk_forward,
        curve,
        rebalance_df,
        [],
    )


def _pct(value: Any) -> str:
    try:
        return f"{float(value):.2%}"
    except Exception:
        return "N/A"


def _ratio(value: Any) -> str:
    try:
        return f"{float(value):.3f}"
    except Exception:
        return "N/A"


def _cash_lines(metrics: dict[str, Any]) -> list[str]:
    attribution = metrics.get("cash_attribution", {}) or {}
    details = attribution.get("cash_weight_share_by_reason", {}) or {}
    if not details:
        return ["- 无现金归因记录。"]
    return [f"- {reason}: 占该策略累计现金暴露 {_pct(share)}" for reason, share in details.items()]


def write_fund_strategy_comparison_report(result: StrategyComparisonResult) -> StrategyComparisonResult:
    """Persist transparent reports, curves, rebalances, and rolling-forward windows."""
    output = project_path("output", "fund_governance")
    output.mkdir(parents=True, exist_ok=True)
    safe_date = result.as_of_date
    json_path = output / f"strategy_comparison_{safe_date}.json"
    md_path = output / f"strategy_comparison_{safe_date}.md"
    curve_path = output / f"strategy_comparison_curve_{safe_date}.csv"
    log_path = output / f"strategy_comparison_rebalances_{safe_date}.csv"
    wf_path = output / f"strategy_comparison_walk_forward_{safe_date}.csv"

    payload = {
        "status": result.status,
        "reason": result.reason,
        "as_of_date": result.as_of_date,
        "data_summary": result.data_summary,
        "assumptions": result.assumptions,
        "legacy_top2_reconstructed_metrics": result.legacy_metrics,
        "top2_dynamic_exposure_metrics": result.dynamic_top2_metrics,
        "v42_multi_cash_metrics": result.v42_metrics,
        "comparison": result.comparison,
        "walk_forward": result.walk_forward,
        "refresh_results": result.refresh_results,
        "artifacts": {"curve": str(curve_path), "rebalances": str(log_path), "walk_forward": str(wf_path)},
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if result.curve is not None:
        result.curve.to_csv(curve_path, index=False, encoding="utf-8-sig")
    if result.rebalance_log is not None:
        result.rebalance_log.to_csv(log_path, index=False, encoding="utf-8-sig")
    pd.DataFrame(result.walk_forward.get("folds", [])).to_csv(wf_path, index=False, encoding="utf-8-sig")

    metrics_pack = [
        ("旧策略核心重构 Top-2", result.legacy_metrics),
        ("Top-2 动态仓位", result.dynamic_top2_metrics),
        ("新版多资产+现金", result.v42_metrics),
    ]
    lines = [
        f"# V4.5.1 策略治理对比报告｜{safe_date}",
        "",
        f"- 状态：**{result.status}**",
        f"- 结论：{result.reason}",
        "- 本报告只做研究比较，不生成申购、赎回或调仓计划，也不会改变 Champion。",
        "",
        "## 这次实际比较了什么",
        "- **旧策略核心重构 Top-2**：用同基金直接累计净值重构旧系统可观察的 20/60 日动量、下行风险、路径破碎度与 Top-2 核心；不等于直接调用旧 `Strategy_Engine.py`。",
        "- **Top-2 动态仓位**：Top-2 强者恒强，但 Risk-On / Neutral / Risk-Off 分别采用配置文件中的 100% / 75% / 20% 默认研究暴露；这才是实际候选主策略。",
        "- **新版多资产+现金**：V4.2 多周期趋势 + 趋势质量 + 同质化相关性约束 + 最多三只基金 + 市场状态现金上限。",
        "- 三者均使用同基金直接净值；旧工程的 ETF 代理和成交量硬否决不进入比较。",
        "",
        "## 共同数据与十年研究资格",
        f"- 共同净值观测：**{result.data_summary.get('common_observations', 'N/A')}**",
        f"- 共同区间：{result.data_summary.get('common_start_date', 'N/A')} 至 {result.data_summary.get('common_end_date', 'N/A')}",
        f"- 参与基金：{', '.join(result.data_summary.get('included_symbols', [])) or 'N/A'}",
    ]
    coverage = result.data_summary.get("historical_coverage", {}) or {}
    lines.extend([
        f"- 十年研究资格：**{'具备' if coverage.get('ten_year_research_ready') else '不具备'}**",
        f"- 说明：{coverage.get('reason', 'N/A')}",
    ])
    excluded = result.data_summary.get("excluded_symbols", {}) or {}
    if excluded:
        lines.append("- 数据质量隔离：" + "；".join(f"{symbol}（{'；'.join(reasons)}）" for symbol, reasons in excluded.items()))
    fee_excluded = result.data_summary.get("fee_excluded_symbols", {}) or {}
    if fee_excluded:
        lines.append("- 因研究成本未配置而透明排除：" + "；".join(fee_excluded.values()))

    lines.extend(["", "## 逐基金成本与份额类别"])
    for symbol in result.data_summary.get("included_symbols", []):
        fee = (result.assumptions.get("fee_table", {}) or {}).get(symbol, {})
        lines.append(
            f"- {symbol}｜份额 {fee.get('share_class', 'N/A')}｜申购 {_pct(fee.get('subscription_fee_rate'))}｜赎回 {_pct(fee.get('redemption_fee_rate'))}｜模式 {fee.get('mode', 'N/A')}"
        )
    lines.extend([
        "- 重要：C 类零成本仅在用户显式确认长期持有研究假设时使用；A / LOF / QDII 不会自动继承。",
        "",
        "## 整体结果（净值已扣除上述逐基金研究成本）",
        "| 指标 | 旧策略核心重构 Top-2 | Top-2 动态仓位 | 新版多资产+现金 | 更优者 |",
        "|---|---:|---:|---:|---|",
        f"| 总收益 | {_pct(result.legacy_metrics.get('total_return'))} | {_pct(result.dynamic_top2_metrics.get('total_return'))} | {_pct(result.v42_metrics.get('total_return'))} | {result.comparison.get('total_return_winner', 'N/A')} |",
        f"| 年化收益 | {_pct(result.legacy_metrics.get('annualized_return'))} | {_pct(result.dynamic_top2_metrics.get('annualized_return'))} | {_pct(result.v42_metrics.get('annualized_return'))} | {result.comparison.get('annualized_return_winner', 'N/A')} |",
        f"| 最大回撤 | {_pct(result.legacy_metrics.get('max_drawdown'))} | {_pct(result.dynamic_top2_metrics.get('max_drawdown'))} | {_pct(result.v42_metrics.get('max_drawdown'))} | {result.comparison.get('max_drawdown_winner', 'N/A')} |",
        f"| Sortino | {_ratio(result.legacy_metrics.get('sortino'))} | {_ratio(result.dynamic_top2_metrics.get('sortino'))} | {_ratio(result.v42_metrics.get('sortino'))} | {result.comparison.get('sortino_winner', 'N/A')} |",
        f"| Calmar | {_ratio(result.legacy_metrics.get('calmar'))} | {_ratio(result.dynamic_top2_metrics.get('calmar'))} | {_ratio(result.v42_metrics.get('calmar'))} | {result.comparison.get('calmar_winner', 'N/A')} |",
        f"| 调仓次数 | {result.legacy_metrics.get('executed_rebalances', 'N/A')} | {result.dynamic_top2_metrics.get('executed_rebalances', 'N/A')} | {result.v42_metrics.get('executed_rebalances', 'N/A')} | — |",
        f"| 累计换手 | {_pct(result.legacy_metrics.get('total_turnover'))} | {_pct(result.dynamic_top2_metrics.get('total_turnover'))} | {_pct(result.v42_metrics.get('total_turnover'))} | {result.comparison.get('lower_turnover_winner', 'N/A')} |",
        f"| 平均现金权重 | {_pct(result.legacy_metrics.get('average_cash_weight'))} | {_pct(result.dynamic_top2_metrics.get('average_cash_weight'))} | {_pct(result.v42_metrics.get('average_cash_weight'))} | — |",
        "",
        "## 现金从哪里来",
    ])
    for label, metrics in metrics_pack:
        lines.append(f"### {label}")
        lines.extend(_cash_lines(metrics))
    lines.extend(["", "## 滚动前推稳定性检验（固定规则，不反向调参）"])
    wf = result.walk_forward or {}
    lines.extend([
        f"- 状态：**{wf.get('status', 'N/A')}**",
        f"- 说明：{wf.get('reason', 'N/A')}",
        f"- 窗口：训练观察 {wf.get('config', {}).get('train_nav_observations', 'N/A')}，测试观察 {wf.get('config', {}).get('test_nav_observations', 'N/A')}，步长 {wf.get('config', {}).get('step_nav_observations', 'N/A')}",
    ])
    if wf.get("folds"):
        lines.extend([
            "| 测试期 | 总收益更优 | 最大回撤更优 | Sortino 更优 |",
            "|---|---|---|---|",
        ])
        for fold in wf["folds"]:
            lines.append(f"| {fold['test_start']} 至 {fold['test_end']} | {fold['total_return_winner']} | {fold['max_drawdown_winner']} | {fold['sortino_winner']} |")
    lines.extend([
        "",
        "## 如何读本报告",
        "- 三策略结果不同，不代表任何一条会在未来必然获胜。",
        "- 旧 Top-2 重构版是进攻基准；Top-2 动态仓位是你更贴近低频真实操作的候选；多资产+现金是稳健 Challenger。",
        "- ‘有 Cash 仍收益更高’在数学上可能成立，但必须先看现金来源、滚动窗口和费用假设，不能只看一条总收益。",
        f"- 治理结论：{result.comparison.get('promotion_reason', '禁止自动晋级。')}",
        "",
        "## 输出文件",
        f"- 净值曲线：`{curve_path.name}`",
        f"- 调仓审计：`{log_path.name}`",
        f"- 滚动前推窗口：`{wf_path.name}`",
        "",
        "## 安全边界",
        "- 不使用 ETF 代理、不使用成交量/OHLCV，也不把场外基金净值当盘中价格。",
        "- T 日信号只能在后续共同净值日执行；不使用未来净值。",
        "- 不连接基金销售平台，不生成或发送真实申赎订单。",
    ])
    md_path.write_text("\n".join(lines), encoding="utf-8")
    result.report_path = str(md_path)
    return result


def refresh_and_run_strategy_comparison(
    *,
    as_of_date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    assumed_subscription_fee: float | None = None,
    assumed_redemption_fee: float | None = None,
    assume_c_class_over_30d: bool = False,
    assumption_note: str = "",
) -> StrategyComparisonResult:
    from .config import load_json

    settings = load_json("config/fund/settings.json")
    instruments = [FundInstrument.from_dict(item) for item in load_json("config/fund/instruments.json")]
    validate_fund_catalog(instruments)
    as_of = pd.Timestamp(as_of_date).normalize() if as_of_date else pd.Timestamp.today().normalize()
    repo = FundNAVRepository()
    refresh = auto_refresh_direct_fund_navs(
        symbols=[item.symbol for item in instruments],
        data_config=settings["data"],
        as_of_date=as_of,
        repository=repo,
    )
    frames: dict[str, pd.DataFrame] = {}
    for item in instruments:
        try:
            frames[item.symbol] = repo.load(item.symbol)
        except Exception:
            frames[item.symbol] = pd.DataFrame()
    result = run_fund_strategy_governance_comparison(
        settings=settings,
        instruments=instruments,
        frames=frames,
        as_of_date=str(as_of.date()),
        start_date=start_date,
        end_date=end_date,
        assumed_subscription_fee=assumed_subscription_fee,
        assumed_redemption_fee=assumed_redemption_fee,
        assume_c_class_over_30d=assume_c_class_over_30d,
        assumption_note=assumption_note,
    )
    result.refresh_results = [item.to_dict() for item in refresh]
    return write_fund_strategy_comparison_report(result)
