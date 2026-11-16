"""V4.6 多维市场状态集成。

从单一沪深 300 判断升级为多指数、多宽度、多波动率输入。
输出 5 种市场状态 + 置信度 + 有效期。

状态切换原则：
- 风险状态（RISK_OFF）可以快速进入
- 风险解除需要较慢确认
- 不允许单日噪声频繁切换
- 保留 legacy_core_regime 用于对照
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from quant.domain.enums import MarketRegime
from quant.models import Regime as LegacyRegime, RegimeResult as LegacyRegimeResult


@dataclass
class EnsembleInput:
    """多维 Regime 输入。"""
    csi300_trend: float | None = None          # 沪深 300 趋势得分
    csi500_trend: float | None = None          # 中证 500 趋势得分
    gem_trend: float | None = None             # 创业板趋势得分
    value_trend: float | None = None           # 红利/价值趋势得分
    advance_ratio: float | None = None         # 上涨比例
    above_ma20_ratio: float | None = None      # MA20 以上比例
    above_ma60_ratio: float | None = None      # MA60 以上比例
    volatility: float | None = None            # 实现波动率
    downside_vol: float | None = None          # 下行波动率
    turnover_percentile: float | None = None   # 成交额分位
    industry_diffusion: float | None = None    # 行业扩散
    cross_asset_confirm: float | None = None   # 跨资产确认度


@dataclass
class RegimeEnsembleResult:
    """多维市场状态输出。"""
    regime: MarketRegime
    confidence: float                          # [0, 1]
    reasons: list[str] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)
    valid_until: str | None = None
    legacy_label: str | None = None            # 旧 Regime 对照

    def to_dict(self) -> dict[str, Any]:
        return {
            "regime": self.regime.value,
            "confidence": self.confidence,
            "reasons": list(self.reasons),
            "metrics": dict(self.metrics),
            "valid_until": self.valid_until,
            "legacy_label": self.legacy_label,
        }


class RegimeEnsemble:
    """多维市场状态集成器。

    从多个来源（多个指数、宽度、波动率）综合判断市场状态。
    """

    # 权重配置
    W_TREND = 0.30        # 多指数趋势
    W_BREADTH = 0.25      # 市场宽度
    W_VOLATILITY = 0.20   # 波动率
    W_TURNOVER = 0.15     # 成交额
    W_CONFIRM = 0.10      # 跨资产确认

    # 状态切换保护
    MIN_RISK_OFF_DAYS = 2       # 最少维持 RISK_OFF 的天数
    RECOVERY_CONFIRM_DAYS = 3   # 从 RISK_OFF 恢复需要的确认天数

    def __init__(self, min_risk_off_days: int | None = None):
        self._prev_regime: MarketRegime = MarketRegime.UNKNOWN
        self._prev_date: str | None = None
        self._risk_off_start: str | None = None
        self._recovery_streak: int = 0
        self._transition_count: int = 0  # 用于检测频繁切换
        self.min_risk_off_days = min_risk_off_days or self.MIN_RISK_OFF_DAYS

    def compute(self, inp: EnsembleInput, as_of_date: str) -> RegimeEnsembleResult:
        """根据多维输入计算市场状态。"""
        reasons: list[str] = []
        metrics: dict[str, float] = {}

        # 1. 计算各维度得分
        trend_score = self._score_trend(inp)
        breadth_score = self._score_breadth(inp)
        vol_score = self._score_volatility(inp)
        turnover_score = self._score_turnover(inp)
        confirm_score = inp.cross_asset_confirm or 0.0

        # 2. 综合得分
        raw_score = (
            self.W_TREND * trend_score
            + self.W_BREADTH * breadth_score
            + self.W_VOLATILITY * vol_score
            + self.W_TURNOVER * turnover_score
            + self.W_CONFIRM * confirm_score
        )

        metrics["trend_score"] = trend_score
        metrics["breadth_score"] = breadth_score
        metrics["volatility_score"] = vol_score
        metrics["turnover_score"] = turnover_score
        metrics["raw_score"] = raw_score

        # 3. 初始判断
        if raw_score < 0.2:
            proposed = MarketRegime.STRESS_RISK_OFF
            reasons.append(f"综合得分 {raw_score:.2f} < 0.2：进入压力/风险规避状态")
        elif raw_score < 0.4:
            proposed = MarketRegime.RECOVERY_PROBE
            reasons.append(f"综合得分 {raw_score:.2f} 在 0.2-0.4：恢复试探期")
        elif raw_score < 0.6:
            proposed = MarketRegime.RANGE_NEUTRAL
            reasons.append(f"综合得分 {raw_score:.2f} 在 0.4-0.6：震荡中性")
        else:
            proposed = MarketRegime.TREND_RISK_ON
            reasons.append(f"综合得分 {raw_score:.2f} >= 0.6：趋势风险偏好")

        # 4. 切换保护：RISK_OFF 快速进入，慢速退出
        final_regime, protection_reasons = self._apply_switching_protection(
            proposed, as_of_date
        )
        reasons.extend(protection_reasons)

        # 5. 置信度
        confidence = self._compute_confidence(raw_score, metrics)

        # 6. 有效期
        valid_until = self._estimate_valid_until(final_regime, as_of_date)

        # 7. 旧 Regime 对照
        legacy = self._map_to_legacy(final_regime)

        # 8. 防频繁切换
        self._transition_count += 1

        self._prev_regime = final_regime
        self._prev_date = as_of_date

        return RegimeEnsembleResult(
            regime=final_regime,
            confidence=confidence,
            reasons=reasons,
            metrics=metrics,
            valid_until=valid_until,
            legacy_label=legacy.value if legacy else None,
        )

    def _score_trend(self, inp: EnsembleInput) -> float:
        scores = []
        if inp.csi300_trend is not None:
            scores.append(float(np.clip(inp.csi300_trend, -1, 1)) * 0.5 + 0.5)
        if inp.csi500_trend is not None:
            scores.append(float(np.clip(inp.csi500_trend, -1, 1)) * 0.5 + 0.5)
        if inp.gem_trend is not None:
            scores.append(float(np.clip(inp.gem_trend, -1, 1)) * 0.5 + 0.5)
        if inp.value_trend is not None:
            scores.append(float(np.clip(inp.value_trend, -1, 1)) * 0.5 + 0.5)
        return float(np.mean(scores)) if scores else 0.5

    def _score_breadth(self, inp: EnsembleInput) -> float:
        scores = []
        if inp.advance_ratio is not None:
            scores.append(inp.advance_ratio)
        if inp.above_ma20_ratio is not None:
            scores.append(inp.above_ma20_ratio)
        if inp.above_ma60_ratio is not None:
            scores.append(inp.above_ma60_ratio)
        if inp.industry_diffusion is not None:
            scores.append(inp.industry_diffusion)
        return float(np.mean(scores)) if scores else 0.5

    def _score_volatility(self, inp: EnsembleInput) -> float:
        """低波动 = 高分，高波动 = 低分。"""
        scores = []
        if inp.volatility is not None and inp.volatility > 0:
            vol_score = max(0.0, 1.0 - inp.volatility * 5)
            scores.append(vol_score)
        if inp.downside_vol is not None and inp.downside_vol > 0:
            dv_score = max(0.0, 1.0 - inp.downside_vol * 5)
            scores.append(dv_score)
        return float(np.mean(scores)) if scores else 0.5

    def _score_turnover(self, inp: EnsembleInput) -> float:
        if inp.turnover_percentile is not None:
            return float(inp.turnover_percentile)
        return 0.5

    def _apply_switching_protection(
        self, proposed: MarketRegime, as_of_date: str,
    ) -> tuple[MarketRegime, list[str]]:
        """状态切换保护逻辑。"""
        reasons: list[str] = []

        # 进入 RISK_OFF：快速
        if proposed == MarketRegime.STRESS_RISK_OFF:
            if self._prev_regime != MarketRegime.STRESS_RISK_OFF:
                self._risk_off_start = as_of_date
                reasons.append("RISK_OFF: 快速进入（风险状态可立即触发）")
            return proposed, reasons

        # 从 RISK_OFF 退出：需要确认
        if (self._prev_regime == MarketRegime.STRESS_RISK_OFF
                and proposed != MarketRegime.STRESS_RISK_OFF):
            if self._risk_off_start:
                days_in_risk_off = self._days_between(self._risk_off_start, as_of_date)
                if days_in_risk_off < self.min_risk_off_days:
                    reasons.append(
                        f"RISK_OFF 维持至少 {self.min_risk_off_days} 天（当前 {days_in_risk_off} 天）"
                    )
                    return MarketRegime.STRESS_RISK_OFF, reasons

            self._recovery_streak += 1
            if self._recovery_streak < self.RECOVERY_CONFIRM_DAYS:
                reasons.append(
                    f"恢复确认中（第 {self._recovery_streak}/{self.RECOVERY_CONFIRM_DAYS} 天）"
                )
                return MarketRegime.RECOVERY_PROBE, reasons

        # 正常切换
        self._recovery_streak = 0
        return proposed, reasons

    def _compute_confidence(self, raw_score: float, metrics: dict) -> float:
        """置信度计算：极端位置高置信，中间位置低置信。"""
        distance_from_center = abs(raw_score - 0.5) * 2  # [0, 1]
        return float(max(0.3, min(0.95, distance_from_center + 0.3)))

    def _estimate_valid_until(self, regime: MarketRegime, as_of_date: str) -> str:
        """估计状态有效期。"""
        dt = datetime.strptime(as_of_date, "%Y-%m-%d")
        if regime == MarketRegime.STRESS_RISK_OFF:
            delta = timedelta(days=1)  # 风险状态每日检查
        elif regime == MarketRegime.TREND_RISK_ON:
            delta = timedelta(days=5)  # 趋势状态有效 5 天
        else:
            delta = timedelta(days=3)  # 其他状态 3 天
        return (dt + delta).strftime("%Y-%m-%d")

    @staticmethod
    def _days_between(start: str, end: str) -> int:
        from datetime import datetime
        s = datetime.strptime(start, "%Y-%m-%d")
        e = datetime.strptime(end, "%Y-%m-%d")
        return max(0, (e - s).days)

    @staticmethod
    def _map_to_legacy(regime: MarketRegime) -> LegacyRegime:
        """映射到旧版 3 状态 Regime。"""
        mapping = {
            MarketRegime.TREND_RISK_ON: LegacyRegime.RISK_ON,
            MarketRegime.RANGE_NEUTRAL: LegacyRegime.NEUTRAL,
            MarketRegime.STRESS_RISK_OFF: LegacyRegime.RISK_OFF,
            MarketRegime.RECOVERY_PROBE: LegacyRegime.NEUTRAL,
            MarketRegime.UNKNOWN: LegacyRegime.NEUTRAL,
        }
        return mapping.get(regime, LegacyRegime.NEUTRAL)
