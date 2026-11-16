"""V4.6 空特征模块。"""
from __future__ import annotations
import numpy as np
import pandas as pd

def price_range_position(close: pd.Series, window: int = 60) -> float | None:
    if len(close) < window: return None
    seg, low, high, cur = close.tail(window), float(close.tail(window).min()), float(close.tail(window).max()), float(close.iloc[-1])
    return 0.5 if high == low else float((cur - low) / (high - low))

def distance_to_high(close: pd.Series, window: int = 60) -> float | None:
    if len(close) < window: return None
    high, cur = float(close.tail(window).max()), float(close.iloc[-1])
    return float((cur / high) - 1.0)

def distance_to_low(close: pd.Series, window: int = 60) -> float | None:
    if len(close) < window: return None
    low, cur = float(close.tail(window).min()), float(close.iloc[-1])
    return float((cur / low) - 1.0)

def current_drawdown(close: pd.Series, window: int = 60) -> float | None:
    if len(close) < window: return None
    peak, cur = float(close.tail(window).cummax().iloc[-1]), float(close.iloc[-1])
    return float((cur / peak) - 1.0)

def atr_distance(close: pd.Series, atr_value: float) -> float | None:
    if len(close) < 20 or atr_value <= 0: return None
    ma20, cur = float(close.tail(20).mean()), float(close.iloc[-1])
    return float((cur - ma20) / atr_value)

def cross_sectional_percentile(current_value: float, universe_values: list[float]) -> float:
    if not universe_values: return 0.5
    below, total = sum(1 for v in universe_values if v < current_value), len(universe_values)
    return float(below / total)
