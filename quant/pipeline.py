from __future__ import annotations

from pathlib import Path

import pandas as pd

from .config import load_json, project_path
from .datahub import DataRepository, quality_gate
from .execution import build_trade_plan, load_state, write_trade_plan
from .governance import append_decision_log, governance_status
from .guard import GuardOutcome, rebalance_guard, signal_confidence, stage_target_weights, trading_days_since, validate_state
from .models import Decision, DecisionStatus, Regime
from .portfolio import apply_regime_caps, apply_sector_cap, cap_single_weights, hrp_weights, inverse_downside_vol_weights, select_assets
from .regime import core_regime, hmm_shadow
from .reporting import write_decision_report
from .universe import Instrument, build_signals, validate_catalog


def _previous_day_signals(instruments, frames, checks, settings):
    previous_frames = {symbol: frame.iloc[:-1].copy() for symbol, frame in frames.items() if len(frame) > 1}
    if any(len(frame) < settings["minimum_history_days"] for frame in previous_frames.values()):
        return None
    return build_signals(instruments, previous_frames, checks, settings)


def run_daily(as_of_date: str | None = None) -> Decision:
    settings = load_json("config/settings.json")
    raw_instruments = load_json("config/instruments.json")
    instruments = [Instrument.from_dict(item) for item in raw_instruments]
    validate_catalog(instruments)
    instrument_map = {item.symbol: item.__dict__ for item in instruments}

    as_of = pd.Timestamp(as_of_date).normalize() if as_of_date else pd.Timestamp.today().normalize()
    repository = DataRepository()
    frames = {}
    checks = {}
    for inst in instruments:
        frame = repository.load(inst.symbol)
        frame = frame[frame["date"] <= as_of].copy()
        frames[inst.symbol] = frame
        checks[inst.symbol] = quality_gate(
            frame=frame,
            symbol=inst.symbol,
            as_of_date=as_of,
            min_history_days=int(settings["minimum_history_days"]),
            max_staleness_trading_days=int(settings["data"]["max_staleness_trading_days"]),
            require_ohlcv=bool(settings["data"]["require_ohlcv"]),
            max_single_day_return_abs=float(settings["data"]["max_single_day_return_abs"]),
        )

    benchmark = frames[settings["benchmark_symbol"]]
    regime = core_regime(benchmark, settings["regime"])
    regime.hmm_shadow = hmm_shadow(benchmark, int(settings["regime"]["hmm_min_history_days"]))

    signals = build_signals(instruments, frames, checks, settings)
    previous_signals = _previous_day_signals(instruments, frames, checks, settings)
    confidence, confidence_reasons = signal_confidence(signals, previous_signals, settings["signals"])

    selected, corr = select_assets(
        signals=signals,
        frames=frames,
        regime=regime.regime,
        max_selected=int(settings["portfolio"]["max_selected_assets"]),
        max_pair_corr=float(settings["portfolio"]["max_pair_correlation"]),
    )
    cash = settings["portfolio"]["cash_symbol"]
    champion = inverse_downside_vol_weights(selected, float(settings["portfolio"]["max_single_weight"]), cash)
    champion = cap_single_weights(champion, float(settings["portfolio"]["max_single_weight"]), cash)
    champion = apply_sector_cap(champion, instrument_map, float(settings["portfolio"]["max_sector_weight"]), cash)
    champion = apply_regime_caps(champion, instrument_map, regime.regime, settings["portfolio"])
    challenger = hrp_weights(frames, selected, cash)
    challenger = cap_single_weights(challenger, float(settings["portfolio"]["max_single_weight"]), cash)
    challenger = apply_sector_cap(challenger, instrument_map, float(settings["portfolio"]["max_sector_weight"]), cash)
    challenger = apply_regime_caps(challenger, instrument_map, regime.regime, settings["portfolio"])

    state = load_state()
    current_weights = {key: float(value) for key, value in state.get("current_weights", {}).items()}
    state_issues = validate_state(state, set(instrument_map), cash)
    elapsed = trading_days_since(state.get("last_executed_trade_date"), as_of, benchmark["date"])
    scheduled_due = elapsed is None or elapsed >= int(settings["scheduled_rebalance_interval_trading_days"])
    risk_override = regime.regime == Regime.RISK_OFF and sum(current_weights.get(symbol, 0.0) for symbol, meta in instrument_map.items() if meta["group"] == "equity") > float(settings["portfolio"]["risk_off_equity_cap"])

    guard = rebalance_guard(
        data_checks=checks,
        state_issues=state_issues,
        scheduled_due=scheduled_due,
        risk_override=risk_override,
        confidence=confidence,
        confidence_reasons=confidence_reasons,
        current_weights=current_weights,
        target_weights=champion,
        max_non_emergency_turnover=float(settings["execution"]["max_non_emergency_turnover"]),
    )

    actionable_target = champion
    if guard.requires_staging:
        actionable_target = stage_target_weights(
            current_weights, champion, float(settings["execution"]["max_non_emergency_turnover"])
        )
    # 换手率必须先于容忍带检查计算。此前该赋值位于检查之后，导致 ETF 分支一旦进入
    # REBALANCE_CANDIDATE / RISK_OVERRIDE 就抛 UnboundLocalError —— 即零换手容忍带
    # 检查从未真正生效（对应 GAP-6 / REQ-LEGACY-03）。顺序与基金侧 fund_pipeline 对齐。
    actionable_turnover = guard.turnover if not guard.requires_staging else float(settings["execution"]["max_non_emergency_turnover"])

    # V46-0105: 零换手容忍带检查（ETF分支）。容忍带只在数据/周期/信号守卫之后生效，
    # 不能覆盖数据阻断。
    tolerance_band = float(settings["execution"]["tolerance_band"])
    if guard.status in {DecisionStatus.REBALANCE_CANDIDATE, DecisionStatus.RISK_OVERRIDE} and actionable_turnover <= tolerance_band:
        guard = GuardOutcome(
            DecisionStatus.HOLD,
            ["目标与当前持仓偏离未超过容忍带；不生成交易计划"],
            guard.confidence,
            actionable_turnover,
        )

    estimated_cost = actionable_turnover * float(settings["execution"]["estimated_one_way_cost"])
    decision = Decision(
        as_of_date=str(as_of.date()),
        status=guard.status,
        reasons=guard.reasons,
        regime=regime,
        data_checks=checks,
        signals=signals,
        target_weights=actionable_target,
        strategic_target_weights=champion,
        champion_weights=champion,
        challenger_weights=challenger,
        confidence=guard.confidence,
        estimated_turnover=actionable_turnover,
        estimated_cost=estimated_cost,
    )

    if decision.status in {DecisionStatus.REBALANCE_CANDIDATE, DecisionStatus.RISK_OVERRIDE}:
        plan = build_trade_plan(current_weights, actionable_target, decision.as_of_date, float(settings["execution"]["estimated_one_way_cost"]))
        decision.trade_plan_path = str(write_trade_plan(plan, decision.as_of_date).relative_to(project_path()))

    payload = decision.to_dict()
    payload["governance"] = governance_status(settings)
    payload["selected_symbols"] = [item.symbol for item in selected]
    payload["correlation_matrix"] = corr.round(4).to_dict() if not corr.empty else {}
    append_decision_log(payload)
    write_decision_report(payload, decision.as_of_date)
    return decision
