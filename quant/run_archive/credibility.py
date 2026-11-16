"""历史运行档案四档可信度（c41 / REQ-RUN-03）。

运行档案（run archive）中的每条记录需标注其可信度来源，便于事后审计区分
「经对账确认的真实记录」与「由模型 / 计划推算的推测值」与「来源不明的遗留数据」。

四档：
- ACTUAL_CONFIRMED     经对账（reconciliation）确认的真实记录
- PLAN_INFERRED       由决策计划账（第二本账）推算
- MODEL_INFERRED      由模型目标账（第一本账）推算
- UNKNOWN_LEGACY_STATE 历史遗留、来源不明
"""

from __future__ import annotations

from enum import Enum


class RunCredibility(str, Enum):
    """运行档案可信度四档。"""

    ACTUAL_CONFIRMED = "ACTUAL_CONFIRMED"
    PLAN_INFERRED = "PLAN_INFERRED"
    MODEL_INFERRED = "MODEL_INFERRED"
    UNKNOWN_LEGACY_STATE = "UNKNOWN_LEGACY_STATE"

    @property
    def is_confirmed(self) -> bool:
        return self is RunCredibility.ACTUAL_CONFIRMED

    @property
    def is_inferred(self) -> bool:
        return self in (RunCredibility.PLAN_INFERRED, RunCredibility.MODEL_INFERRED)

    @property
    def is_unknown(self) -> bool:
        return self is RunCredibility.UNKNOWN_LEGACY_STATE


def tag_run(*, source: str, has_reconciliation: bool = False) -> RunCredibility:
    """根据来源标注一条运行记录的可信度。

    Args:
        source: 来源标识，建议含 ``PLAN`` / ``MODEL`` / ``LEGACY`` 等关键字。
        has_reconciliation: 是否经对账确认（最高可信度）。
    """
    if has_reconciliation:
        return RunCredibility.ACTUAL_CONFIRMED
    s = (source or "").upper()
    if "LEGACY" in s or s == "":
        return RunCredibility.UNKNOWN_LEGACY_STATE
    if "PLAN" in s:
        return RunCredibility.PLAN_INFERRED
    if "MODEL" in s:
        return RunCredibility.MODEL_INFERRED
    return RunCredibility.UNKNOWN_LEGACY_STATE


# 可信度排序（数值越大越可信），用于「取最可信记录」等聚合
CREDIBILITY_ORDER = {
    RunCredibility.UNKNOWN_LEGACY_STATE: 0,
    RunCredibility.MODEL_INFERRED: 1,
    RunCredibility.PLAN_INFERRED: 2,
    RunCredibility.ACTUAL_CONFIRMED: 3,
}


def most_credible(records: list[tuple[str, RunCredibility]]) -> RunCredibility | None:
    """从一组 (标识, 可信度) 中取最可信的一档。"""
    if not records:
        return None
    return max(records, key=lambda r: CREDIBILITY_ORDER[r[1]])[1]
