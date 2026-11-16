"""V4.6 战术 ETF 横截面排名。

从 V4.2 的 Top-2 窄池升级为研究池 → 过滤 → 评分 → 排名 → 选池流程。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from quant.features import compute_signal_features, fragmentation, downside_volatility
from quant.models import Regime


@dataclass
class RankedETF:
    """排名后的 ETF 评分结果。"""
    symbol: str
    name: str
    score: float
    rank: int
    r10: float | None = None
    r20: float | None = None
    r60: float | None = None
    r120: float | None = None
    downside_vol: float | None = None
    fragmentation: float | None = None
    trend_quality: float | None = None
    volume_confirmation: float | None = None
    reasons: list[str] = field(default_factory=list)
    eligible: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "score": self.score,
            "rank": self.rank,
            "r10": self.r10,
            "r20": self.r20,
            "r60": self.r60,
            "r120": self.r120,
            "downside_vol": self.downside_vol,
            "fragmentation": self.fragmentation,
            "trend_quality": self.trend_quality,
            "eligible": self.eligible,
            "reasons": list(self.reasons),
        }


def _make_default_signal_settings() -> dict:
    return {
        "short_window": 10, "medium_window": 20, "long_window": 60,
        "fi_window": 60, "minimum_absolute_momentum": 0.0,
        "minimum_trend_quality": 0.0, "minimum_rank_margin": 0.0,
        "require_two_day_stability": False,
    }


def score_trend(
    symbol: str,
    frame: pd.DataFrame,
    settings: dict | None = None,
) -> RankedETF | None:
    """TACTICAL_TREND 评分。

    使用 V4.2 的多周期动量 + 趋势质量逻辑。
    只使用直接 ETF 行情数据。
    """
    if frame is None or frame.empty or "close" not in frame.columns:
        return None

    cfg = _make_default_signal_settings()
    if settings:
        cfg.update(settings)

    close = frame["close"].astype(float)
    lw = int(cfg["long_window"])
    if len(close) < lw:
        return None

    try:
        features = compute_signal_features(frame, cfg)
    except (ValueError, KeyError, TypeError):
        return None

    score = float(features.get("score", 0.0) or 0.0)
    r10 = float(close.iloc[-1] / close.iloc[-10] - 1) if len(close) >= 10 else None

    return RankedETF(
        symbol=symbol,
        name=symbol,
        score=score,
        rank=0,
        r10=r10,
        r20=features.get("r20"),
        r60=features.get("r60"),
        r120=features.get("r120"),
        downside_vol=features.get("downside_vol"),
        fragmentation=features.get("fragmentation"),
        trend_quality=features.get("trend_quality"),
        eligible=score > 0 if score else False,
    )


def rank_universe(
    frames: dict[str, pd.DataFrame],
    pool: list[str],
    settings: dict | None = None,
    max_results: int = 10,
    regime: Regime | None = None,
    min_absolute_momentum: float = 0.0,
) -> list[RankedETF]:
    """对资产池进行横截面排名。

    流程：
    1. 数据检查 → 排除数据不足
    2. 绝对动量过滤 → 排除负收益
    3. 评分 → 多因子趋势评分
    4. 排序 → 降序
    5. 截取 top N
    """
    ranked: list[RankedETF] = []

    for symbol in pool:
        frame = frames.get(symbol)
        result = score_trend(symbol, frame, settings)
        if result is None:
            continue

        # 绝对动量过滤
        if result.r60 is not None and result.r60 < min_absolute_momentum:
            result.eligible = False
            result.reasons.append(f"60日动量 {result.r60:.2%} < {min_absolute_momentum:.0%}")

        ranked.append(result)

    # 排序
    ranked.sort(key=lambda r: r.score if r.eligible else -1e9, reverse=True)

    # 分配排名
    for i, r in enumerate(ranked):
        r.rank = i + 1

    # Regime 过滤（风险状态下只保留防御型）
    if regime == Regime.RISK_OFF:
        ranked = [r for r in ranked if r.eligible]

    return ranked[:max_results]
