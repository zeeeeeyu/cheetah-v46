"""跨源一致性比对（不静默覆盖任何一方）。

用途：当某资产存在两个来源（如主源 + 次源）时，量化差异，供研究人员判断是否
可互证。绝不在本模块内把一方数据覆盖到另一方。

状态：
- 给定 threshold 且 close_diff > threshold -> DATA_SOURCE_MISMATCH
- 给定 threshold 且 close_diff <= threshold -> OK
- 未给定 threshold -> PROVISIONAL（不硬编码最终结论，交由上层裁决）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd


@dataclass
class CrossSourceReport:
    date_match_ratio: float
    close_diff: float
    ohlc_diff: float
    volume_diff: float
    missing_dates: list[str] = field(default_factory=list)
    source_only_dates: list[str] = field(default_factory=list)
    adjustment_conflict: bool = False
    status: str = "PROVISIONAL"


def _mean_abs_pct(a: pd.Series, b: pd.Series) -> float:
    """对齐后平均绝对百分比差（分母为基准 b，避免除零）。"""
    merged = pd.DataFrame({"a": a, "b": b}).dropna()
    if merged.empty or (merged["b"].abs() < 1e-12).all():
        return float("nan")
    diff = (merged["a"] - merged["b"]).abs()
    base = merged["b"].abs().replace(0.0, float("nan"))
    return float((diff / base).mean(skipna=True))


def compare_sources(
    primary_df: pd.DataFrame,
    secondary_df: pd.DataFrame,
    threshold: Optional[float] = None,
) -> CrossSourceReport:
    """比对主源与次源的研究域 DataFrame（需含 date/close/open/high/low/volume）。"""
    p = primary_df[["date", "close", "open", "high", "low", "volume"]].copy()
    s = secondary_df[["date", "close", "open", "high", "low", "volume"]].copy()
    p["date"] = p["date"].astype(str)
    s["date"] = s["date"].astype(str)

    p_dates = set(p["date"])
    s_dates = set(s["date"])
    intersection = p_dates & s_dates
    union = p_dates | s_dates
    date_match_ratio = (len(intersection) / len(union)) if union else 0.0

    missing = sorted(s_dates - p_dates)        # 次源有、主源缺
    source_only = sorted(p_dates - s_dates)    # 主源有、次源缺

    m = pd.merge(
        p.rename(columns=lambda c: f"p_{c}" if c != "date" else c),
        s.rename(columns=lambda c: f"s_{c}" if c != "date" else c),
        on="date", how="inner",
    )
    close_diff = _mean_abs_pct(m["p_close"], m["s_close"]) if len(m) else float("nan")
    ohlc_diff = _mean_abs_pct(
        m[["p_open", "p_high", "p_low", "p_close"]].mean(axis=1),
        m[["s_open", "s_high", "s_low", "s_close"]].mean(axis=1),
    ) if len(m) else float("nan")
    volume_diff = _mean_abs_pct(m["p_volume"], m["s_volume"]) if len(m) else float("nan")

    # 复权冲突：若两源均声明复权且不同
    adj_conflict = False
    if "adjustment" in primary_df.columns and "adjustment" in secondary_df.columns:
        pa = set(primary_df["adjustment"].astype(str).unique())
        sa = set(secondary_df["adjustment"].astype(str).unique())
        adj_conflict = bool(
            bool(pa | sa) and pa != sa and (pa - {"UNKNOWN"}) and (sa - {"UNKNOWN"})
        )

    # 状态裁决
    if threshold is not None:
        status = "DATA_SOURCE_MISMATCH" if (close_diff > threshold) else "OK"
    else:
        status = "PROVISIONAL"

    return CrossSourceReport(
        date_match_ratio=date_match_ratio,
        close_diff=close_diff,
        ohlc_diff=ohlc_diff,
        volume_diff=volume_diff,
        missing_dates=missing,
        source_only_dates=source_only,
        adjustment_conflict=adj_conflict,
        status=status,
    )
