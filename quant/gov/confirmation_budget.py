"""ConfirmationBudget：时间 / 次数 / 额度三维预算（c38 / REQ-BUDGET-01）。

``RiskBudget`` 仅有额度维度；``ConfirmationBudget`` 增加「时间窗口内允许的次数」与
「时间窗口内允许的累计额度」两维，用于约束人工确认 / 下单频度，防止失控。

Fail-Closed 语义：预算耗尽时 ``can_confirm`` 一律返回 ``False``，消费方必须据此
放弃或升级，而非悄悄放行。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any


@dataclass
class ConfirmationBudget:
    """三维预算追踪器（滑动时间窗）。

    - ``window_seconds``：滑动窗口长度（秒）。
    - ``max_count``：窗口内最大确认次数。
    - ``max_amount``：窗口内最大累计金额（货币单位）。
    """

    window_seconds: float
    max_count: int
    max_amount: float
    _events: list[tuple[datetime, float]] = field(default_factory=list, repr=False)

    def _prune(self, now: datetime) -> None:
        cutoff = now - timedelta(seconds=self.window_seconds)
        self._events = [(t, a) for (t, a) in self._events if t >= cutoff]

    def remaining(self, now: datetime | None = None) -> tuple[int, float]:
        """返回 (剩余次数, 剩余额度)。"""
        now = now or datetime.now()
        self._prune(now)
        used_count = len(self._events)
        used_amount = sum(a for _, a in self._events)
        return self.max_count - used_count, self.max_amount - used_amount

    def can_confirm(self, amount: float = 0.0, now: datetime | None = None) -> bool:
        rem_count, rem_amount = self.remaining(now)
        return rem_count >= 1 and rem_amount >= amount

    def consume(self, amount: float = 0.0, now: datetime | None = None) -> bool:
        """尝试消费一次预算；成功返回 True 并登记，失败返回 False（Fail-Closed）。"""
        now = now or datetime.now()
        if not self.can_confirm(amount, now):
            return False
        self._events.append((now, amount))
        return True

    def reset(self) -> None:
        self._events.clear()
