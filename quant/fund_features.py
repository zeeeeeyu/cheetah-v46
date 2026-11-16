from __future__ import annotations

import numpy as np
import pandas as pd

from .features import downside_volatility, fragmentation


def compute_fund_nav_features(frame: pd.DataFrame, settings: dict) -> dict[str, float]:
    """Features intentionally limited to observable fund NAV data.

    Open-end funds do not have exchange OHLCV in this branch.  No ATR, intraday range, turnover,
    order-book, or ETF proxy feature is invented.
    """
    nav = pd.to_numeric(frame["nav"], errors="coerce").astype(float)
    sw = int(settings["short_window"])
    mw = int(settings["medium_window"])
    lw = int(settings["long_window"])
    fiw = int(settings["fi_window"])
    if len(nav) < lw:
        raise ValueError(f"历史长度不足以计算 {lw} 日场外基金信号")

    r20 = float(nav.iloc[-1] / nav.iloc[-sw] - 1.0)
    r60 = float(nav.iloc[-1] / nav.iloc[-mw] - 1.0)
    r120 = float(nav.iloc[-1] / nav.iloc[-lw] - 1.0)
    ma20 = float(nav.rolling(sw).mean().iloc[-1])
    ma60 = float(nav.rolling(mw).mean().iloc[-1])
    ma120 = float(nav.rolling(lw).mean().iloc[-1])
    fi = fragmentation(nav, fiw)
    dvol = downside_volatility(nav, mw)

    returns = nav.pct_change(fill_method=None).dropna().tail(mw)
    positive_day_ratio = float((returns > 0).mean())
    rolling_max = nav.tail(mw).cummax()
    max_drawdown = float((nav.tail(mw) / rolling_max - 1.0).min())
    trend_component = 0.10 * r20 + 0.35 * r60 + 0.55 * r120
    ma_alignment = float(np.mean([nav.iloc[-1] > ma20, ma20 > ma60, ma60 > ma120]))
    drawdown_quality = float(np.clip(1.0 + max_drawdown / 0.15, 0.0, 1.0))
    fi_quality = float(np.clip(1.0 - fi, 0.0, 1.0)) if not np.isnan(fi) else 0.0
    trend_quality = float(
        0.35 * ma_alignment
        + 0.25 * positive_day_ratio
        + 0.25 * drawdown_quality
        + 0.15 * fi_quality
    )
    score = float((trend_component / max(dvol, 1e-6)) * (0.50 + 0.50 * trend_quality))
    return {
        "r20": r20,
        "r60": r60,
        "r120": r120,
        "ma20": ma20,
        "ma60": ma60,
        "ma120": ma120,
        "downside_vol": dvol,
        "fragmentation": float(fi),
        "positive_day_ratio": positive_day_ratio,
        "max_drawdown_60d": max_drawdown,
        "trend_quality": trend_quality,
        "score": score,
    }


def nav_frames_as_close(frames: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Compatibility view for shared correlation/HRP code; it contains only NAV-based close values."""
    result: dict[str, pd.DataFrame] = {}
    for symbol, frame in frames.items():
        if frame is None or frame.empty:
            continue
        result[symbol] = frame[["date", "nav"]].rename(columns={"nav": "close"}).copy()
    return result
