from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from .calendar_groups import CalendarGroupError, normalize_calendar_group
from .config import load_json, project_path
from .fund_datahub import (
    FundNAVRepository,
    auto_refresh_direct_fund_navs,
    fund_nav_quality_gate,
    summarize_fund_refresh,
)
from .fund_execution import (
    build_fund_trade_plan,
    load_fund_state,
    load_tradeability_snapshot,
    validate_fund_execution_constraints,
    validate_fund_state,
    write_fund_trade_plan,
)
from .fund_features import nav_frames_as_close
from .fund_governance import append_fund_decision_log, fund_governance_status
from .fund_regime import fund_hmm_shadow, fund_nav_regime
from .fund_reporting import write_fund_decision_report
from .fund_schedule import estimate_next_review, review_interval_nav_observations
from .fund_universe import FundInstrument, build_fund_signals, validate_fund_catalog
from .guard import GuardOutcome, rebalance_guard, signal_confidence, stage_target_weights, trading_days_since, weights_turnover
from .models import CheckResult, DataStatus, Decision, DecisionStatus, Regime, RegimeResult
from .portfolio import apply_regime_caps, apply_sector_cap, cap_single_weights, hrp_weights, inverse_downside_vol_weights, normalize_weights, select_assets


def _blocked_check(reason: str) -> CheckResult:
    return CheckResult(DataStatus.BLOCK, [reason], {})



def classify_fund_quality_failures(
    instruments: list[FundInstrument],
    checks: dict[str, CheckResult],
    benchmark_symbol: str,
) -> tuple[set[str], set[str]]:
    """Split failed data into global blockers and quarantined instruments.

    A failed benchmark can invalidate the regime decision, so it remains a global block.  A failed
    non-benchmark fund is not allowed to enter rankings or receive new money, but it should not
    erase valid direct-NAV signals from the rest of the universe.
    """
    global_blocked: set[str] = set()
    quarantined: set[str] = set()
    for item in instruments:
        check = checks.get(item.symbol)
        if check is None or check.status != DataStatus.BLOCK:
            continue
        if item.symbol == benchmark_symbol or item.quality_failure_policy == "BLOCK_GLOBAL":
            global_blocked.add(item.symbol)
        else:
            quarantined.add(item.symbol)
    return global_blocked, quarantined


@dataclass(frozen=True)
class CalendarGroupAlignment:
    """Per-calendar-group signal dates and the resulting cross-group age spread.

    Replaces the single "common signal date": there is no honest scalar answer once the
    portfolio spans calendars that disclose NAV on different days.
    """

    signal_dates: dict[str, str]
    """Calendar group -> the group's own common signal date (ISO)."""

    symbols: dict[str, tuple[str, ...]]
    """Calendar group -> the usable symbols aligned inside that group."""

    signal_age_days: dict[str, int]
    """Calendar group -> calendar days behind the freshest group."""

    lag_threshold_calendar_days: int
    """Configured tolerance for the cross-group age spread."""

    stale_groups: tuple[str, ...]
    """Groups whose age exceeds the tolerance; their symbols must be degraded, not trusted."""

    @property
    def latest_signal_date(self) -> str | None:
        """The freshest group signal date.  Used as a schedule anchor, not as a global truth."""
        return max(self.signal_dates.values()) if self.signal_dates else None

    @property
    def max_cross_group_lag_days(self) -> int:
        return max(self.signal_age_days.values()) if self.signal_age_days else 0

    @property
    def stale_symbols(self) -> tuple[str, ...]:
        return tuple(sorted(s for group in self.stale_groups for s in self.symbols.get(group, ())))

    def describe(self) -> list[str]:
        lines = [
            f"{group} 组信号日 {self.signal_dates[group]}"
            + (
                "（最新组）"
                if self.signal_age_days[group] == 0
                else f"（较最新组滞后 {self.signal_age_days[group]} 个自然日）"
            )
            for group in sorted(self.signal_dates)
        ]
        for group in self.stale_groups:
            lines.append(
                f"{group} 组滞后 {self.signal_age_days[group]} 个自然日，超过跨组滞后上限 "
                f"{self.lag_threshold_calendar_days} 日：该组本轮降级隔离，不参与新增申购"
            )
        return lines

    def to_dict(self) -> dict[str, Any]:
        return {
            "convention": "LATEST_PER_GROUP",
            "signal_dates": dict(self.signal_dates),
            "symbols": {group: list(items) for group, items in self.symbols.items()},
            "signal_age_days": dict(self.signal_age_days),
            "max_cross_group_lag_calendar_days": self.max_cross_group_lag_days,
            "lag_threshold_calendar_days": self.lag_threshold_calendar_days,
            "stale_groups": list(self.stale_groups),
            "latest_signal_date": self.latest_signal_date,
        }


