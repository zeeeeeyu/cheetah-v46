from __future__ import annotations

import numpy as np
import pandas as pd


def fragmentation(close: pd.Series, window: int = 60) -> float:
    section = close.tail(window).to_numpy(dtype=float)
    if len(section) < window:
        return float("nan")
    displacement = abs(section[-1] - section[0])
    path_length = np.abs(np.diff(section)).sum()
    return 0.0 if path_length == 0 else float(1.0 - displacement / path_length)


def downside_volatility(close: pd.Series, window: int = 60) -> float:
    returns = close.pct_change(fill_method=None).dropna().tail(window)
    if len(returns) < window - 1:
        return float("nan")
    downside = np.minimum(returns.to_numpy(dtype=float), 0.0)
    value = float(np.sqrt(np.mean(np.square(downside))) * np.sqrt(252))
    return max(value, 1e-6)


def average_true_range(frame: pd.DataFrame, window: int = 20) -> float:
    prev_close = frame["close"].shift(1)
    true_range = pd.concat(
        [frame["high"] - frame["low"], (frame["high"] - prev_close).abs(), (frame["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    value = true_range.rolling(window).mean().iloc[-1]
    return float(value) if pd.notna(value) else float("nan")


def compute_signal_features(frame: pd.DataFrame, settings: dict) -> dict[str, float]:
    close = frame["close"].astype(float)
    sw = int(settings["short_window"])
    mw = int(settings["medium_window"])
    lw = int(settings["long_window"])
    fiw = int(settings["fi_window"])
    if len(close) < lw:
        raise ValueError(f"历史长度不足以计算 {lw} 日信号")

    r20 = float(close.iloc[-1] / close.iloc[-sw] - 1)
    r60 = float(close.iloc[-1] / close.iloc[-mw] - 1)
    r120 = float(close.iloc[-1] / close.iloc[-lw] - 1)
    ma20 = float(close.rolling(sw).mean().iloc[-1])
    ma60 = float(close.rolling(mw).mean().iloc[-1])
    ma120 = float(close.rolling(lw).mean().iloc[-1])
    fi = fragmentation(close, fiw)
    dvol = downside_volatility(close, mw)

    positive_day_ratio = float((close.pct_change(fill_method=None).dropna().tail(mw) > 0).mean())
    rolling_max = close.tail(mw).cummax()
    max_drawdown = float((close.tail(mw) / rolling_max - 1).min())

    # V4.2: 多周期趋势 + 趋势质量。成交量不改变趋势方向。
    trend_component = 0.10 * r20 + 0.35 * r60 + 0.55 * r120
    ma_alignment = float(np.mean([close.iloc[-1] > ma20, ma20 > ma60, ma60 > ma120]))
    drawdown_quality = float(np.clip(1.0 + max_drawdown / 0.15, 0.0, 1.0))
    fi_quality = float(np.clip(1.0 - fi, 0.0, 1.0)) if not np.isnan(fi) else 0.0
    trend_quality = float(
        0.35 * ma_alignment
        + 0.25 * positive_day_ratio
        + 0.25 * drawdown_quality
        + 0.15 * fi_quality
    )
    score = float((trend_component / max(dvol, 1e-6)) * (0.50 + 0.50 * trend_quality))

    median_turnover_20d = float(frame["turnover"].tail(20).median())
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
        "median_turnover_20d": median_turnover_20d,
    }


def pairwise_returns(frames: dict[str, pd.DataFrame], symbols: list[str], window: int = 60) -> pd.DataFrame:
    table: dict[str, pd.Series] = {}
    for symbol in symbols:
        item = frames[symbol].set_index("date")["close"].pct_change(fill_method=None)
        table[symbol] = item
    return pd.DataFrame(table).tail(window).dropna(how="all")
