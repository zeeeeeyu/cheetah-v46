"""Topic-01 可分解参考策略（LEGACY_CHAMPION_BASELINE 重建参考版）。

忠实复刻 V4.6 tactical_etf 信号逻辑（quant/features.compute_signal_features /
ranking.py），但将每个组件参数化、可独立开关，以支持 Strategy Attribution 消融。

重要诚实声明：
- 真实 V4.2 / V4.5.1 旧策略源码在本仓库不存在（git 历史仅含 V4.6 提交）。
- 本文件是「依据 V4.6 文档化逻辑重建的参考策略」，并非原始 Legacy 源码。
  其 External Evidence = NOT_APPLICABLE（见 T01_BASELINE_AUDIT.md）。
- 成交量确认（volume_confirmation）在 V4.6 实际未实现，本策略不伪造该模块；
  若实验配置请求启用，统一返回 NOT_IMPLEMENTED 并记录。

组件映射（对照 Topic-01 §八 T01-04）：
- Absolute Momentum: 各周期收益 r_h
- Cross-Sectional Ranking: 池内按信号打分降序
- Multi-Horizon: trend_component = Σ w_h * r_h
- Vol Scaling: score = trend_component / downside_vol * (0.5 + 0.5*trend_quality)
- FI (fragmentation): fi_quality = clip(1 - fragmentation, 0, 1)
- MA alignment: mean(close>ma20, ma20>ma60, ma60>ma120)
- Volume confirmation: NOT_IMPLEMENTED
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from .data_loader import window_as_of


def _fragmentation(close: pd.Series, window: int) -> float:
    section = close.tail(window).to_numpy(dtype=float)
    if len(section) < window:
        return float("nan")
    displacement = abs(section[-1] - section[0])
    path_length = np.abs(np.diff(section)).sum()
    return 0.0 if path_length == 0 else float(1.0 - displacement / path_length)


def _downside_vol(close: pd.Series, window: int) -> float:
    returns = close.pct_change(fill_method=None).dropna().tail(window)
    if len(returns) < window - 1:
        return float("nan")
    downside = np.minimum(returns.to_numpy(dtype=float), 0.0)
    value = float(np.sqrt(np.mean(np.square(downside))) * np.sqrt(252))
    return max(value, 1e-6)


@dataclass
class ReferenceConfig:
    """可分解配置。每个开关对应一个可消融组件。"""
    horizons: list[int] = field(default_factory=lambda: [10, 20, 60])
    horizon_weights: dict[int, float] | None = None  # 缺省等权于 horizons
    use_absolute_momentum: bool = True
    use_cross_sectional: bool = True
    min_absolute_momentum: float = 0.0
    use_vol_scaling: bool = True
    vol_scaling_mode: str = "downside"  # downside | full | none
    use_ma_alignment: bool = True
    use_fi: bool = True
    use_volume_confirmation: bool = False  # V4.6 未实现 -> NOT_IMPLEMENTED
    max_positions: int = 3
    max_single_weight: float = 0.50
    cash_symbol: str = "CASH"
    fi_window: int = 60
    speed: str = "legacy"  # fast | medium | slow | multi | legacy (T01-10)

    def effective_horizon_weights(self) -> dict[int, float]:
        if self.horizon_weights:
            return {int(k): float(v) for k, v in self.horizon_weights.items()}
        n = len(self.horizons)
        return {int(h): 1.0 / n for h in self.horizons}


@dataclass
class AssetFeatures:
    symbol: str
    momentum: dict[int, float] = field(default_factory=dict)
    trend_component: float = 0.0
    ma_alignment: float = 0.0
    fi_quality: float = 0.0
    positive_day_ratio: float = 0.0
    drawdown_quality: float = 0.0
    trend_quality: float = 0.0
    downside_vol: float = 0.0
    score: float = 0.0
    eligible: bool = False
    volume_confirmation: str = "NOT_IMPLEMENTED"


def compute_asset_features(
    frame: pd.DataFrame,
    config: ReferenceConfig,
    as_of: str,
) -> AssetFeatures | None:
    """在 as_of 时点（PIT）计算单资产特征。仅使用 as_of 及之前的数据。"""
    w = window_as_of(frame, as_of)
    close = w["close"].astype(float)
    if len(close) < max(config.horizons):
        return None

    feats = AssetFeatures(symbol=frame.get("symbol", "?"))

    # ── Absolute Momentum（多周期）──
    for h in config.horizons:
        if len(close) > h:
            feats.momentum[h] = float(close.iloc[-1] / close.iloc[-h] - 1.0)
        else:
            feats.momentum[h] = float("nan")

    # ── Multi-Horizon trend_component ──
    hw = config.effective_horizon_weights()
    tc = 0.0
    for h, wt in hw.items():
        m = feats.momentum.get(h)
        if m is not None and not np.isnan(m):
            tc += wt * m
    feats.trend_component = tc

    # ── MA alignment ──
    ma_short = float(close.rolling(min(config.horizons)).mean().iloc[-1]) if len(close) >= min(config.horizons) else float(close.iloc[-1])
    med_h = sorted(config.horizons)[len(config.horizons) // 2] if config.horizons else 20
    long_h = max(config.horizons)
    ma_med = float(close.rolling(med_h).mean().iloc[-1])
    ma_long = float(close.rolling(long_h).mean().iloc[-1])
    ma_align_parts = [close.iloc[-1] > ma_short, ma_short > ma_med, ma_med > ma_long]
    if config.use_ma_alignment:
        feats.ma_alignment = float(np.mean([1.0 if p else 0.0 for p in ma_align_parts]))
    else:
        feats.ma_alignment = 0.0  # 关闭：不贡献

    # ── FI / fragmentation ──
    fi = _fragmentation(close, config.fi_window)
    feats.fi_quality = float(np.clip(1.0 - fi, 0.0, 1.0)) if not np.isnan(fi) else 0.0

    # ── 辅助趋势质量子项 ──
    rets = close.pct_change(fill_method=None).dropna().tail(max(config.horizons))
    feats.positive_day_ratio = float((rets > 0).mean()) if len(rets) else 0.0
    roll_max = close.tail(max(config.horizons)).cummax()
    mdd = float((close.tail(max(config.horizons)) / roll_max - 1).min())
    feats.drawdown_quality = float(np.clip(1.0 + mdd / 0.15, 0.0, 1.0))

    # ── 趋势质量（按开关重归一化 4 个子项）──
    sub = {
        "ma": (0.35, feats.ma_alignment if config.use_ma_alignment else None),
        "pos": (0.25, feats.positive_day_ratio),
        "dd": (0.25, feats.drawdown_quality),
        "fi": (0.15, feats.fi_quality if config.use_fi else None),
    }
    num = 0.0
    den = 0.0
    for _, (w_sub, val) in sub.items():
        if val is not None:
            num += w_sub * val
            den += w_sub
    feats.trend_quality = num / den if den > 0 else 0.0

    # ── Volatility scaling（T01-03 核心）──
    feats.downside_vol = _downside_vol(close, max(config.horizons))
    if not config.use_vol_scaling or config.vol_scaling_mode == "none":
        # 完全不做波动率缩放：仅用多周期动量趋势分量
        feats.score = float(feats.trend_component)
    elif config.vol_scaling_mode == "raw":
        # 无波动率缩放，但保留趋势质量乘数（T01-03 C 对照）
        feats.score = float(feats.trend_component * (0.50 + 0.50 * feats.trend_quality))
    else:
        dvol = feats.downside_vol
        if config.vol_scaling_mode == "downside":
            scale = 1.0 / dvol
        else:  # full vol (inverse full volatility)
            std = float(rets.std()) if len(rets) > 1 else 1e-6
            scale = 1.0 / max(std * np.sqrt(252), 1e-6)
        feats.score = float(feats.trend_component * scale * (0.50 + 0.50 * feats.trend_quality))

    # ── Absolute momentum 过滤 ──
    if config.use_absolute_momentum:
        eff_r = feats.momentum.get(max(config.horizons))
        if eff_r is None or np.isnan(eff_r) or eff_r < config.min_absolute_momentum:
            feats.eligible = False
        else:
            feats.eligible = True
    else:
        feats.eligible = True  # 仅横截面模式不做绝对过滤

    feats.volume_confirmation = "NOT_IMPLEMENTED"  # V4.6 未实现
    return feats


def generate_target_weights(
    frames: dict[str, pd.DataFrame],
    pool: list[str],
    as_of: str,
    config: ReferenceConfig,
) -> dict[str, float]:
    """在 as_of 时点生成目标权重（含现金，归一为 1）。

    组合方式：
    - 绝对动量模式：按绝对动量信号选 Top-N（use_cross_sectional=False）。
    - 横截面模式：按池内信号打分降序选 Top-N（use_absolute_momentum=False）。
    - 组合模式：绝对过滤 + 横截面排名（V4.6 默认）。
    """
    if config.use_volume_confirmation:
        # 明确拒绝伪造成交量确认模块
        raise NotImplementedError("volume_confirmation 在 V4.6 未实现（NOT_IMPLEMENTED），禁止伪造")

    scored: list[tuple[str, AssetFeatures]] = []
    for sym in pool:
        f = frames.get(sym)
        if f is None or f.empty:
            continue
        feat = compute_asset_features(f, config, as_of)
        if feat is None:
            continue
        scored.append((sym, feat))

    if not scored:
        return {config.cash_symbol: 1.0}

    # Cross-sectional ranking by score
    ranked = sorted(scored, key=lambda x: x[1].score if x[1].eligible else -1e9, reverse=True)

    if config.use_absolute_momentum and config.use_cross_sectional:
        selected = [(s, ft) for s, ft in ranked if ft.eligible][: config.max_positions]
    elif config.use_absolute_momentum and not config.use_cross_sectional:
        # 仅绝对动量：按绝对动量总和排序
        scored_abs = sorted(
            [(s, ft) for s, ft in scored if ft.eligible],
            key=lambda x: sum(v for v in x[1].momentum.values() if not np.isnan(v)),
            reverse=True,
        )
        selected = scored_abs[: config.max_positions]
    elif config.use_cross_sectional and not config.use_absolute_momentum:
        # 仅横截面：直接按打分取 Top-N，不做绝对过滤
        selected = ranked[: config.max_positions]
    else:
        selected = []

    if not selected:
        return {config.cash_symbol: 1.0}

    # 权重：波动率缩放 -> 浓度上限（超额转现金，不静默加杠杆）
    if config.use_vol_scaling and config.vol_scaling_mode != "none":
        raw = {s: max(ft.score, 1e-3) for s, ft in selected}
    else:
        raw = {s: 1.0 for s, ft in selected}  # 等权（T01-03 B）
    total = sum(raw.values())
    norm = {s: v / total for s, v in raw.items()}
    weights = _cap_single_weights(norm, config.max_single_weight, config.cash_symbol)
    return weights


def _cap_single_weights(raw: dict[str, float], max_single: float, cash: str) -> dict[str, float]:
    """单标的权重上限，超额部分转为现金。"""
    out = {cash: 0.0}
    allocated = 0.0
    capped = 0.0
    for s, w in raw.items():
        if w > max_single:
            capped += w - max_single
            out[s] = max_single
        else:
            out[s] = w
        allocated += min(w, max_single)
    out[cash] = round(1.0 - allocated, 6)
    # 把被截断的超额加到现金
    out[cash] = round(out[cash] + capped, 6)
    return out
