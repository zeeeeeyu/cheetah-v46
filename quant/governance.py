from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import project_path


def append_decision_log(payload: dict[str, Any]) -> Path:
    path = project_path("output", "decisions", "decision_log.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return path


def governance_status(settings: dict) -> dict[str, Any]:
    path = project_path("output", "decisions", "decision_log.jsonl")
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
        "reason": "V4.5 禁止自动晋级。必须完成样本外与纸面交易人工审查后才可讨论替换 Champion。",
    }
