"""市场哨兵（V4.6 审计建议修改顺序第 4 步 / P0）。

防御性、状态感知的市场风险等级系统：
  * 四级 GREEN / YELLOW / ORANGE / RED；
  * 非对称迟滞：升级即时、降级需连续确认且每次仅降一级；
  * 事件唯一键 + 去重 + 幂等；已确认黑天鹅事件可强制 RED；
  * 单日噪声不翻级；全过程产出可审计的 SentinelDecision。
"""

from __future__ import annotations

from .events import (
    ORANGE_EVENT_TYPES,
    RED_EVENT_TYPES,
    SentinelEvent,
    SentinelEventLog,
)
from .levels import (
    SentinelLevel,
    escalate_immediately,
    next_level_down,
)
from .sentinel import (
    MarketSentinel,
    SentinelDecision,
    SentinelReading,
    compute_raw_level,
)

__all__ = [
    # levels
    "SentinelLevel",
    "escalate_immediately",
    "next_level_down",
    # events
    "SentinelEvent",
    "SentinelEventLog",
    "RED_EVENT_TYPES",
    "ORANGE_EVENT_TYPES",
    # sentinel
    "MarketSentinel",
    "SentinelReading",
    "SentinelDecision",
    "compute_raw_level",
]
