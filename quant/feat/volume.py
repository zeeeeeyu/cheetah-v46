"""V4.6 量特征模块。

只回答"数据表现为什么样"，不回答"应该买什么"。
基金净值不得使用成交量、盘中高低价或 ETF 份额数据。
"""

from __future__ import annotations
import numpy as np
import pandas as pd

def relative_volume(volume: pd.Series, window: int = 20) -> float | None:
    if len(volume) < window + 1: return None
    recent, avg = float(volume.iloc[-1]), float(volume.tail(window + 1).iloc[:-1].mean())
    return None if avg <= 0 else recent / avg

def turnover_percentile(turnover: pd.Series, window: int = 60) -> float | None:
    if len(turnover) < window + 1: return None
    current, history = float(turnover.iloc[-1]), turnover.tail(window + 1).iloc[:-1].values
    below, total = float(np.sum(history < current)), float(len(history))
    return below / total if total > 0 else None

def amount_percentile(turnover: pd.Series, window: int = 60) -> float | None:
    return turnover_percentile(turnover, window)

def volume_price_confirmation(close: pd.Series, volume: pd.Series, window: int = 20) -> float | None:
    if len(close) < window or len(volume) < window: return None
    price_dir, vol_dir = np.sign(close.diff().tail(window).dropna()), np.sign(volume.diff().tail(window).dropna())
    aligned = (price_dir * vol_dir).replace(0, np.nan).dropna()
    return None if len(aligned) < 5 else float(aligned.mean())

def liquidity_score(turnover: pd.Series, window: int = 20) -> float | None:
    if len(turnover) < window: return None
    recent = turnover.tail(window)
    avg, std = float(recent.mean()), float(recent.std())
    if avg <= 0: return 0.0
    cv, stability = std / avg if avg > 0 else 1.0, max(0.0, 1.0 - std / avg) if avg > 0 else 0.0
    size_score = min(1.0, avg / 1e8)
    return float(0.5 * stability + 0.5 * size_score)

def etf_share_change(shares: pd.Series, window: int = 20) -> float | None:
    if shares is None or len(shares) < window: return None
    return float((shares.iloc[-1] / shares.iloc[-window]) - 1.0)
