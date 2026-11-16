from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from .config import project_path
from .fund_execution import build_fund_trade_plan
from .guard import weights_turnover

PENDING_PATH = project_path("state", "fund_paper_pending_order.json")
STATE_PATH = project_path("state", "fund_paper_state.json")


def load_fund_paper_state() -> dict[str, Any]:
    with STATE_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_fund_paper_state(state: dict[str, Any]) -> Path:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return STATE_PATH


def queue_fund_paper_order(signal_date: str, model: str, target_weights: dict[str, float]) -> Path:
    payload = {
        "signal_date": signal_date,
        "model": model,
        "target_weights": {key: float(value) for key, value in target_weights.items()},
        "note": "仅纸面订单：T 日净值信号后，T+1 基金可申赎日提交；按随后已披露净值模拟定价。",
    }
    PENDING_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return PENDING_PATH


def load_fund_pending_order() -> dict[str, Any]:
    if not PENDING_PATH.exists():
        raise FileNotFoundError("没有待处理的场外基金纸面订单。请先人工审核并入队。")
    with PENDING_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def clear_fund_pending_order() -> None:
    if PENDING_PATH.exists():
        PENDING_PATH.unlink()


def mark_fund_to_nav(state: dict[str, Any], frames: dict[str, pd.DataFrame], as_of_date: str, cash_symbol: str = "CASH") -> dict[str, Any]:
    target_date = pd.Timestamp(as_of_date).normalize()
    if state.get("last_mark_date") == str(target_date.date()):
        return state
    if state.get("last_mark_date") is None:
        closes: dict[str, float] = {}
        for symbol, frame in frames.items():
            sub = frame.loc[pd.to_datetime(frame["date"]).dt.normalize() <= target_date]
            if not sub.empty:
                closes[symbol] = float(sub.iloc[-1]["nav"])
        state["last_nav"] = closes
        state["last_mark_date"] = str(target_date.date())
        return state

    factor = 0.0
    new_nav = dict(state.get("last_nav", {}))
    for symbol, weight in state.get("weights", {}).items():
        if symbol == cash_symbol:
            factor += float(weight)
            continue
        sub = frames[symbol].loc[pd.to_datetime(frames[symbol]["date"]).dt.normalize() <= target_date]
        if sub.empty or symbol not in state.get("last_nav", {}):
            raise RuntimeError(f"纸面标记缺少 {symbol} 在 {target_date.date()} 的净值")
        current = float(sub.iloc[-1]["nav"])
        factor += float(weight) * current / float(state["last_nav"][symbol])
        new_nav[symbol] = current
    state["nav"] = float(state["nav"]) * factor
    state["last_nav"] = new_nav
    state["last_mark_date"] = str(target_date.date())
    return state


def execute_fund_pending_at_future_nav(
    state: dict[str, Any],
    pending: dict[str, Any],
    frames: dict[str, pd.DataFrame],
    pricing_date: str,
    tradeability: dict[str, Any],
    instruments: dict[str, dict[str, Any]],
    execution_config: dict[str, Any],
    cash_symbol: str = "CASH",
) -> tuple[dict[str, Any], pd.DataFrame]:
    """Paper execution after a future disclosed NAV becomes available.

    The pricing date must be later than the signal date.  The function does not pretend to know the
    execution NAV at signal time and does not call any real trading endpoint.
    """
    signal_date = pd.Timestamp(pending["signal_date"]).normalize()
    pricing_ts = pd.Timestamp(pricing_date).normalize()
    if pricing_ts <= signal_date:
        raise ValueError("场外基金纸面定价日必须晚于信号日，禁止同日净值成交。")
    state = mark_fund_to_nav(state, frames, str(pricing_ts.date()), cash_symbol)
    current = {key: float(value) for key, value in state.get("weights", {}).items()}
    target = {key: float(value) for key, value in pending["target_weights"].items()}
    plan = build_fund_trade_plan(
        current_weights=current,
        target_weights=target,
        as_of_date=str(signal_date.date()),
        instruments=instruments,
        tradeability=tradeability,
        execution_config=execution_config,
        portfolio_value_cny=None,
        cash_symbol=cash_symbol,
    )
    cost = 0.0
    for _, row in plan.iterrows():
        if row["action"] in {"SUBSCRIBE", "REDEEM"} and pd.notna(row["fee_rate_verified"]):
            cost += abs(float(row["delta_weight"])) * float(row["fee_rate_verified"])
    state["nav"] = float(state["nav"]) * (1.0 - cost)
    state["weights"] = target
    state["last_mark_date"] = str(pricing_ts.date())
    # New positions start from the actually disclosed pricing NAV date, not a fabricated intraday price.
    state["last_nav"] = {
        symbol: float(frame.loc[pd.to_datetime(frame["date"]).dt.normalize() <= pricing_ts, "nav"].iloc[-1])
        for symbol, frame in frames.items()
        if not frame.loc[pd.to_datetime(frame["date"]).dt.normalize() <= pricing_ts].empty
    }
    return state, plan
