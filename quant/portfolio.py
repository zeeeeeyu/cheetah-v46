from __future__ import annotations

import math
from typing import Iterable

import numpy as np
import pandas as pd
from scipy.cluster.hierarchy import linkage, leaves_list
from scipy.spatial.distance import squareform

from .features import pairwise_returns
from .models import Regime, Signal


def selection_allowed_by_regime(signal: Signal, regime: Regime) -> bool:
    if not signal.eligible:
        return False
    if regime == Regime.RISK_ON:
        return signal.group in {"equity", "defensive_equity", "defensive"}
    if regime == Regime.NEUTRAL:
        return signal.group in {"defensive_equity", "defensive", "equity"}
    return signal.group in {"defensive_equity", "defensive"}


def correlation_matrix(frames: dict[str, pd.DataFrame], symbols: list[str], window: int = 60) -> pd.DataFrame:
    returns = pairwise_returns(frames, symbols, window=window)
    if returns.empty or len(symbols) <= 1:
        return pd.DataFrame(np.eye(len(symbols)), index=symbols, columns=symbols)
    corr = returns.corr().fillna(0.0).clip(-1.0, 1.0)
    for symbol in symbols:
        corr.loc[symbol, symbol] = 1.0
    return corr


def select_assets(
    signals: list[Signal],
    frames: dict[str, pd.DataFrame],
    regime: Regime,
    max_selected: int,
    max_pair_corr: float,
) -> tuple[list[Signal], pd.DataFrame]:
    candidates = [item for item in signals if selection_allowed_by_regime(item, regime)]
    candidates = sorted(candidates, key=lambda item: item.score if item.score is not None else -math.inf, reverse=True)
    if not candidates:
        return [], pd.DataFrame()
    all_symbols = [item.symbol for item in candidates]
    corr = correlation_matrix(frames, all_symbols)

    selected: list[Signal] = []
    for candidate in candidates:
        if len(selected) >= max_selected:
            break
        if not selected:
            selected.append(candidate)
            continue
        if all(float(corr.loc[candidate.symbol, held.symbol]) <= max_pair_corr for held in selected):
            selected.append(candidate)
    return selected, corr


def inverse_downside_vol_weights(selected: list[Signal], max_single_weight: float, cash_symbol: str = "CASH") -> dict[str, float]:
    if not selected:
        return {cash_symbol: 1.0}
    inv = {item.symbol: 1.0 / max(float(item.downside_vol or 0), 1e-6) for item in selected}
    total = sum(inv.values())
    raw = {symbol: value / total for symbol, value in inv.items()}
    capped = {symbol: min(weight, max_single_weight) for symbol, weight in raw.items()}
    residual = 1.0 - sum(capped.values())
    uncapped = [symbol for symbol, weight in capped.items() if weight < max_single_weight - 1e-12]
    while residual > 1e-10 and uncapped:
        current = sum(raw[symbol] for symbol in uncapped)
        additions = {}
        for symbol in uncapped:
            additions[symbol] = min(residual * raw[symbol] / current, max_single_weight - capped[symbol])
        used = sum(additions.values())
        for symbol, add in additions.items():
            capped[symbol] += add
        residual -= used
        uncapped = [symbol for symbol in uncapped if capped[symbol] < max_single_weight - 1e-12]
        if used <= 1e-12:
            break
    if residual > 1e-10:
        capped[cash_symbol] = capped.get(cash_symbol, 0.0) + residual
    return normalize_weights(capped)


def _cluster_variance(cov: pd.DataFrame, cluster: list[str]) -> float:
    sub_cov = cov.loc[cluster, cluster]
    inv_diag = 1.0 / np.diag(sub_cov.values)
    ivp = inv_diag / inv_diag.sum()
    return float(ivp @ sub_cov.values @ ivp)


