"""市场哨兵引擎：指标读数 + 事件 + 非对称迟滞 → 稳定的市场风险等级。

工作流::

    reading (指标)  ─┐
                     ├─> compute_raw_level ─> raw ─┐
    events (事件)   ─┘                             ├─> 迟滞 ─> level
                                    current level ─┘

设计要点：
  * 原始等级由定量指标与已确认事件共同决定，取两者较高者（防御优先）。
  * 迟滞非对称：升级即时、降级需连续确认且每次只降一级。
  * 全过程无副作用地产出 SentinelDecision（含原因），便于审计与快照。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .events import SentinelEvent, SentinelEventLog
from .levels import SentinelLevel, next_level_down


@dataclass(frozen=True)
class SentinelReading:
    """市场哨兵的定量指标读数（越"坏"值越大者，阈值方向已在下方注明）。"""
    as_of: str
    drawdown: float | None = None              # 距高点回撤（正数，如 0.12 = -12%）
    volatility_percentile: float | None = None # 实现波动率分位（0-1，越高越危险）
    breadth_above_ma50: float | None = None    # MA50 以上个股比例（0-1，越低越危险）
    downside_vol: float | None = None          # 下行波动率（越高越危险）
    cross_asset_stress: float | None = None    # 跨资产压力（0-1，越高越危险）


# ── 阈值（可调；默认值来自防御性经验，宁可早预警） ──────────────────
_DRAWDOWN_YELLOW = 0.05
_DRAWDOWN_ORANGE = 0.10
_DRAWDOWN_RED = 0.18
_VOL_PCT_YELLOW = 0.70
_VOL_PCT_ORANGE = 0.85
_VOL_PCT_RED = 0.95
_BREADTH_YELLOW = 0.50   # 低于该比例 → 至少 YELLOW
_BREADTH_ORANGE = 0.35
_BREADTH_RED = 0.20
_STRESS_YELLOW = 0.50
_STRESS_ORANGE = 0.70
_STRESS_RED = 0.85


def _level_from_thresholds(value: float, yellow: float, orange: float, red: float, *, higher_is_worse: bool) -> int:
    """将单指标映射到 0-3 等级。"""
    if higher_is_worse:
        if value >= red:
            return 3
        if value >= orange:
            return 2
        if value >= yellow:
            return 1
        return 0
    else:  # lower is worse (如市场宽度)
        if value <= red:
            return 3
        if value <= orange:
            return 2
        if value <= yellow:
            return 1
        return 0


# 哨兵可用的风险指标总数（drawdown / volatility / breadth / cross_asset_stress）
_TOTAL_INDICATORS = 4

# 低于该覆盖率时判断不可靠，等级下限抬到 YELLOW
_MIN_INDICATOR_COVERAGE = 0.5


def compute_raw_level(
    reading: SentinelReading,
    event_log: SentinelEventLog | None = None,
) -> tuple[SentinelLevel, list[str]]:
    """由指标与事件计算**原始**等级（未经迟滞）。取所有信号的最高等级。

    **数据失效必须 Fail-Closed**（对应 GAP-3 / REQ-MKT-05）：

    * 无任何可用指标 → ``DATA_ERROR``，而不是 GREEN。
      此前的实现是 ``max(scores) if scores else 0``，意味着数据源全挂时
      哨兵报告绿灯 —— 系统在最该报警的时刻反而最安静。
    * 指标覆盖率低于 ``_MIN_INDICATOR_COVERAGE`` → 判断不可靠，等级下限抬到
      ``YELLOW``，并在理由中记录覆盖率，供人工复核。

    覆盖率阈值**刻意不做成入参**。可选的严格等于不严格：一旦允许调用方传
    ``min_indicator_coverage=0.0``，这条保护就等同于 StockSharp 的
    ``throwOnInvalid=false``（本项目明确否决的 Fail-Open 反面教材）。
    需要调整阈值请改模块常量，让改动出现在 diff 里、经过评审。
    """
    scores: list[int] = []
    reasons: list[str] = []

    if reading.drawdown is not None:
        s = _level_from_thresholds(reading.drawdown, _DRAWDOWN_YELLOW, _DRAWDOWN_ORANGE, _DRAWDOWN_RED, higher_is_worse=True)
        scores.append(s)
        if s > 0:
            reasons.append(f"回撤 {reading.drawdown:.1%} → L{s}")
    if reading.volatility_percentile is not None:
        s = _level_from_thresholds(reading.volatility_percentile, _VOL_PCT_YELLOW, _VOL_PCT_ORANGE, _VOL_PCT_RED, higher_is_worse=True)
        scores.append(s)
        if s > 0:
            reasons.append(f"波动分位 {reading.volatility_percentile:.2f} → L{s}")
    if reading.breadth_above_ma50 is not None:
        s = _level_from_thresholds(reading.breadth_above_ma50, _BREADTH_YELLOW, _BREADTH_ORANGE, _BREADTH_RED, higher_is_worse=False)
        scores.append(s)
        if s > 0:
            reasons.append(f"市场宽度 {reading.breadth_above_ma50:.2f} → L{s}")
    if reading.cross_asset_stress is not None:
        s = _level_from_thresholds(reading.cross_asset_stress, _STRESS_YELLOW, _STRESS_ORANGE, _STRESS_RED, higher_is_worse=True)
        scores.append(s)
        if s > 0:
            reasons.append(f"跨资产压力 {reading.cross_asset_stress:.2f} → L{s}")

    coverage = len(scores) / _TOTAL_INDICATORS
    if not scores:
        # Fail-Closed：没有任何可用指标 ≠ 市场平静，而是「无法判断」
        reasons.append("无任何可用风险指标 → DATA_ERROR（Fail-Closed，不得视为 GREEN）")
        indicator_level = int(SentinelLevel.DATA_ERROR)
    else:
        indicator_level = max(scores)
        if coverage < _MIN_INDICATOR_COVERAGE:
            reasons.append(
                f"指标覆盖率 {coverage:.0%} 低于 {_MIN_INDICATOR_COVERAGE:.0%}，"
                f"判断不可靠 → 等级下限抬至 YELLOW"
            )
            indicator_level = max(indicator_level, int(SentinelLevel.YELLOW))

    event_level = 0
    if event_log is not None:
        event_level = event_log.max_implied_level()
        if event_level > 0:
            reasons.append(f"已确认事件 → L{event_level}")

    raw = max(indicator_level, event_level)
    return SentinelLevel(raw), reasons


@dataclass(frozen=True)
class SentinelDecision:
    """哨兵一次评估的输出（不可变，可入运行快照）。"""
    as_of: str
    level: SentinelLevel
    raw_level: SentinelLevel
    changed: bool
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of,
            "level": self.level.label,
            "raw_level": self.raw_level.label,
            "changed": self.changed,
            "allows_new_entries": self.level.allows_new_entries,
            "is_risk_off": self.level.is_risk_off,
            "reasons": list(self.reasons),
        }


class MarketSentinel:
    """有状态的市场哨兵，跨评估维持等级与降级确认计数。

    参数
    ----
    deescalation_confirmations:
        连续多少次"原始等级更低"才允许降一级（默认 2）。数值越大越稳。
    """

    def __init__(self, *, initial: SentinelLevel = SentinelLevel.GREEN, deescalation_confirmations: int = 2) -> None:
        self._level = initial
        self._deescalation_confirmations = max(1, int(deescalation_confirmations))
        self._deescalation_streak = 0

    @property
    def level(self) -> SentinelLevel:
        return self._level

    @property
    def deescalation_streak(self) -> int:
        return self._deescalation_streak

    def evaluate(self, reading: SentinelReading, event_log: SentinelEventLog | None = None) -> SentinelDecision:
        """基于读数与事件评估并推进哨兵状态，返回决策。"""
        raw, reasons = compute_raw_level(reading, event_log)
        previous = self._level

        if raw > self._level:
            # 升级：即时到位，重置降级计数。
            self._level = raw
            self._deescalation_streak = 0
            reasons.append(f"风险升级 {previous.label}→{self._level.label}（即时）")
        elif raw < self._level:
            # 候选降级：需连续确认，且每次仅降一级。
            self._deescalation_streak += 1
            if self._deescalation_streak >= self._deescalation_confirmations:
                stepped = next_level_down(self._level)
                reasons.append(
                    f"风险解除确认 {self._deescalation_streak}/{self._deescalation_confirmations} → "
                    f"{previous.label}→{stepped.label}（每次降一级）"
                )
                self._level = stepped
                self._deescalation_streak = 0
            else:
                reasons.append(
                    f"原始等级 {raw.label} 低于当前 {self._level.label}，"
                    f"降级确认中 {self._deescalation_streak}/{self._deescalation_confirmations}（维持不变）"
                )
        else:
            # 与当前等级相同：视为压力持续，打断降级确认。
            self._deescalation_streak = 0

        return SentinelDecision(
            as_of=reading.as_of,
            level=self._level,
            raw_level=raw,
            changed=self._level != previous,
            reasons=tuple(reasons),
        )
