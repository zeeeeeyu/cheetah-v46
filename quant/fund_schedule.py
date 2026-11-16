from __future__ import annotations

"""Low-frequency review scheduler for the direct-fund branch.

The scheduler deliberately recommends a *review time*, not an automatic order time.
It uses available fund-NAV observations as the timing anchor.  Future Chinese trading
holidays are not inferred from a scraped calendar, so the calendar date is clearly
labelled as an estimate and should be moved to the next available fund-NAV evening
when a holiday intervenes.
"""

from typing import Any

import pandas as pd

from .models import DecisionStatus, Regime


def review_interval_nav_observations(
    regime: Regime,
    confidence: str,
    status: DecisionStatus | None,
    schedule_config: dict[str, Any],
) -> tuple[int, str]:
    """Return the recommended number of new NAV observations before the next review."""
    if status == DecisionStatus.DATA_BLOCKED:
        return int(schedule_config.get("data_retry_interval_business_days", 1)), "数据需复核：下一工作日晚间重试"
    if regime == Regime.RISK_OFF:
        return int(schedule_config.get("risk_off_interval_nav_days", 3)), "Risk-Off：缩短复核间隔"
    if regime == Regime.NEUTRAL or str(confidence).upper() != "STRONG":
        return int(schedule_config.get("neutral_or_weak_interval_nav_days", 10)), "中性或弱信号：两周内复核"
    return int(schedule_config.get("stable_risk_on_strong_interval_nav_days", 20)), "趋势稳定：按低频节奏复核"


def estimate_next_review(
    *,
    anchor_date: str | None,
    as_of_date: pd.Timestamp,
    interval_nav_days: int,
    schedule_config: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    """Build a transparent next-review recommendation.

    `interval_nav_days` means *new available NAV observations*, not calendar days.
    For a personal local system without an official exchange calendar, the future date
    is an approximate business-day estimate.  The report says this explicitly.
    """
    anchor = pd.Timestamp(anchor_date).normalize() if anchor_date else pd.Timestamp(as_of_date).normalize()
    # Business-day approximation only.  It is intentionally not called a guaranteed trading day.
    estimated_date = (anchor + pd.offsets.BDay(max(1, int(interval_nav_days)))).normalize()
    run_time = str(schedule_config.get("suggested_run_time", "21:00"))
    timezone_label = str(schedule_config.get("timezone_label", "北京时间"))
    return {
        "anchor_signal_date": str(anchor.date()),
        "interval_nav_observations": int(interval_nav_days),
        "estimated_review_date": str(estimated_date.date()),
        "suggested_run_time": run_time,
        "timezone_label": timezone_label,
        "suggested_run_at": f"{estimated_date.date()} {run_time}（{timezone_label}，预计；遇节假日顺延至下一可用基金净值日晚间）",
        "reason": reason,
        "calendar_disclaimer": "日期按工作日估算，不等同于交易所或基金销售平台的最终交易日；以基金净值披露和销售平台申赎截止时间为准。",
    }
