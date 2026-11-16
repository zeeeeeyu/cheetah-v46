from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .config import project_path


def load_state() -> dict:
    path = project_path("state", "portfolio_state.json")
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def build_trade_plan(current: dict[str, float], target: dict[str, float], as_of_date: str, estimated_one_way_cost: float) -> pd.DataFrame:
    rows = []
    for symbol in sorted(set(current) | set(target)):
        current_weight = float(current.get(symbol, 0.0))
        target_weight = float(target.get(symbol, 0.0))
        delta = target_weight - current_weight
        action = "HOLD" if abs(delta) < 1e-10 else ("BUY" if delta > 0 else "SELL")
        rows.append(
            {
                "as_of_date": as_of_date,
                "symbol": symbol,
                "current_weight": current_weight,
                "target_weight": target_weight,
                "delta_weight": delta,
                "action": action,
                "estimated_one_way_cost_weight": abs(delta) * estimated_one_way_cost,
                "manual_approval_required": True,
                "broker_order_sent": False,
            }
        )
    return pd.DataFrame(rows)


def write_trade_plan(plan: pd.DataFrame, as_of_date: str) -> Path:
    output_dir = project_path("output", "trade_plans")
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"trade_plan_{as_of_date}.csv"
    plan.to_csv(path, index=False, encoding="utf-8-sig")
    return path
