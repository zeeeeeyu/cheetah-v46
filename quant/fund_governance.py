from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import project_path


def append_fund_decision_log(payload: dict[str, Any]) -> Path:
    path = project_path("output", "fund_decisions", "fund_decision_log.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return path


def fund_governance_status(settings: dict[str, Any]) -> dict[str, Any]:
    path = project_path("output", "fund_decisions", "fund_decision_log.jsonl")
    count = 0
    if path.exists():
        with path.open("r", encoding="utf-8") as handle:
            count = sum(1 for line in handle if line.strip())
    required = int(settings["governance"]["min_out_of_sample_decisions_before_review"])
    return {
        "champion": settings["governance"]["champion_name"],
        "challenger": settings["governance"]["challenger_name"],
        "logged_decisions": count,
        "minimum_before_review": required,
        "promotion_allowed": False,
        "reason": "场外基金分支禁止自动晋级。净值滞后、申赎状态、费率和确认时滞均需经过人工审计。",
    }