def align_fund_frames_by_calendar_group(
    frames: dict[str, pd.DataFrame],
    checks: dict[str, CheckResult],
    calendar_groups: dict[str, str],
    max_cross_group_lag_calendar_days: int,
) -> tuple[dict[str, pd.DataFrame], CalendarGroupAlignment | None]:
    """Align usable direct-NAV series inside each calendar group, never across groups.

    Funds sharing a calendar are truncated to that calendar's own common date, so a series
    that is briefly ahead of its peers cannot be compared against a stale one.  Groups are
    kept independent: a QDII fund whose NAV is structurally one or two days late must not
    drag every A-share fund back to that date (docs/CALENDAR_GROUPS.md §2.2 R3).

    Quarantined/blocked series stay out of every group's date intersection, and the
    remaining cross-group age spread is reported rather than averaged away.
    """
    usable = {
        symbol: frame.copy()
        for symbol, frame in frames.items()
        if checks.get(symbol) is not None
        and checks[symbol].status != DataStatus.BLOCK
        and frame is not None
        and not frame.empty
        and "date" in frame.columns
    }
    if not usable:
        return frames, None

    members: dict[str, list[str]] = {}
    for symbol in usable:
        group = calendar_groups.get(symbol)
        if group is None:
            raise CalendarGroupError(
                f"{symbol} 缺少日历分组，无法判定其信号日期口径；按 Fail-Closed 原则拒绝对齐"
            )
        members.setdefault(normalize_calendar_group(group), []).append(symbol)

    group_dates: dict[str, pd.Timestamp] = {}
    for group, symbols in members.items():
        # Intra-group intersection only.  Its scope is one calendar, which is exactly what
        # makes it a legitimate comparison date.
        group_dates[group] = min(
            pd.to_datetime(usable[symbol]["date"]).max().normalize() for symbol in symbols
        )

    freshest = max(group_dates.values())
    ages = {group: int((freshest - date).days) for group, date in group_dates.items()}
    threshold = int(max_cross_group_lag_calendar_days)
    stale = tuple(sorted(group for group, age in ages.items() if age > threshold))

    aligned = dict(frames)
    for group, symbols in members.items():
        cutoff = group_dates[group]
        for symbol in symbols:
            frame = usable[symbol]
            aligned[symbol] = (
                frame[pd.to_datetime(frame["date"]) <= cutoff].copy().reset_index(drop=True)
            )

    alignment = CalendarGroupAlignment(
        signal_dates={group: str(date.date()) for group, date in group_dates.items()},
        symbols={group: tuple(sorted(symbols)) for group, symbols in members.items()},
        signal_age_days=ages,
        lag_threshold_calendar_days=threshold,
        stale_groups=stale,
    )
    return aligned, alignment


def freeze_quarantined_fund_holdings(
    current_weights: dict[str, float],
    proposed_target: dict[str, float],
    quarantined_symbols: set[str],
    cash_symbol: str,
) -> tuple[dict[str, float], dict[str, float]]:
    """Keep failed-data holdings unchanged and prohibit both buy and redeem instructions.

    The remaining target is rescaled over valid instruments and cash.  This is a containment rule,
    not an attempt to infer a price or a sell decision from bad data.
    """
    frozen = {
        symbol: float(current_weights.get(symbol, 0.0))
        for symbol in quarantined_symbols
        if float(current_weights.get(symbol, 0.0)) > 1e-12
    }
    locked_weight = sum(frozen.values())
    if locked_weight >= 1.0 - 1e-12:
        return normalize_weights(frozen), frozen

    free_target = {
        symbol: float(weight)
        for symbol, weight in proposed_target.items()
        if symbol not in quarantined_symbols and float(weight) > 1e-12
    }
    if not free_target:
        free_target = {cash_symbol: 1.0}
    free_total = sum(free_target.values())
    residual = max(0.0, 1.0 - locked_weight)
    result = dict(frozen)
    for symbol, weight in free_target.items():
        result[symbol] = result.get(symbol, 0.0) + residual * weight / free_total
    return normalize_weights(result), frozen

