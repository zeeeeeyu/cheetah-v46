"""快慢状态合成：取更保守者（c39 / REQ-STATE-03）。

快状态（``MarketRegime``，反应快）与慢状态（``SentinelLevel``，降级需多级确认）
来自不同轴。合成决策状态时以「更保守者」为准——任一轴认为应降风险，则整体降风险。

历史缺陷：此前两轴各自独立判断，慢轴的 DATA_ERROR（无法判断市场）未能把快轴
也拉到 Risk-Off，导致「哨兵判不出状态」时策略仍按快轴的乐观状态运行。本模块
统一把两轴映射到同一保守严重度标尺后取 max。
"""

from __future__ import annotations

from dataclasses import dataclass

from quant.domain.enums import MarketRegime
from quant.sentinel.levels import SentinelLevel


# 保守严重度标尺（数值越大越保守；与两枚举各自顺序一致）
_REGIME_SEVERITY = {
    MarketRegime.TREND_RISK_ON: 0,
    MarketRegime.RECOVERY_PROBE: 1,
    MarketRegime.RANGE_NEUTRAL: 1,
    MarketRegime.UNKNOWN: 2,
    MarketRegime.STRESS_RISK_OFF: 3,
}
_SENTINEL_SEVERITY = {
    SentinelLevel.GREEN: 0,
    SentinelLevel.YELLOW: 1,
    SentinelLevel.ORANGE: 2,
    SentinelLevel.RED: 3,
    SentinelLevel.DATA_ERROR: 4,
}


@dataclass(frozen=True)
class SynthesizedState:
    """快慢状态合成结果。"""

    regime: MarketRegime
    sentinel: SentinelLevel
    severity: int
    basis: str  # 哪个轴更保守："regime" / "sentinel"

    @property
    def is_risk_off(self) -> bool:
        """任一阵营达到 ORANGE 及以上（或数据失效）即视为降风险。"""
        return (
            self.sentinel.value >= SentinelLevel.ORANGE.value
            or self.regime == MarketRegime.STRESS_RISK_OFF
        )


def synthesize(fast: MarketRegime, slow: SentinelLevel) -> SynthesizedState:
    """合成快慢状态，取更保守者。

    DATA_ERROR 被刻意排在最高严重度——「哨兵无法判断市场」必须至少与
    「已知最坏状态」同等保守，从而把快轴也拉到降风险。
    """
    fs = _REGIME_SEVERITY.get(fast, 2)
    ss = _SENTINEL_SEVERITY.get(slow, 4)
    if ss >= fs:
        return SynthesizedState(fast, slow, ss, "sentinel")
    return SynthesizedState(fast, slow, fs, "regime")
