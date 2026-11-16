from __future__ import annotations

"""Transparent historical-coverage audit for long-horizon research.

The system never manufactures a ten-year backtest from a recent fund pool.  This module tells the
user exactly how much direct-NAV history is available and whether a dynamic historical universe is
actually complete enough to study a requested period.
"""

from typing import Any

import pandas as pd

from .fund_universe import FundInstrument


def historical_coverage_report(
    instruments: list[FundInstrument],
    frames: dict[str, pd.DataFrame],
    *,
    target_start: str = "2014-01-01",
    target_end: str | None = None,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    starts: list[pd.Timestamp] = []
    ends: list[pd.Timestamp] = []
    for inst in instruments:
        frame = frames.get(inst.symbol)
        if frame is None or frame.empty or "date" not in frame.columns:
            rows.append({"symbol": inst.symbol, "name": inst.name, "available": False, "reason": "无直接净值文件"})
            continue
        dates = pd.to_datetime(frame["date"], errors="coerce").dropna().sort_values()
        if dates.empty:
            rows.append({"symbol": inst.symbol, "name": inst.name, "available": False, "reason": "净值日期为空"})
            continue
        first, last = dates.iloc[0].normalize(), dates.iloc[-1].normalize()
        starts.append(first)
        ends.append(last)
        rows.append({
            "symbol": inst.symbol,
            "name": inst.name,
            "available": True,
            "first_nav_date": str(first.date()),
            "last_nav_date": str(last.date()),
            "observations": int(len(dates)),
            "historical_active_from": inst.historical_active_from,
            "historical_active_to": inst.historical_active_to,
            "covers_target_start": first <= pd.Timestamp(target_start),
        })
    common_start = max(starts) if starts else None
    common_end = min(ends) if ends else None
    requested_end = pd.Timestamp(target_end).normalize() if target_end else (common_end or pd.Timestamp.today().normalize())
    full_coverage = bool(common_start and common_end and common_start <= pd.Timestamp(target_start) and common_end >= requested_end)
    return {
        "target_start": target_start,
        "target_end": str(requested_end.date()),
        "common_direct_nav_start": str(common_start.date()) if common_start is not None else None,
        "common_direct_nav_end": str(common_end.date()) if common_end is not None else None,
        "ten_year_research_ready": full_coverage,
        "reason": (
            "直接净值池覆盖目标区间，可进一步核验动态历史候选池成员资格。"
            if full_coverage
            else "当前直接净值共同历史不足以支撑目标十年研究；系统不会生成伪十年结论。需补充当时可交易基金的历史直接净值和成员资格。"
        ),
        "funds": rows,
    }
