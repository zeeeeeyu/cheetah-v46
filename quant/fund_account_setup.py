from __future__ import annotations

"""One-time interactive setup for a real mixed A/C/LOF open-fund account.

The system deliberately does not guess share-class mappings.  Each actual platform code is copied
by the user.  Different share classes may coexist: a C-class long-held fund and an A-class QDII-LOF
are both valid direct-NAV instruments, but their fee treatment remains separate.
"""

import json
from datetime import date
from pathlib import Path
from typing import Any

from .config import load_json, project_path
from .fund_datahub import normalize_fund_code
from .fund_execution import load_fund_state, save_fund_state
from .fund_fee_assumptions import default_fee_assumption_payload, fee_assumption_path, save_fee_assumption_payload
from .fund_universe import FundInstrument


_ALLOWED_SHARE_CLASSES = {"A", "B", "C", "LOF", "MIXED", "UNKNOWN"}


def _save_json(relative: str, payload: Any) -> Path:
    path = project_path(relative)
    backup = path.with_suffix(path.suffix + ".before_mixed_account_setup.bak")
    if path.exists() and not backup.exists():
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _yes_no(prompt: str, default: bool = True) -> bool:
    raw = input(prompt).strip().lower()
    if not raw:
        return default
    return raw in {"y", "yes", "是", "1", "true"}


def _float_or_default(prompt: str, default: float) -> float:
    raw = input(prompt).strip()
    if not raw:
        return default
    return float(raw)


def _share_class(prompt: str, default: str) -> str:
    while True:
        raw = input(f"{prompt} [默认 {default}]：").strip().upper() or default.upper()
        if raw in _ALLOWED_SHARE_CLASSES:
            return raw
        print("  份额类别只能是 A / B / C / LOF / MIXED / UNKNOWN。")


