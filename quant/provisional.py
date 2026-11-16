"""V4.6 未冻结参数（PROVISIONAL）登记与校验。

D-01 决策（采纳方案 A）：保留 ``config/settings.json`` 的分叉值不变，统一标记
``PROVISIONAL``，等 Wave 3 回测基础设施就绪后统一校正。本模块是「标记 + 校验」的
单一事实来源，**不改动任何参数取值本身**——避免在没有回测依据时改变策略行为。

登记项：点号路径 → {当前生效值 current, 参考默认值 reference_default, 所属段 section}。
``current`` 必须与 ``config/settings.json`` 实际值保持一致；``reference_default`` 仅用于
Wave 3 校正对照（原 dataclass 默认值：252 / 1000万 / 0.40 / 0.25）。
"""

from __future__ import annotations

from typing import Any

PROVISIONAL_PARAMETERS: dict[str, dict[str, Any]] = {
    "minimum_history_days": {
        "current": 126,
        "reference_default": 252,
        "section": "root",
    },
    "data.min_median_turnover_20d": {
        "current": 100_000_000,
        "reference_default": 10_000_000,
        "section": "data",
    },
    "portfolio.max_single_weight": {
        "current": 0.45,
        "reference_default": 0.40,
        "section": "portfolio",
    },
    "execution.max_non_emergency_turnover": {
        "current": 0.35,
        "reference_default": 0.25,
        "section": "execution",
    },
}

PROVISIONAL_KEYS: list[str] = list(PROVISIONAL_PARAMETERS.keys())


def is_provisional(key: str) -> bool:
    """判断某参数是否为 PROVISIONAL。"""
    return key in PROVISIONAL_PARAMETERS


def _resolve(settings: dict, dotted: str) -> Any:
    cur: Any = settings
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise KeyError(dotted)
        cur = cur[part]
    return cur


def get_provisional_parameters(settings: dict) -> dict[str, Any]:
    """按点号路径从 settings 取出所有 PROVISIONAL 参数的当前生效值。"""
    return {key: _resolve(settings, key) for key in PROVISIONAL_KEYS}


def verify_provisional_markers(settings: dict) -> list[str]:
    """校验 settings 是否登记了 ``provisional_parameters`` 且覆盖完整。

    返回缺失的键列表；空列表表示登记完整。
    """
    listed = set(settings.get("provisional_parameters", []))
    return sorted(set(PROVISIONAL_KEYS) - listed)


def verify_provisional_values(settings: dict) -> list[str]:
    """校验 settings 中实际生效值与本登记 ``current`` 一致。

    返回不一致的键列表（格式 ``key: settings值!=current值``）；空列表表示一致。
    """
    problems: list[str] = []
    for key, meta in PROVISIONAL_PARAMETERS.items():
        try:
            actual = _resolve(settings, key)
        except KeyError:
            problems.append(f"{key}: missing-in-settings")
            continue
        if actual != meta["current"]:
            problems.append(f"{key}: {actual}!={meta['current']}")
    return problems
