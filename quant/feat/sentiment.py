"""V4.6 市场情绪特征模块。

初版只使用可稳定获取的数据：
- 市场成交额
- 指数上涨比例
- 行业上涨比例
- 创新高比例
- 高波动资产比例
- ETF 份额变化
- 热点持续天数
- 板块扩散程度

热点扩散等级：
- 单一ETF强
- 同主题多ETF强
- 多主题扩散
- 市场全面扩散
- 热点衰退
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd


# ═══════════════════════════════════════════════════════════════
# 热点扩散
# ═══════════════════════════════════════════════════════════════

class HotspotDiffusionLevel(str):
    SINGLE_ETF_STRONG = "SINGLE_ETF_STRONG"               # 单一ETF强
    SAME_THEME_MULTI_ETF = "SAME_THEME_MULTI_ETF"         # 同主题多ETF强
    MULTI_THEME_DIFFUSION = "MULTI_THEME_DIFFUSION"       # 多主题扩散
    MARKET_WIDE_DIFFUSION = "MARKET_WIDE_DIFFUSION"       # 市场全面扩散
    HOTSPOT_DECAY = "HOTSPOT_DECAY"                        # 热点衰退


@dataclass
class HotspotAnalysis:
    """热点分析结果。"""
    as_of_date: str
    diffusion_level: str = "HOTSPOT_DECAY"
    top_themes: list[tuple[str, float]] = field(default_factory=list)  # (theme, strength)
    above_5pct_count: int = 0                                          # 涨幅 > 5% 的 ETF 数
    theme_clusters: dict[str, list[str]] = field(default_factory=dict) # 主题→ETF 列表
    dominant_theme: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of_date": self.as_of_date,
            "diffusion_level": self.diffusion_level,
            "top_themes": list(self.top_themes[:5]),
            "above_5pct_count": self.above_5pct_count,
            "dominant_theme": self.dominant_theme,
            "notes": list(self.notes),
        }


def analyze_hotspot_diffusion(
    frames: dict[str, pd.DataFrame],
    theme_map: dict[str, list[str]],  # theme → [symbols]
    as_of_date: str,
    lookback: int = 5,
) -> HotspotAnalysis:
    """分析市场热点扩散程度。"""
    analysis = HotspotAnalysis(as_of_date=as_of_date)

    # 计算各 ETF 近期涨幅
    etf_returns: dict[str, float] = {}
    above_5pct = 0
    for sym, frame in frames.items():
        if frame is None or frame.empty or "close" not in frame.columns:
            continue
        close = frame["close"].astype(float)
        if len(close) < lookback + 1:
            continue
        ret = float(close.iloc[-1] / close.iloc[-lookback - 1] - 1)
        etf_returns[sym] = ret
        if ret > 0.05:
            above_5pct += 1

    analysis.above_5pct_count = above_5pct

    # 按主题聚合
    theme_strength: dict[str, float] = {}
    analysis.theme_clusters = {}
    for theme, symbols in theme_map.items():
        strengths = [etf_returns.get(s, 0) for s in symbols if s in etf_returns]
        if strengths:
            theme_strength[theme] = float(np.mean(strengths))
            analysis.theme_clusters[theme] = [s for s in symbols if s in etf_returns]

    # 排序主题强度
    sorted_themes = sorted(theme_strength.items(), key=lambda x: x[1], reverse=True)
    analysis.top_themes = sorted_themes[:10]

    if sorted_themes:
        analysis.dominant_theme = sorted_themes[0][0]

    # 判定扩散等级
    strong_themes = [t for t, s in sorted_themes if s > 0.03]
    if len(strong_themes) >= 5:
        analysis.diffusion_level = "MARKET_WIDE_DIFFUSION"
        analysis.notes.append("多主题显著上涨，市场全面扩散")
    elif len(strong_themes) >= 3:
        analysis.diffusion_level = "MULTI_THEME_DIFFUSION"
        analysis.notes.append(f"{len(strong_themes)} 个主题同时走强")
    elif len(strong_themes) >= 1 and analysis.above_5pct_count >= 3:
        analysis.diffusion_level = "SAME_THEME_MULTI_ETF"
        analysis.notes.append(f"主题 {strong_themes[0]} 内多只ETF走强")
    elif len(strong_themes) >= 1:
        analysis.diffusion_level = "SINGLE_ETF_STRONG"
        analysis.notes.append(f"单一主题 {strong_themes[0]} 强势")
    else:
        analysis.diffusion_level = "HOTSPOT_DECAY"
        analysis.notes.append("无显著热点，热点可能衰退")

    return analysis


# ═══════════════════════════════════════════════════════════════
# 市场情绪指标
# ═══════════════════════════════════════════════════════════════

@dataclass
class SentimentSnapshot:
    """市场情绪快照。"""
    as_of_date: str
    market_turnover: float | None = None         # 市场总成交额
    advance_ratio: float | None = None           # 上涨比例
    industry_advance_ratio: float | None = None  # 行业上涨比例
    new_high_ratio: float | None = None          # 创新高比例
    high_vol_ratio: float | None = None          # 高波动资产比例
    etf_share_change: float | None = None        # ETF 份额变化
    hotspot_days: int = 0                        # 热点持续天数
    diffusion_level: str = "HOTSPOT_DECAY"       # 扩散等级

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of_date": self.as_of_date,
            "market_turnover": self.market_turnover,
            "advance_ratio": round(self.advance_ratio, 4) if self.advance_ratio else None,
            "industry_advance_ratio": round(self.industry_advance_ratio, 4) if self.industry_advance_ratio else None,
            "new_high_ratio": round(self.new_high_ratio, 4) if self.new_high_ratio else None,
            "high_vol_ratio": round(self.high_vol_ratio, 4) if self.high_vol_ratio else None,
            "etf_share_change": round(self.etf_share_change, 4) if self.etf_share_change else None,
            "hotspot_days": self.hotspot_days,
            "diffusion_level": self.diffusion_level,
        }


def compute_sentiment(
    frames: dict[str, pd.DataFrame],
    as_of_date: str,
    hotspot: HotspotAnalysis | None = None,
    new_high_window: int = 60,
    high_vol_threshold: float = 0.03,
) -> SentimentSnapshot:
    """计算市场情绪快照。"""
    snap = SentimentSnapshot(as_of_date=as_of_date)
    total_turnover = 0.0
    advancers = 0
    new_highs = 0
    high_vol = 0
    total = 0

    for sym, frame in frames.items():
        if frame is None or frame.empty:
            continue
        total += 1
        if "close" in frame.columns:
            close = frame["close"].astype(float)
            if len(close) >= 2:
                current = float(close.iloc[-1])
                prev = float(close.iloc[-2])
                if current > prev: advancers += 1
                if len(close) >= new_high_window and current >= float(close.tail(new_high_window).max()):
                    new_highs += 1
            if len(close) >= 20:
                daily_ret = close.pct_change().dropna().tail(20)
                vol = float(daily_ret.std())
                if vol > high_vol_threshold: high_vol += 1
        if "turnover" in frame.columns:
            total_turnover += float(frame["turnover"].iloc[-1]) if len(frame) > 0 else 0

    snap.market_turnover = total_turnover
    snap.advance_ratio = advancers / total if total > 0 else None
    snap.new_high_ratio = new_highs / total if total > 0 else None
    snap.high_vol_ratio = high_vol / total if total > 0 else None

    if hotspot:
        snap.diffusion_level = hotspot.diffusion_level

    return snap