def hrp_weights(frames: dict[str, pd.DataFrame], selected: list[Signal], cash_symbol: str = "CASH") -> dict[str, float]:
    """V4.3 shadow allocator. Returns cash only when there is no tradable selection."""
    symbols = [item.symbol for item in selected]
    if not symbols:
        return {cash_symbol: 1.0}
    if len(symbols) == 1:
        return {symbols[0]: 1.0}

    returns = pairwise_returns(frames, symbols, window=60).dropna()
    if len(returns) < 20:
        return inverse_downside_vol_weights(selected, max_single_weight=1.0, cash_symbol=cash_symbol)
    cov = returns.cov()
    corr = returns.corr().clip(-1, 1)
    distance = np.sqrt(np.clip((1 - corr.values) / 2, 0, 1))
    condensed = squareform(distance, checks=False)
    tree = linkage(condensed, method="single")
    order = [symbols[i] for i in leaves_list(tree)]

    weights = pd.Series(1.0, index=order)
    clusters: list[list[str]] = [order]
    while clusters:
        cluster = clusters.pop(0)
        if len(cluster) <= 1:
            continue
        split = len(cluster) // 2
        left, right = cluster[:split], cluster[split:]
        left_var = _cluster_variance(cov, left)
        right_var = _cluster_variance(cov, right)
        alpha = 1.0 - left_var / max(left_var + right_var, 1e-12)
        weights[left] *= alpha
        weights[right] *= 1.0 - alpha
        clusters.extend([left, right])
    return normalize_weights(weights.to_dict())


def cap_single_weights(weights: dict[str, float], max_single_weight: float, cash_symbol: str = "CASH") -> dict[str, float]:
    """Feasibility cap shared by Champion and Challenger; excess stays in cash rather than being silently re-levered."""
    result: dict[str, float] = {}
    excess = 0.0
    for symbol, weight in normalize_weights(weights).items():
        if symbol == cash_symbol:
            result[symbol] = result.get(symbol, 0.0) + weight
        elif weight > max_single_weight:
            result[symbol] = max_single_weight
            excess += weight - max_single_weight
        else:
            result[symbol] = weight
    if excess > 0:
        result[cash_symbol] = result.get(cash_symbol, 0.0) + excess
    return normalize_weights(result)


def apply_sector_cap(weights: dict[str, float], instruments: dict[str, dict], max_sector_weight: float, cash_symbol: str = "CASH") -> dict[str, float]:
    """Caps the aggregate of equity-sector ETFs; excess is retained as cash, not moved into another unverified theme."""
    sector_symbols = [
        symbol for symbol in weights
        if symbol != cash_symbol and instruments.get(symbol, {}).get("asset_class") == "equity_sector"
    ]
    sector_total = sum(weights.get(symbol, 0.0) for symbol in sector_symbols)
    if sector_total <= max_sector_weight + 1e-12:
        return normalize_weights(weights)
    result = weights.copy()
    scale = max_sector_weight / sector_total
    for symbol in sector_symbols:
        result[symbol] *= scale
    result[cash_symbol] = result.get(cash_symbol, 0.0) + (sector_total - max_sector_weight)
    return normalize_weights(result)


def apply_regime_caps(weights: dict[str, float], instruments: dict[str, dict], regime: Regime, config: dict) -> dict[str, float]:
    cash = config["cash_symbol"]
    if regime == Regime.RISK_ON:
        return normalize_weights(weights)
    cap = float(config["neutral_equity_cap"] if regime == Regime.NEUTRAL else config["risk_off_equity_cap"])
    equity_symbols = [symbol for symbol in weights if symbol != cash and instruments[symbol]["group"] == "equity"]
    equity_total = sum(weights[symbol] for symbol in equity_symbols)
    if equity_total <= cap + 1e-12:
        return normalize_weights(weights)
    scale = cap / equity_total
    result = weights.copy()
    for symbol in equity_symbols:
        result[symbol] *= scale
    result[cash] = result.get(cash, 0.0) + (equity_total - cap)
    return normalize_weights(result)


def normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    clean = {key: max(0.0, float(value)) for key, value in weights.items() if float(value) > 1e-12}
    total = sum(clean.values())
    if total <= 0:
        return {"CASH": 1.0}
    return {key: value / total for key, value in clean.items()}