def configure_mixed_class_account_interactive() -> int:
    settings = load_json("config/fund/settings.json")
    instruments = load_json("config/fund/instruments.json")
    previous_state = load_fund_state()

    print("\n一次性配置真实混合份额基金账户（A / C / LOF 可混合）")
    print("请填写销售平台显示的实际基金代码、份额类别和权重。系统不会猜测 A/C 对应关系。")
    print("160216 等 A 类 QDII-LOF 可以保留 A，不会被强制改成 C。\n")

    new_instruments: list[dict[str, Any]] = []
    new_weights: dict[str, float] = {}
    holding_days: dict[str, int] = {}
    benchmark_old = str(settings.get("benchmark_symbol", ""))
    benchmark_new: str | None = None

    for item in instruments:
        old_symbol = str(item["symbol"])
        old_name = str(item["name"])
        default_class = str(item.get("share_class", "UNKNOWN") or "UNKNOWN").upper()
        include = _yes_no(f"纳入真实候选池？{old_symbol} {old_name} [Y/n]：", default=True)
        if not include:
            continue
        while True:
            raw_symbol = input(f"  实际基金代码（对应 {old_name}）：").strip()
            symbol = normalize_fund_code(raw_symbol)
            if symbol and symbol.isdigit() and len(symbol) == 6:
                break
            print("  必须输入 6 位基金代码；不确定请在销售平台确认后再配置。")
        name = input(f"  基金名称 [默认：{old_name}]：").strip() or old_name
        share_class = _share_class("  份额类别 A/C/LOF", default_class)
        fund_type = input(f"  基金类型 [默认：{item.get('fund_type', 'OPEN_FUND')}]：").strip().upper() or str(item.get("fund_type", "OPEN_FUND")).upper()
        weight_pct = _float_or_default("  当前实际市值权重 % [未持有填 0]：", 0.0)
        if weight_pct < 0:
            print("  权重不能为负，已取消，未写入任何配置。")
            return 1
        days = int(_float_or_default("  当前持有天数 [未持有填 0]：", 0.0))
        updated = dict(item)
        updated.update({
            "symbol": symbol,
            "name": name,
            "signal_symbol": symbol,
            "trade_symbol": symbol,
            "share_class": share_class,
            "fund_type": fund_type,
            "is_c_class": share_class == "C",
            "fee_model": "C_LONG_HOLD_ZERO" if share_class == "C" else "MANUAL_VERIFY",
            "active": True,
        })
        new_instruments.append(updated)
        if weight_pct > 0:
            new_weights[symbol] = weight_pct / 100.0
            holding_days[symbol] = max(0, days)
        if old_symbol == benchmark_old:
            benchmark_new = symbol

    if not new_instruments:
        print("未选择任何真实基金，未保存。")
        return 1
    if not benchmark_new:
        print("基准基金未映射，未保存。必须为原基准基金填写实际代码。")
        return 1
    total = sum(new_weights.values())
    if total > 1.0 + 1e-9:
        print(f"当前基金权重合计 {total:.2%} 超过 100%，未保存。")
        return 1
    new_weights["CASH"] = max(0.0, 1.0 - total)

    portfolio_value_raw = input("当前组合总市值（元；可留空）：").strip()
    last_trade = input("最近一次实际申购/赎回提交日 YYYY-MM-DD（可留空）：").strip()
    print("\n即将写入：")
    for symbol, weight in new_weights.items():
        print(f"- {symbol}: {weight:.2%}")
    print(f"- 基准基金：{benchmark_new}")
    print("确认无误请输入 SAVE：", end="")
    if input().strip() != "SAVE":
        print("未保存。")
        return 0

    settings["benchmark_symbol"] = benchmark_new
    settings.setdefault("account_profile", {})
    settings["account_profile"].update({
        "expected_share_class": "MIXED",
        "mapping_confirmed": True,
        "configured_on": str(date.today()),
        "note": "用户在销售平台确认真实 A/C/LOF 代码、当前权重和持有天数；后续只使用这些同代码直接净值。",
    })
    state = dict(previous_state)
    state.update({
        "schema_version": max(3, int(state.get("schema_version", 1))),
        "mode": "FUND_NAV_ONLY",
        "current_weights": new_weights,
        "holding_days_by_symbol": holding_days,
        "last_executed_trade_date": last_trade or state.get("last_executed_trade_date"),
        "account_profile_confirmed": True,
        "note": "真实混合份额基金账户状态；基金代码、份额类别、权重和持有天数由用户在销售平台确认。",
    })
    if portfolio_value_raw:
        state["portfolio_value_cny"] = float(portfolio_value_raw)

    snapshot = {
        "schema_version": 3,
        "mode": "FUND_NAV_ONLY",
        "as_of_date": None,
        "funds": {
            item["symbol"]: {
                "purchase_status": "UNKNOWN",
                "redemption_status": "UNKNOWN",
                "subscription_fee_rate": None,
                "redemption_fee_rate": None,
                "fee_verified": False,
                "source_note": "仅在策略出现调整候选时，按销售平台当日页面核实。",
            }
            for item in new_instruments
        },
        "note": "交易可行性快照。缺失时不掩盖策略方向，但计划会标为待确认，绝不自动下单。",
    }

    _save_json("config/fund/instruments.json", new_instruments)
    _save_json("config/fund/settings.json", settings)
    save_fund_state(state)
    _save_json("state/fund_tradeability_snapshot.json", snapshot)
    # Create a transparent per-fund research fee skeleton; it does not guess any A/LOF cost.
    typed = [FundInstrument.from_dict(item) for item in new_instruments]
    fee_path = fee_assumption_path()
    if not fee_path.exists():
        save_fee_assumption_payload(default_fee_assumption_payload(typed))
    print("已保存真实混合份额账户配置。下一次 main_run.py 会自动请求这些同代码直接累计净值。")
    print("V4.5 对比若含 A/LOF，请在菜单中配置逐基金研究成本，或让它透明排除未配置基金。")
    return 0


# Backward-compatible import name used by old main_run.py/menu code.
def configure_c_class_account_interactive() -> int:
    return configure_mixed_class_account_interactive()
