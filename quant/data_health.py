"""数据失效分级隔离（c37 / REQ-DATA-03）。

将「数据是否可用」从二值（有/无）升级为四档，使下游（哨兵、决策）能按失效等级
采取不同降级动作，而非一律 Fail-Open 或一律 Fail-Closed。

四档语义：
- NORMAL             数据完整且在新鲜窗口内
- STALE              数据完整但超出新鲜窗口（可能滞后）
- PARTIALLY_MISSING  关键列缺失或完整性不足
- FULLY_MISSING      无任何数据

设计要点：分类必须是**纯函数**、无副作用、可被生产数据层（datahub）与诊断
（inspect_system）调用。它不决定动作，只客观描述数据健康度。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Iterable

import pandas as pd


class DataHealth(str, Enum):
    """数据健康四档（数值越大越不可用）。"""

    NORMAL = "NORMAL"
    STALE = "STALE"
    PARTIALLY_MISSING = "PARTIALLY_MISSING"
    FULLY_MISSING = "FULLY_MISSING"

    @property
    def rank(self) -> int:
        """保守严重度：数值越大越不可用。"""
        return {
            DataHealth.NORMAL: 0,
            DataHealth.STALE: 1,
            DataHealth.PARTIALLY_MISSING: 2,
            DataHealth.FULLY_MISSING: 3,
        }[self]

    @property
    def is_usable(self) -> bool:
        """是否可用于生产决策（FULLY_MISSING 一律不可用）。"""
        return self is not DataHealth.FULLY_MISSING

    def is_at_least_as_bad_as(self, other: "DataHealth") -> bool:
        return self.rank >= other.rank


def _resolve_date_column(frame: pd.DataFrame) -> str | None:
    if "date" in frame.columns:
        return "date"
    if frame.index.name == "date":
        return None  # 用 index
    # 退化：取第一列
    return frame.columns[0]


def _last_timestamp(frame: pd.DataFrame) -> pd.Timestamp | None:
    if frame is None or frame.empty:
        return None
    col = _resolve_date_column(frame)
    try:
        if col is None:
            return pd.to_datetime(frame.index).max()
        return pd.to_datetime(frame[col]).max()
    except Exception:
        return None


def classify(
    frame: pd.DataFrame | None,
    *,
    as_of: datetime | str | None = None,
    freshness_window_days: int = 5,
    min_completeness: float = 0.9,
    require_columns: Iterable[str] | None = None,
) -> DataHealth:
    """对单只标的的行情帧做四档健康分类。

    Args:
        frame: 行情 DataFrame（需含 ``date`` 列或 DatetimeIndex）。``None`` 或空表
            视为完全没有数据。
        as_of: 判定基准日；缺省取今日。
        freshness_window_days: 超过该天数的数据视为 STALE。
        min_completeness: 非缺失单元格占比阈值，低于则 PARTIALLY_MISSING。
        require_columns: 必须存在的列；缺失任一则 PARTIALLY_MISSING。

    Returns:
        DataHealth 四档之一。
    """
    if frame is None or (isinstance(frame, pd.DataFrame) and frame.empty):
        return DataHealth.FULLY_MISSING

    if require_columns:
        missing = [c for c in require_columns if c not in getattr(frame, "columns", [])]
        if missing:
            return DataHealth.PARTIALLY_MISSING

    if isinstance(frame, pd.DataFrame):
        try:
            completeness = 1.0 - float(frame.isnull().mean().mean())
        except Exception:
            completeness = 0.0
        if completeness < min_completeness:
            return DataHealth.PARTIALLY_MISSING

    last = _last_timestamp(frame)
    if last is None:
        return DataHealth.FULLY_MISSING

    as_of_ts = pd.to_datetime(as_of) if as_of is not None else pd.Timestamp.today().normalize()
    age_days = (as_of_ts.normalize() - last.normalize()).days
    if age_days > freshness_window_days:
        return DataHealth.STALE
    return DataHealth.NORMAL


def worst(healths: Iterable["DataHealth"]) -> "DataHealth":
    """取一组健康度中最差的一档（用于多源数据聚合）。"""
    worst_seen = DataHealth.NORMAL
    for h in healths:
        if h.rank > worst_seen.rank:
            worst_seen = h
    return worst_seen
