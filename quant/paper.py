from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from .config import project_path
from .execution import build_trade_plan
from .guard import weights_turnover


PAPER_STATE_PATH = project_path("state", "paper_state.json")
PENDING_PATH = project_path("state", "paper_pending_order.json")


def _default_paper_state() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "mode": "PAPER_ONLY",
        "nav": 1.0,
        "last_mark_date": None,
        "weights": {"CASH": 1.0},
        "last_close": {},
        "note": "纸面账户。任何动作均不会向券商发送订单。",
    }


def load_paper_state() -> dict[str, Any]:
    if not PAPER_STATE_PATH.exists():
        return _default_paper_state()
    return json.loads(PAPER_STATE_PATH.read_text(encoding="utf-8"))


def save_paper_state(state: dict[str, Any]) -> None:
    PAPER_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def queue_paper_order(signal_date: str, model: str, target_weights: dict[str, float]) -> None:
    if PENDING_PATH.exists():
        raise RuntimeError("已有未执行纸面订单；请先执行或人工删除后再排队新的决策。")
    PENDING_PATH.write_text(
        json.dumps(
            {
                "signal_date": signal_date,
                "model": model,
                "target_weights": target_weights,
                "status": "QUEUED_T_PLUS_1",
                "note": "纸面订单仅可在信号日之后的下一可用交易日执行；不会向券商发送订单。",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def load_pending_order() -> dict[str, Any]:
    if not PENDING_PATH.exists():
        raise FileNotFoundError("没有待执行纸面订单。")
    return json.loads(PENDING_PATH.read_text(encoding="utf-8"))


def mark_to_close(state: dict[str, Any], frames: dict[str, pd.DataFrame], as_of: str, cash_symbol: str = "CASH") -> dict[str, Any]:
    """Mark existing paper holdings from their saved price to the selected close."""
    as_of_ts = pd.Timestamp(as_of).normalize()
    if state.get("last_mark_date") is None:
        state["last_close"] = {
            symbol: float(frame.loc[frame["date"] <= as_of_ts, "close"].iloc[-1])
            for symbol, frame in frames.items()
            if not frame.loc[frame["date"] <= as_of_ts].empty
        }
        state["last_mark_date"] = str(as_of_ts.date())
        return state

    factor = 0.0
    new_close = dict(state.get("last_close", {}))
    for symbol, weight in state.get("weights", {}).items():
        if symbol == cash_symbol:
            factor += float(weight)
            continue
        sub = frames[symbol].loc[frames[symbol]["date"] <= as_of_ts]
        if sub.empty or symbol not in state.get("last_close", {}):
            raise RuntimeError(f"纸面标记缺少 {symbol} 的价格")
        current = float(sub["close"].iloc[-1])
        factor += float(weight) * current / float(state["last_close"][symbol])
        new_close[symbol] = current
    state["nav"] = float(state["nav"]) * factor
    state["last_close"] = new_close
    state["last_mark_date"] = str(as_of_ts.date())
    return state


def execute_pending_at_t_plus_one_open(
    state: dict[str, Any],
    pending: dict[str, Any],
    frames: dict[str, pd.DataFrame],
    execution_date: str,
    one_way_cost: float,
    cash_symbol: str = "CASH",
) -> tuple[dict[str, Any], pd.DataFrame]:
    """Executes a queued paper target at T+1 open, then marks it to the T+1 close."""
    signal_date = pd.Timestamp(pending["signal_date"]).normalize()
    execution_ts = pd.Timestamp(execution_date).normalize()
    if execution_ts <= signal_date:
        raise ValueError("纸面执行日必须晚于信号日，禁止同日成交。")
    target = {key: float(value) for key, value in pending["target_weights"].items()}
    current = {key: float(value) for key, value in state.get("weights", {}).items()}

    # Mark legacy holdings from saved close to T+1 open.
    if state.get("last_mark_date") is None:
        previous_dates = []
        for frame in frames.values():
            previous_dates.extend(pd.to_datetime(frame.loc[frame["date"] < execution_ts, "date"]).tolist())
        if not previous_dates:
            raise RuntimeError("没有可用于 T+1 开盘前标记的历史收盘价")
        previous_close_date = max(previous_dates)
        state = mark_to_close(state, frames, str(pd.Timestamp(previous_close_date).date()), cash_symbol)

    pretrade_factor = 0.0
    open_prices: dict[str, float] = {}
    close_prices: dict[str, float] = {}
    for symbol, frame in frames.items():
        row = frame.loc[pd.to_datetime(frame["date"]).dt.normalize() == execution_ts]
        if row.empty:
            raise RuntimeError(f"{symbol} 缺少执行日 {execution_ts.date()} 的 OHLC 数据")
        open_prices[symbol] = float(row.iloc[-1]["open"])
        close_prices[symbol] = float(row.iloc[-1]["close"])
    for symbol, weight in current.items():
        if symbol == cash_symbol:
            pretrade_factor += weight
        else:
            prior_close = float(state["last_close"].get(symbol, open_prices[symbol]))
            pretrade_factor += weight * open_prices[symbol] / prior_close
    state["nav"] = float(state["nav"]) * pretrade_factor

    plan = build_trade_plan(current, target, str(signal_date.date()), one_way_cost)
    turnover = weights_turnover(current, target)
    state["nav"] = float(state["nav"]) * (1.0 - turnover * one_way_cost)
    state["weights"] = target

    intraday_factor = 0.0
    for symbol, weight in target.items():
        intraday_factor += weight if symbol == cash_symbol else weight * close_prices[symbol] / open_prices[symbol]
    state["nav"] = float(state["nav"]) * intraday_factor
    state["last_close"] = close_prices
    state["last_mark_date"] = str(execution_ts.date())
    return state, plan


def clear_pending_order() -> None:
    if PENDING_PATH.exists():
        PENDING_PATH.unlink()
