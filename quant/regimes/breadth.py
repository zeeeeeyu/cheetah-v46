"""V4.6 市场宽度模块。

基于 ETF 或指数池计算：
- 上涨比例
- MA20 以上比例
- MA60 以上比例
- 创新高比例
- 强势 ETF 数量
- 行业扩散程度

排除被隔离（quarantined）的资产。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class BreadthSnapshot:
    """市场宽度快照。"""
    as_of_date: str
    advance_ratio: float | None = None          # 上涨比例
    above_ma20_ratio: float | None = None       # MA20 以上比例
    above_ma60_ratio: float | None = None       # MA60 以上比例
    new_high_ratio: float | None = None         # 创阶段新高比例
    strong_etf_count: int | None = None         # 强势 ETF 数量
    industry_diffusion: float | None = None     # 行业扩散程度
    total_instruments: int = 0
    quarantined_count: int = 0
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of_date": self.as_of_date,
            "advance_ratio": self.advance_ratio,
            "above_ma20_ratio": self.above_ma20_ratio,
            "above_ma60_ratio": self.above_ma60_ratio,
            "new_high_ratio": self.new_high_ratio,
            "strong_etf_count": self.strong_etf_count,
            "industry_diffusion": self.industry_diffusion,
            "total_instruments": self.total_instruments,
            "quarantined_count": self.quarantined_count,
            "warnings": list(self.warnings),
        }


def compute_breadth(
    frames: dict[str, pd.DataFrame],
    as_of_date: str,
    quarantined_symbols: set[str] | None = None,
    new_high_window: int = 60,
    lookback_days: int = 1,
) -> BreadthSnapshot:
    """计算市场宽度。

    排除被隔离的资产（quarantined）以避免数据污染。
    """
    quarantined = quarantined_symbols or set()
    snapshot = BreadthSnapshot(as_of_date=as_of_date)
    advancers = 0
    above_ma20 = 0
    above_ma60 = 0
    new_highs = 0
    total = 0

    for symbol, frame in frames.items():
        if symbol in quarantined:
            snapshot.quarantined_count += 1
            continue
        if frame is None or frame.empty or "close" not in frame.columns:
            snapshot.warnings.append(f"{symbol}: 数据不可用")
            continue
        close = frame["close"].astype(float)
        if len(close) < max(new_high_window, 60) + lookback_days:
            continue

        total += 1
        current = float(close.iloc[-1])
        prev = float(close.iloc[-lookback_days - 1]) if lookback_days < len(close) else float(close.iloc[0])

        # 上涨
        if current > prev:
            advancers += 1

        # MA20 以上
        ma20 = float(close.tail(20).mean())
        if current > ma20:
            above_ma20 += 1

        # MA60 以上
        ma60 = float(close.tail(60).mean())
        if current > ma60:
            above_ma60 += 1

        # 创新高
        if current >= float(close.tail(new_high_window).max()):
            new_highs += 1

    if total == 0:
        snapshot.warnings.append("无可用资产计算宽度")
        return snapshot

    snapshot.total_instruments = total
    snapshot.advance_ratio = float(advancers / total)
    snapshot.above_ma20_ratio = float(above_ma20 / total)
    snapshot.above_ma60_ratio = float(above_ma60 / total)
    snapshot.new_high_ratio = float(new_highs / total)
    snapshot.strong_etf_count = int(advancers)

    # 行业扩散：用 MA20 以上比例的稳定性衡量
    # 越高表示越多的资产处于趋势中
    snapshot.industry_diffusion = float(above_ma20 / total) if total > 0 else None

    return snapshot
