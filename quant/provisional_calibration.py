"""Wave 3：PROVISIONAL 参数校准闸门 harness（D-01 遗留动作）。

D-01 决策规定：4 个 PROVISIONAL 参数保留暂定值，等 Wave 3 回测基础设施就绪后，
以 ``verify_provisional_values`` 为**闸门**统一校正。

本模块提供校准 harness：
- ``propose_calibration`` 对候选取值给出「建议 + 依据 + 置信度」，但**不自动改数**；
  最终取值需人工结合回测复核后才能从登记表移除。
- ``is_calibration_gated`` 确认闸门（verify_provisional_values）已就绪。

诚实边界：当前 4 个参数**仍为 PROVISIONAL**。本模块不伪造任何回测依据——
默认对所有参数给出 ``confidence=LOW`` 并明确标注「需人工回测复核」。真正的数值校正
属于策略决策，必须由带回测证据的人工评审完成，不应由代码静默写入。
"""

from __future__ import annotations

from dataclasses import dataclass

from quant.provisional import PROVISIONAL_KEYS, PROVISIONAL_PARAMETERS


@dataclass
class CalibrationProposal:
    """单条参数的校准建议。"""

    key: str
    current: float
    candidate: float
    basis: str
    confidence: str  # LOW / MEDIUM / HIGH
    note: str


def propose_calibration(
    candidates: dict[str, float] | None = None,
) -> list[CalibrationProposal]:
    """对（可选提供的）候选值给出校准建议。

    - ``candidates`` 为空：返回「维持现状 PROVISIONAL」的建议（不伪造回测依据）。
    - 即使提供了候选值，默认置信度仍为 ``LOW``——除非调用方附带来源可信的回测证据，
      否则本模块不会把 PROVISIONAL 标成已校准。

    修改 ``PROVISIONAL`` 取值需同时更新 ``quant/provisional.py`` 登记表与
    ``config/settings.json``，并经 ``verify_provisional_values`` 闸门复核。
    """
    proposals: list[CalibrationProposal] = []
    for key in PROVISIONAL_KEYS:
        current = float(PROVISIONAL_PARAMETERS[key]["current"])
        cand = float(candidates.get(key, current)) if candidates else current
        proposals.append(
            CalibrationProposal(
                key=key,
                current=current,
                candidate=cand,
                basis="maintained_provisional" if cand == current else "candidate_provided",
                confidence="LOW",
                note=(
                    "PROVISIONAL 维持不变：尚无人类复核的回测依据（D-01 方案 A）。"
                    if cand == current
                    else "候选值已记录，但置信度 LOW：需人工回测复核后方可从登记表移除。"
                ),
            )
        )
    return proposals


def is_calibration_gated() -> bool:
    """校准必须经由 verify_provisional_values 闸门；该函数就绪即视为闸门可用。"""
    try:
        from quant.provisional import verify_provisional_values
        from quant.config import load_json

        verify_provisional_values(load_json("config/settings.json"))
        return True
    except Exception:
        return False
