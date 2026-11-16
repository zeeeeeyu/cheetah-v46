from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .models import DataStatus, DecisionStatus, Regime, Signal


@dataclass
class GuardOutcome:
    status: DecisionStatus
    reasons: list[str]
    confidence: str
    turnover: float
    requires_staging: bool = False


def weights_turnover(current: dict[str, float], target: dict[str, float]) -> float:
    assets = set(current) | set(target)
    return 0.5 * sum(abs(float(current.get(asset, 0.0)) - float(target.get(asset, 0.0))) for asset in assets)


def validate_state(state: dict, valid_symbols: set[str], cash_symbol: str) -> list[str]:
    issues: list[str] = []
    weights = state.get("current_weights", {})
    if not weights:
        issues.append("当前持仓为空")
        return issues
    total = sum(float(value) for value in weights.values())
    if abs(total - 1.0) > 1e-6:
        issues.append(f"当前权重总和为 {total:.6f}，必须等于 1.0")
    unknown = [symbol for symbol in weights if symbol not in valid_symbols and symbol != cash_symbol]
    if unknown:
        issues.append(f"检测到旧场外基金或未知代码 {unknown}；ETF_ONLY 模式下必须先人工完成迁移")
    return issues


def trading_days_since(last_date: str | None, as_of: pd.Timestamp, benchmark_dates: pd.Series) -> int | None:
    if not last_date:
        return None
    last = pd.Timestamp(last_date).normalize()
    dates = pd.to_datetime(benchmark_dates).dt.normalize()
    return int(((dates > last) & (dates <= as_of.normalize())).sum())


def signal_confidence(
    signals_today: list[Signal],
    signals_prev: list[Signal] | None,
    settings: dict,
) -> tuple[str, list[str]]:
    eligible_today = sorted([item for item in signals_today if item.eligible], key=lambda x: float(x.score or -1e9), reverse=True)
    if not eligible_today:
        return "WEAK", ["没有满足绝对动量、趋势质量、MA120 与流动性条件的资产"]

    reasons: list[str] = []
    score_margin = float("inf")
    if len(eligible_today) >= 2:
        leader = max(abs(float(eligible_today[0].score or 0.0)), 1e-6)
        score_margin = abs(float(eligible_today[0].score or 0.0) - float(eligible_today[1].score or 0.0)) / leader
        if score_margin < float(settings["minimum_rank_margin"]):
            reasons.append(f"排名领先幅度不足：{score_margin:.2%}")

    if settings.get("require_two_day_stability", True):
        if signals_prev is None:
            reasons.append("无法计算前一交易日稳定性")
        else:
            previous_eligible = sorted(
                [item for item in signals_prev if item.eligible],
                key=lambda x: float(x.score or -1e9),
                reverse=True,
            )
            top_n = min(3, len(eligible_today), len(previous_eligible))
            previous_top = [item.symbol for item in previous_eligible[:top_n]]
            current_top = [item.symbol for item in eligible_today[:top_n]]
            if current_top != previous_top:
                reasons.append("候选排序未满足连续两次检查稳定性")

    if reasons:
        return "WEAK", reasons
    return "STRONG", ["候选信号满足排名幅度与两日稳定性要求"]


def stage_target_weights(current: dict[str, float], target: dict[str, float], max_turnover: float) -> dict[str, float]:
    """Move only a proportional fraction toward a strategic target when the one-shot turnover cap is exceeded."""
    turnover = weights_turnover(current, target)
    if turnover <= max_turnover or turnover <= 1e-12:
        return target.copy()
    alpha = max_turnover / turnover
    assets = set(current) | set(target)
    staged = {asset: float(current.get(asset, 0.0)) + alpha * (float(target.get(asset, 0.0)) - float(current.get(asset, 0.0))) for asset in assets}
    total = sum(staged.values())
    return {asset: value / total for asset, value in staged.items() if value > 1e-12}


def rebalance_guard(
    data_checks: dict[str, object],
    state_issues: list[str],
    scheduled_due: bool,
    risk_override: bool,
    confidence: str,
    confidence_reasons: list[str],
    current_weights: dict[str, float],
    target_weights: dict[str, float],
    max_non_emergency_turnover: float,
) -> GuardOutcome:
    blocked = [symbol for symbol, check in data_checks.items() if getattr(check, "status", None) == DataStatus.BLOCK]
    turnover = weights_turnover(current_weights, target_weights)
    if blocked:
        return GuardOutcome(DecisionStatus.DATA_BLOCKED, [f"数据质量阻断：{blocked}"], "WEAK", turnover)
    if state_issues:
        return GuardOutcome(DecisionStatus.MIGRATION_REQUIRED, state_issues, "WEAK", turnover)
    if risk_override:
        return GuardOutcome(DecisionStatus.RISK_OVERRIDE, ["Risk-Off 风险覆盖：允许突破常规调仓周期"] + confidence_reasons, confidence, turnover)
    if not scheduled_due:
        return GuardOutcome(DecisionStatus.HOLD, ["未到计划调仓日，且没有 Risk-Off 风险覆盖"], confidence, turnover)
    if confidence != "STRONG":
        return GuardOutcome(DecisionStatus.HOLD_RECHECK, ["信号置信度不足，不执行换仓"] + confidence_reasons, confidence, turnover)
    if turnover > max_non_emergency_turnover:
        return GuardOutcome(
            DecisionStatus.REBALANCE_CANDIDATE,
            [
                f"完整目标换手率 {turnover:.1%} 超过单次安全上限 {max_non_emergency_turnover:.1%}；仅允许生成分段人工审核计划",
            ] + confidence_reasons,
            confidence,
            turnover,
            requires_staging=True,
        )
    return GuardOutcome(DecisionStatus.REBALANCE_CANDIDATE, ["数据、周期、稳定性与换手约束均通过；仅生成待人工审核计划"] + confidence_reasons, confidence, turnover)
