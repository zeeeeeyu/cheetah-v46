"""Calendar-group primitives (ARCH-CALENDAR-001, docs/CALENDAR_GROUPS.md).

A calendar group is the unit at which "is this data the latest available" and "how old is
this signal" are answered.  CN / HK / QDII do not share a trading calendar, a NAV
disclosure schedule or a settlement cycle, so a single global answer is always wrong for
at least one of them.

Assignment is fail-closed: an asset that cannot be resolved to exactly one group is
rejected rather than silently defaulted to ``CN``.
"""

from __future__ import annotations

from typing import Any, Mapping

CALENDAR_GROUPS: tuple[str, ...] = ("CN", "HK", "QDII")

_CN_EXCHANGES = {"SSE", "SZSE"}
_HK_EXCHANGES = {"HKEX", "SEHK"}
_HK_NAME_MARKERS = ("港股", "恒生", "H股", "HANG SENG", "HSCEI")


class CalendarGroupError(ValueError):
    """An asset could not be assigned to exactly one calendar group."""


def normalize_calendar_group(value: Any) -> str:
    group = str(value or "").strip().upper()
    if group not in CALENDAR_GROUPS:
        raise CalendarGroupError(
            f"非法日历分组：{value!r}；只能是 {'/'.join(CALENDAR_GROUPS)}"
        )
    return group


def resolve_calendar_group(payload: Mapping[str, Any]) -> str:
    """Assign one calendar group using the deterministic rules of CALENDAR_GROUPS.md §1.3.

    Priority: explicit declaration > QDII fund type > cross-border asset type > mainland
    exchange > Hong Kong exchange/name marker > reject.  There is deliberately no default:
    an unrecognised asset must not inherit the A-share calendar by accident.
    """
    declared = payload.get("calendar_group")
    if declared not in (None, ""):
        return normalize_calendar_group(declared)

    symbol = str(payload.get("symbol", "?"))
    fund_type = str(payload.get("fund_type") or "").upper()
    if "QDII" in fund_type:
        return "QDII"
    if str(payload.get("asset_type") or "").upper() == "CROSS_BORDER":
        return "QDII"

    exchange = str(payload.get("exchange") or "").upper()
    if exchange in _CN_EXCHANGES:
        return "CN"
    name = str(payload.get("name") or "").upper()
    if exchange in _HK_EXCHANGES or any(marker in name for marker in _HK_NAME_MARKERS):
        return "HK"

    raise CalendarGroupError(
        f"{symbol} 未声明 calendar_group 且无法从 fund_type/asset_type/exchange 推断；"
        "按 Fail-Closed 原则拒绝加载，请在主数据中显式声明 CN/HK/QDII"
    )
