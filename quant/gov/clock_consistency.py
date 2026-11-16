"""三时钟一致性校验（c40 / REQ-DATA-04）。

数据时钟（行情落地时间）、决策时钟（策略生成决策时间）、执行时钟（订单成交时间）
三者必须单调对齐：

- 决策不得早于其依赖的数据（否则等于用未来数据决策）；
- 执行不得早于其依赖的决策（否则等于决策未生成就成交）。

此前三时钟字段零散存在于各处，无统一校验，时间错乱只能在事故后回溯。本模块
提供统一原语，供决策引擎与执行链路在入口处断言。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class ClockCheck:
    ok: bool
    reason: str
    data_ts: Optional[datetime]
    decision_ts: Optional[datetime]
    execution_ts: Optional[datetime]


def check_three_clocks(
    *,
    data_ts: Optional[datetime],
    decision_ts: Optional[datetime],
    execution_ts: Optional[datetime] = None,
) -> ClockCheck:
    """校验三时钟单调对齐。

    Returns:
        ClockCheck：``ok`` 为 False 即存在时间错乱（应触发 Fail-Closed）。
    """
    if decision_ts is None:
        # 无决策则无需校验执行
        return ClockCheck(True, "无决策，跳过校验", data_ts, decision_ts, execution_ts)

    if data_ts is None:
        return ClockCheck(
            False,
            "有决策但无数据时钟——可能用未来/不存在的数据决策",
            data_ts, decision_ts, execution_ts,
        )

    if decision_ts < data_ts:
        return ClockCheck(
            False,
            f"决策时间 {decision_ts} 早于数据时间 {data_ts}",
            data_ts, decision_ts, execution_ts,
        )

    if execution_ts is not None and execution_ts < decision_ts:
        return ClockCheck(
            False,
            f"执行时间 {execution_ts} 早于决策时间 {decision_ts}",
            data_ts, decision_ts, execution_ts,
        )

    return ClockCheck(True, "三时钟单调对齐", data_ts, decision_ts, execution_ts)
