"""哨兵事件：唯一键、去重、幂等摄取。

市场哨兵会消费两类信号：
  1. 定量指标读数（见 sentinel.SentinelReading）；
  2. 离散市场事件（政策、黑天鹅、宏观冲击等）。

事件流的两个硬约束：
  * **去重**：同一事件（相同唯一键）只应被计入一次；
  * **幂等**：重复摄取同一事件不得重复触发等级变化或重复生成计划。

唯一键优先使用显式 event_id；缺失时由
(event_type, event_time, source, title) 组合派生稳定哈希。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Iterable


# RED 级事件类型：这些事件一旦确认，可强制把哨兵推到 RED（黑天鹅原则）。
RED_EVENT_TYPES: frozenset[str] = frozenset({"BLACK_SWAN", "SYSTEMIC_SHOCK", "CIRCUIT_BREAKER"})
# ORANGE 级事件类型：显著负面政策/宏观冲击。
ORANGE_EVENT_TYPES: frozenset[str] = frozenset({"POLICY_SHOCK", "MACRO_SHOCK", "LIQUIDITY_STRESS"})


@dataclass(frozen=True)
class SentinelEvent:
    """哨兵消费的市场事件（不可变）。

    与 quant.feat.event.Event 相互独立：feat.Event 服务于事件驱动的
    ETF 主题研究；SentinelEvent 服务于防御性的市场风险等级判定。
    """
    event_type: str
    event_time: str
    severity: str = "INFO"                     # INFO / WARNING / SEVERE / CRITICAL
    source: str = "MANUAL"
    title: str = ""
    event_id: str | None = None
    review_status: str = "UNREVIEWED"          # UNREVIEWED / CONFIRMED / REJECTED
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def unique_key(self) -> str:
        """稳定唯一键：优先 event_id，否则由关键字段派生哈希。"""
        if self.event_id:
            return self.event_id
        raw = f"{self.event_type}|{self.event_time}|{self.source}|{self.title}"
        return "SEV-" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

    @property
    def is_confirmed(self) -> bool:
        return self.review_status == "CONFIRMED"

    def implied_level(self) -> int:
        """事件隐含的哨兵等级（0-3）。仅已确认事件才有强制升级效力。

        未确认事件返回 0（不强制升级，避免噪声/谣言直接触发 Risk-Off）。
        """
        if not self.is_confirmed:
            return 0
        if self.event_type in RED_EVENT_TYPES or self.severity == "CRITICAL":
            return 3   # RED
        if self.event_type in ORANGE_EVENT_TYPES or self.severity == "SEVERE":
            return 2   # ORANGE
        if self.severity == "WARNING":
            return 1   # YELLOW
        return 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "unique_key": self.unique_key,
            "event_type": self.event_type,
            "event_time": self.event_time,
            "severity": self.severity,
            "source": self.source,
            "title": self.title,
            "review_status": self.review_status,
        }


class SentinelEventLog:
    """幂等的哨兵事件日志：按唯一键去重。"""

    def __init__(self) -> None:
        self._seen: dict[str, SentinelEvent] = {}
        self._order: list[str] = []

    def __len__(self) -> int:
        return len(self._seen)

    def has(self, event: SentinelEvent) -> bool:
        return event.unique_key in self._seen

    def ingest(self, event: SentinelEvent) -> bool:
        """摄取一个事件。返回 True 表示首次摄取，False 表示重复（被去重）。"""
        key = event.unique_key
        if key in self._seen:
            return False
        self._seen[key] = event
        self._order.append(key)
        return True

    def ingest_many(self, events: Iterable[SentinelEvent]) -> int:
        """批量摄取，返回真正新增（去重后）的事件数。"""
        return sum(1 for e in events if self.ingest(e))

    def events(self) -> list[SentinelEvent]:
        return [self._seen[k] for k in self._order]

    def max_implied_level(self) -> int:
        """当前所有已确认事件隐含的最高等级（0-3）。"""
        if not self._seen:
            return 0
        return max((e.implied_level() for e in self._seen.values()), default=0)