def _previous_day_fund_signals(
    instruments: list[FundInstrument],
    frames: dict[str, pd.DataFrame],
    checks: dict[str, CheckResult],
    settings: dict[str, Any],
    tradeability: dict[str, Any],
):
    previous = {symbol: frame.iloc[:-1].copy() for symbol, frame in frames.items() if frame is not None and len(frame) > 1}
    if set(previous) != {item.symbol for item in instruments}:
        return None
    if any(len(frame) < int(settings["minimum_history_days"]) for frame in previous.values()):
        return None
    return build_fund_signals(instruments, previous, checks, settings, tradeability)


def run_fund_daily(as_of_date: str | None = None, refresh_symbols: list[str] | None = None) -> Decision:
    settings = load_json("config/fund/settings.json")
    instruments = [FundInstrument.from_dict(item) for item in load_json("config/fund/instruments.json")]
    validate_fund_catalog(instruments)
    instrument_map = {item.symbol: item.__dict__ for item in instruments}
    as_of = pd.Timestamp(as_of_date).normalize() if as_of_date else pd.Timestamp.today().normalize()

    repo = FundNAVRepository()
    refresh_results = auto_refresh_direct_fund_navs(
        symbols=[item.symbol for item in instruments] + list(refresh_symbols or []),
        data_config=settings["data"],
        as_of_date=as_of,
        repository=repo,
    )
    refresh_notes = summarize_fund_refresh(refresh_results)
    frames: dict[str, pd.DataFrame] = {}
    checks: dict[str, CheckResult] = {}
    for item in instruments:
        try:
            frame = repo.load(item.symbol)
            frame = frame[frame["date"] <= as_of].copy()
            frames[item.symbol] = frame
            checks[item.symbol] = fund_nav_quality_gate(
                frame=frame,
                symbol=item.symbol,
                as_of_date=as_of,
                min_history_days=int(settings["minimum_history_days"]),
                max_staleness_calendar_days=int(
                    item.max_staleness_calendar_days
                    if item.max_staleness_calendar_days is not None
                    else settings["data"]["max_staleness_calendar_days"]
                ),
                max_single_day_return_abs=float(settings["data"]["max_single_day_return_abs"]),
                required_nav_basis=item.nav_basis_required,
                require_direct_symbol_match=bool(settings["data"].get("require_direct_symbol_match", True)),
                quality_window_trading_days=int(
                    item.quality_window_trading_days
                    if item.quality_window_trading_days is not None
                    else settings["data"].get("quality_window_trading_days", settings["minimum_history_days"])
                ),
            )
        except Exception as exc:
            frames[item.symbol] = pd.DataFrame(columns=["date", "nav"])
            checks[item.symbol] = _blocked_check(f"无法读取 {item.symbol} 的直接场外基金净值：{exc}")

    benchmark_symbol = str(settings["benchmark_symbol"])
    global_blocked_symbols, quarantined_symbols = classify_fund_quality_failures(
        instruments, checks, benchmark_symbol
    )
    calendar_groups = {item.symbol: item.calendar_group for item in instruments}
    analysis_frames, alignment = align_fund_frames_by_calendar_group(
        frames,
        checks,
        calendar_groups,
        max_cross_group_lag_calendar_days=int(
            settings["data"].get(
                "max_cross_group_signal_lag_calendar_days",
                settings["data"]["max_staleness_calendar_days"],
            )
        ),
    )
    common_signal_date = alignment.latest_signal_date if alignment is not None else None

    # A group that has fallen too far behind the freshest group is no longer comparable with it.
    # Its funds are frozen rather than ranked; only the benchmark's own group can block globally.
    stale_group_symbols: set[str] = set()
    if alignment is not None and alignment.stale_groups:
        for symbol in alignment.stale_symbols:
            item = next((candidate for candidate in instruments if candidate.symbol == symbol), None)
            if symbol == benchmark_symbol or (item is not None and item.quality_failure_policy == "BLOCK_GLOBAL"):
                checks[symbol] = _blocked_check(
                    f"{symbol} 所属 {calendar_groups[symbol]} 组信号日落后最新日历组 "
                    f"{alignment.signal_age_days[calendar_groups[symbol]]} 个自然日，超过跨组滞后上限"
                )
                global_blocked_symbols.add(symbol)
            else:
                stale_group_symbols.add(symbol)
                quarantined_symbols.add(symbol)

    benchmark = analysis_frames.get(benchmark_symbol, pd.DataFrame())
    if benchmark.empty or benchmark_symbol in global_blocked_symbols:
        regime = RegimeResult(Regime.NEUTRAL, ["基准场外基金净值不可用或未通过质量校验；不强行生成市场状态"], {})
        regime.hmm_shadow = {"status": "UNAVAILABLE", "reason": "基准净值不可用或被质量闸门阻断"}
    else:
        regime = fund_nav_regime(benchmark)
        regime.hmm_shadow = fund_hmm_shadow(benchmark, int(settings["regime"]["hmm_min_history_days"]))

    tradeability = load_tradeability_snapshot()
    signals = build_fund_signals(instruments, analysis_frames, checks, settings, tradeability)
    previous_signals = _previous_day_fund_signals(instruments, analysis_frames, checks, settings, tradeability)
    confidence, confidence_reasons = signal_confidence(signals, previous_signals, settings["signals"])

    close_frames = nav_frames_as_close(analysis_frames)
    selected, corr = select_assets(
        signals=signals,
        frames=close_frames,
        regime=regime.regime,
        max_selected=int(settings["portfolio"]["max_selected_assets"]),
        max_pair_corr=float(settings["portfolio"]["max_pair_correlation"]),
    )
    cash = settings["portfolio"]["cash_symbol"]
    champion = inverse_downside_vol_weights(selected, float(settings["portfolio"]["max_single_weight"]), cash)
    champion = cap_single_weights(champion, float(settings["portfolio"]["max_single_weight"]), cash)
    champion = apply_sector_cap(champion, instrument_map, float(settings["portfolio"]["max_sector_weight"]), cash)
    champion = apply_regime_caps(champion, instrument_map, regime.regime, settings["portfolio"])
    challenger = hrp_weights(close_frames, selected, cash)
    challenger = cap_single_weights(challenger, float(settings["portfolio"]["max_single_weight"]), cash)
    challenger = apply_sector_cap(challenger, instrument_map, float(settings["portfolio"]["max_sector_weight"]), cash)
    challenger = apply_regime_caps(challenger, instrument_map, regime.regime, settings["portfolio"])

    state = load_fund_state()
    current_weights = {key: float(value) for key, value in state.get("current_weights", {}).items()}
    champion, frozen_holdings = freeze_quarantined_fund_holdings(
        current_weights, champion, quarantined_symbols, cash
    )
    challenger, _ = freeze_quarantined_fund_holdings(
        current_weights, challenger, quarantined_symbols, cash
    )
    state_issues = validate_fund_state(state, set(instrument_map), cash)
    benchmark_dates = benchmark["date"] if not benchmark.empty and "date" in benchmark.columns else pd.Series(dtype="datetime64[ns]")
    # The historical 20-observation cadence remains the stable-trend baseline.  In neutral,
    # weak-signal or Risk-Off states the next review is deliberately earlier.
    strategy_confidence = confidence
    schedule_cfg = settings.get("review_schedule", {})
    pre_guard_status = DecisionStatus.DATA_BLOCKED if global_blocked_symbols else None
    review_interval, review_reason = review_interval_nav_observations(
        regime.regime, strategy_confidence, pre_guard_status, schedule_cfg
    )
    elapsed = trading_days_since(state.get("last_executed_trade_date"), as_of, benchmark_dates)
    scheduled_due = elapsed is None or elapsed >= review_interval
    risk_override = regime.regime == Regime.RISK_OFF and sum(
        current_weights.get(symbol, 0.0) for symbol, meta in instrument_map.items() if meta["group"] == "equity"
    ) > float(settings["portfolio"]["risk_off_equity_cap"])

    # Only a failed benchmark / explicitly global instrument blocks all portfolio decisions.
    # A quarantined non-benchmark fund is excluded from selection and frozen if currently held.
    guard_checks = {symbol: checks[symbol] for symbol in global_blocked_symbols}
    guard = rebalance_guard(
        data_checks=guard_checks,
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
        actionable_target = stage_target_weights(current_weights, champion, float(settings["execution"]["max_non_emergency_turnover"]))
    actionable_turnover = weights_turnover(current_weights, actionable_target)

    # V46-0104: 重复事件幂等检查 — 相同决策指纹不重复创建订单
    _fingerprint = None
    if guard.status in {DecisionStatus.REBALANCE_CANDIDATE, DecisionStatus.RISK_OVERRIDE}:
        import hashlib, json as _json
        _fingerprint = hashlib.md5(
            f"{as_of.date()}|{regime.regime.value}|{sorted(actionable_target.items())}|{actionable_turnover:.6f}".encode()
        ).hexdigest()
        last_log = project_path("output", "decisions", "fund_decision_log.jsonl")
        if last_log.exists():
            with last_log.open("r", encoding="utf-8") as fh:
                lines = [line for line in fh if line.strip()]
                if lines:
                    last_entry = _json.loads(lines[-1])
                    if last_entry.get("_fingerprint") == _fingerprint:
                        guard = GuardOutcome(
                            DecisionStatus.HOLD,
                            ["幂等检查: 与上次决策完全一致，跳过重复订单"],
                            guard.confidence,
                            actionable_turnover,
                        )

    # Tolerance applies only after the data/cycle/signal guard.  It cannot override a data block.
    if guard.status in {DecisionStatus.REBALANCE_CANDIDATE, DecisionStatus.RISK_OVERRIDE} and actionable_turnover <= float(settings["execution"]["tolerance_band"]):
        guard = GuardOutcome(DecisionStatus.HOLD, ["目标与当前持仓偏离未超过场外基金容忍带；不为小幅净值漂移申赎"], guard.confidence, actionable_turnover)

    execution_check = None
    execution_readiness = "NOT_REQUIRED"
    profile = settings.get("account_profile", {})
    mapping_confirmed = bool(profile.get("mapping_confirmed", True))
    if guard.status in {DecisionStatus.REBALANCE_CANDIDATE, DecisionStatus.RISK_OVERRIDE}:
        execution_check = validate_fund_execution_constraints(
            current_weights=current_weights,
            target_weights=actionable_target,
            instruments=instrument_map,
            tradeability=tradeability,
            as_of_date=str(as_of.date()),
            execution_config=settings["execution"],
            cash_symbol=cash,
        )
        if not mapping_confirmed:
            execution_readiness = "ACCOUNT_MAPPING_REQUIRED"
            guard = GuardOutcome(
                guard.status,
                guard.reasons + [
                    "策略方向已生成，但当前配置仍是旧 A 类示例代码，尚未映射为你的真实 C 类基金代码；仅可作研究参考，不生成真实申赎指令"
                ],
                guard.confidence,
                actionable_turnover,
                guard.requires_staging,
            )
        elif not execution_check.allowed:
            if bool(settings["execution"].get("provisional_plan_when_tradeability_unverified", False)):
                execution_readiness = "PRE_TRADE_CONFIRMATION_REQUIRED"
                guard = GuardOutcome(
                    guard.status,
                    guard.reasons + [
                        "策略方向已形成；当前仅缺销售平台申赎状态/费率核对，生成的是待确认策略计划，不是自动订单"
                    ],
                    guard.confidence,
                    actionable_turnover,
                    guard.requires_staging,
                )
            else:
                execution_readiness = "BLOCKED_BY_TRADEABILITY"
                guard = GuardOutcome(
                    DecisionStatus.HOLD_RECHECK,
                    ["场外基金申赎状态、费率或时点未核实；不生成计划"] + execution_check.reasons,
                    strategy_confidence,
                    actionable_turnover,
                )
        else:
            execution_readiness = "READY_FOR_MANUAL_APPROVAL"

    # Preserve the statistical confidence.  Missing fee snapshots are execution readiness issues,
    # not evidence that the underlying trend signal became weak.
    cost_verified = execution_check is not None and execution_check.allowed
    estimated_cost = float(execution_check.estimated_cost_weight or 0.0) if cost_verified else 0.0
    isolation_notes: list[str] = []
    for symbol in sorted(quarantined_symbols):
        item = next((candidate for candidate in instruments if candidate.symbol == symbol), None)
        if item is None or symbol in stale_group_symbols:
            continue
        current = float(current_weights.get(symbol, 0.0))
        if current > 1e-12:
            isolation_notes.append(
                f"{symbol} 数据异常已隔离：本轮不参与排名、不新增申购，也不依据异常数据赎回；当前 {current:.2%} 持仓被冻结保留"
            )
        else:
            isolation_notes.append(
                f"{symbol} 数据异常已隔离：本轮不参与排名、不新增申购；其余基金继续按直接净值信号评估"
            )
    for symbol in sorted(stale_group_symbols):
        current = float(current_weights.get(symbol, 0.0))
        frozen_note = f"；当前 {current:.2%} 持仓被冻结保留" if current > 1e-12 else ""
        isolation_notes.append(
            f"{symbol} 所属 {calendar_groups[symbol]} 日历组信号日过旧，与最新日历组不可比："
            f"本轮不参与排名、不新增申购{frozen_note}"
        )
    if alignment is not None:
        isolation_notes.extend(alignment.describe())

    next_interval, next_reason = review_interval_nav_observations(
        regime.regime, strategy_confidence, guard.status, schedule_cfg
    )
    next_review = estimate_next_review(
        anchor_date=common_signal_date,
        as_of_date=as_of,
        interval_nav_days=next_interval,
        schedule_config=schedule_cfg,
        reason=next_reason,
    )

    decision = Decision(
        as_of_date=str(as_of.date()),
        status=guard.status,
        reasons=isolation_notes + guard.reasons,
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
        operational_notes=refresh_notes + ([f"日历分组信号日：{item}" for item in alignment.describe()] if alignment is not None else []) + (
            ["账户提示：当前配置尚未确认 C 类基金代码映射；本次策略仅作研究参考，不对应真实账户申赎。"]
            if not mapping_confirmed else []
        ),
        strategy_confidence=strategy_confidence,
        execution_readiness=execution_readiness,
        next_review=next_review,
    )
    # A plan can be written only after the account has been mapped to the real C-class codes.
    # If fees/status are unverified, the plan is explicitly PROVISIONAL and remains manual-only.
    if decision.status in {DecisionStatus.REBALANCE_CANDIDATE, DecisionStatus.RISK_OVERRIDE} and mapping_confirmed:
        plan = build_fund_trade_plan(
            current_weights=current_weights,
            target_weights=actionable_target,
            as_of_date=decision.as_of_date,
            instruments=instrument_map,
            tradeability=tradeability,
            execution_config=settings["execution"],
            portfolio_value_cny=state.get("portfolio_value_cny"),
            cash_symbol=cash,
            execution_readiness=execution_readiness,
            pre_trade_requirements=(execution_check.reasons if execution_check is not None and not execution_check.allowed else []),
        )
        decision.trade_plan_path = str(write_fund_trade_plan(plan, decision.as_of_date).relative_to(project_path()))

    payload = decision.to_dict()
    if _fingerprint:
        payload["_fingerprint"] = _fingerprint
    payload.update(
        {
            "mode": "FUND_NAV_ONLY",
            "nav_basis": "cumulative_nav",
            "execution_model": "T 日基金净值披露后信号；下一基金可申赎日人工提交；按销售平台规则以未知净值定价并等待确认",
            "estimated_cost_verified": cost_verified,
            "fund_execution_constraints": (execution_check.reasons if execution_check is not None else ["本次无可执行申赎动作或尚未通过基础决策闸门"]),
            "tradeability_snapshot_date": tradeability.get("as_of_date"),
            "strategy_confidence": strategy_confidence,
            "execution_readiness": execution_readiness,
            "next_review": next_review,
            "review_interval_nav_observations": next_interval,
            "account_profile": profile,
            "selected_symbols": [item.symbol for item in selected],
            "correlation_limit": float(settings["portfolio"]["max_pair_correlation"]),
            "correlation_matrix": corr.round(4).to_dict() if not corr.empty else {},
            "governance": fund_governance_status(settings),
            "data_refresh": [item.to_dict() for item in refresh_results],
            "quality_isolation": {
                "global_blocked_symbols": sorted(global_blocked_symbols),
                "quarantined_symbols": sorted(quarantined_symbols),
                "frozen_holdings": frozen_holdings,
                "latest_signal_date": common_signal_date,
                "stale_calendar_groups": list(alignment.stale_groups) if alignment is not None else [],
                "policy": "非基准基金数据异常：隔离、不新增、不基于异常数据赎回；基准基金异常才全局阻断",
            },
            "calendar_group_alignment": alignment.to_dict() if alignment is not None else None,
        }
    )
    append_fund_decision_log(payload)
    write_fund_decision_report(payload, decision.as_of_date)
    return decision
