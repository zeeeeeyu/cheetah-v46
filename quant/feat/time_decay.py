"""V4.6 时特征模块。"""
from __future__ import annotations
from datetime import datetime
import numpy as np

def signal_age(signal_generated_date: str, as_of_date: str) -> int:
    gen, now = datetime.strptime(signal_generated_date, "%Y-%m-%d"), datetime.strptime(as_of_date, "%Y-%m-%d")
    return max(0, (now - gen).days)

def breakout_age(breakout_date: str, as_of_date: str) -> int: return signal_age(breakout_date, as_of_date)

def exponential_decay(age_days: int, half_life_days: float = 20.0) -> float:
    return 1.0 if half_life_days <= 0 else float(np.exp(-np.log(2) * age_days / half_life_days))

def data_freshness(last_data_date: str, as_of_date: str, max_stale_days: int = 5) -> float:
    age = signal_age(last_data_date, as_of_date)
    return 0.0 if age >= max_stale_days else float(1.0 - age / max_stale_days)

def signal_expiration(signal_age_days: int, max_valid_days: int = 30) -> bool:
    return signal_age_days >= max_valid_days

def multi_horizon_consistency(short_signal: float, medium_signal: float, long_signal: float) -> float:
    signs = [np.sign(s) for s in [short_signal, medium_signal, long_signal] if s != 0]
    if len(signs) < 2: return 0.5
    positive, negative = sum(1 for s in signs if s > 0), sum(1 for s in signs if s < 0)
    return float(max(positive, negative) / len(signs))
