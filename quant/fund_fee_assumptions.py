from __future__ import annotations

"""Per-fund research fee assumptions for strategy governance.

Live execution still uses a same-day sales-platform snapshot.  These assumptions are only for
historical research and are deliberately separate so a C-class zero-fee assumption cannot silently
spill over to an A-class/QDII-LOF fund.
"""

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from .config import project_path
from .fund_universe import FundInstrument


@dataclass(frozen=True)
class FeeAssumption:
    symbol: str
    share_class: str
    subscription_fee_rate: float | None
    redemption_fee_rate: float | None
    mode: str
    note: str

    @property
    def usable(self) -> bool:
        return (
            self.subscription_fee_rate is not None
            and self.redemption_fee_rate is not None
            and self.subscription_fee_rate >= 0
            and self.redemption_fee_rate >= 0
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "share_class": self.share_class,
            "subscription_fee_rate": self.subscription_fee_rate,
            "redemption_fee_rate": self.redemption_fee_rate,
            "mode": self.mode,
            "note": self.note,
            "usable": self.usable,
        }


def default_fee_assumption_payload(instruments: list[FundInstrument]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "purpose": "仅用于 V4.5 历史策略治理研究。实盘申赎仍必须以销售平台当日申赎状态与费率为准。",
        "missing_fee_policy": "EXCLUDE_FROM_RESEARCH",
        "funds": {
            item.symbol: {
                "share_class": item.share_class,
                "subscription_fee_rate": None,
                "redemption_fee_rate": None,
                "mode": "MANUAL_VERIFY" if item.share_class != "C" else "C_LONG_HOLD_ZERO_OPTIONAL",
                "note": (
                    "C 类若用户明确确认研究期按超过持有门槛零费率，可在治理运行时启用；"
                    "这不构成真实历史费率审计。"
                    if item.share_class == "C"
                    else "A 类 / LOF / QDII 需逐只填写研究成本，或在治理对比中透明排除。"
                ),
            }
            for item in instruments
        },
    }


def fee_assumption_path() -> Path:
    return project_path("state", "fund_research_fee_assumptions.json")


def load_fee_assumption_payload(instruments: list[FundInstrument]) -> dict[str, Any]:
    path = fee_assumption_path()
    base = default_fee_assumption_payload(instruments)
    if not path.exists():
        return base
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return base
    merged = dict(base)
    merged.update({key: value for key, value in raw.items() if key != "funds"})
    old_funds = raw.get("funds", {}) if isinstance(raw.get("funds"), dict) else {}
    merged_funds = base["funds"]
    for symbol, data in old_funds.items():
        if symbol in merged_funds and isinstance(data, dict):
            merged_funds[symbol].update(data)
    merged["funds"] = merged_funds
    return merged


def save_fee_assumption_payload(payload: dict[str, Any]) -> Path:
    path = fee_assumption_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _to_rate(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def resolve_research_fee_table(
    instruments: list[FundInstrument],
    *,
    assume_c_class_over_30d: bool = False,
    global_subscription_fee: float | None = None,
    global_redemption_fee: float | None = None,
) -> tuple[dict[str, FeeAssumption], list[str], dict[str, str]]:
    """Resolve one explicit fee treatment per instrument.

    Returns ``fee_table``, notes, and cost-unverified symbols.  A global fee is retained only as
    an explicit research fallback.  It is never inferred from share class and is reported as such.
    """
    payload = load_fee_assumption_payload(instruments)
    records = payload.get("funds", {}) if isinstance(payload.get("funds"), dict) else {}
    table: dict[str, FeeAssumption] = {}
    notes: list[str] = []
    unavailable: dict[str, str] = {}

    if (global_subscription_fee is None) != (global_redemption_fee is None):
        raise ValueError("全局研究成本必须同时提供申购与赎回费率。")

    for inst in instruments:
        item = records.get(inst.symbol, {}) if isinstance(records.get(inst.symbol), dict) else {}
        sub = _to_rate(item.get("subscription_fee_rate"))
        red = _to_rate(item.get("redemption_fee_rate"))
        mode = str(item.get("mode", inst.fee_model) or inst.fee_model)
        note = str(item.get("note", ""))
        if sub is not None and red is not None:
            assumption = FeeAssumption(inst.symbol, inst.share_class, sub, red, "EXPLICIT_PER_FUND", note or "用户逐基金输入的研究成本假设。")
        elif inst.share_class == "C" and assume_c_class_over_30d:
            assumption = FeeAssumption(
                inst.symbol,
                inst.share_class,
                0.0,
                0.0,
                "USER_CONFIRMED_C_LONG_HOLD_ZERO",
                "用户明确选择：该 C 类份额按超过持有门槛、申购/赎回成本均为 0 的研究假设；不等于真实历史费率审计。",
            )
        elif global_subscription_fee is not None and global_redemption_fee is not None:
            assumption = FeeAssumption(
                inst.symbol,
                inst.share_class,
                float(global_subscription_fee),
                float(global_redemption_fee),
                "EXPLICIT_GLOBAL_RESEARCH_FALLBACK",
                "用户明确选择对所有参与基金使用统一研究费率；这不等于真实历史费用。",
            )
        else:
            assumption = FeeAssumption(inst.symbol, inst.share_class, None, None, "MISSING", note or "缺少逐基金研究成本假设。")
            unavailable[inst.symbol] = (
                f"{inst.symbol}（{inst.share_class}）缺少研究成本假设；不会自动套用 C 类零费率。"
            )
        table[inst.symbol] = assumption

    if unavailable:
        notes.append("未配置成本的基金按配置策略处理；默认透明排除，不把它们偷偷按零成本计算。")
    return table, notes, unavailable


def configure_research_fee_assumptions_interactive(instruments: list[FundInstrument]) -> int:
    payload = load_fee_assumption_payload(instruments)
    funds = payload.setdefault("funds", {})
    print("\n逐基金研究成本配置（仅用于 V4.5 策略治理；不会影响实盘下单）")
    print("C 类可选择按你明确确认的长期持有零费率研究；A/LOF/QDII 必须逐只填写假设，或留空让对比透明排除。")
    for inst in instruments:
        current = funds.setdefault(inst.symbol, {})
        print(f"\n{inst.symbol} {inst.name}｜份额类别 {inst.share_class}｜类型 {inst.fund_type}")
        print(f"当前申购/赎回研究费率：{current.get('subscription_fee_rate')} / {current.get('redemption_fee_rate')}")
        raw_sub = input("研究申购费率（例如 0、0.0015；留空保持/清空）：").strip()
        raw_red = input("研究赎回费率（例如 0、0.005；留空保持/清空）：").strip()
        if raw_sub:
            try:
                current["subscription_fee_rate"] = float(raw_sub)
            except ValueError:
                print("申购费率无效，保持原值。")
        if raw_red:
            try:
                current["redemption_fee_rate"] = float(raw_red)
            except ValueError:
                print("赎回费率无效，保持原值。")
        note = input("假设说明（可留空）：").strip()
        if note:
            current["note"] = note
        current["share_class"] = inst.share_class
        current["mode"] = "EXPLICIT_RESEARCH" if current.get("subscription_fee_rate") is not None and current.get("redemption_fee_rate") is not None else "MANUAL_VERIFY"
    if input("确认保存请输入 SAVE：").strip() != "SAVE":
        print("未保存。")
        return 0
    saved = save_fee_assumption_payload(payload)
    print(f"已保存：{saved}")
    return 0
