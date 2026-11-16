from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import project_path


def write_decision_report(payload: dict[str, Any], as_of_date: str) -> Path:
    output_dir = project_path("output", "decisions")
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"decision_{as_of_date}.json"
    md_path = output_dir / f"decision_{as_of_date}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"# V4.1–V4.5 决策报告｜{as_of_date}",
        "",
        f"- 决策状态：**{payload['status']}**",
        f"- 市场状态：**{payload['regime']['regime']}**",
        f"- 信号置信度：**{payload['confidence']}**",
        f"- 预计换手率：**{payload['estimated_turnover']:.2%}**",
        f"- 预计单次成本（权重口径）：**{payload['estimated_cost']:.4%}**",
        "",
        "## 决策原因",
    ]
    lines.extend([f"- {item}" for item in payload["reasons"]])
    lines.extend(["", "## Champion 目标权重"])
    lines.extend([f"- {key}: {value:.2%}" for key, value in payload["champion_weights"].items()])
    lines.extend(["", "## Challenger（影子 HRP）权重"])
    lines.extend([f"- {key}: {value:.2%}" for key, value in payload["challenger_weights"].items()])
    lines.extend(["", "## 信号明细"])
    for item in payload["signals"]:
        score = item.get("score")
        score_text = "N/A" if score is None else f"{score:.4f}"
        lines.append(f"- {item['symbol']} {item['name']}｜eligible={item['eligible']}｜score={score_text}｜原因：{'；'.join(item['reasons']) or '通过'}")
    lines.extend(["", "## 安全边界", "- 本系统只生成计划，不自动向券商下单。", "- HMM、HRP 均是影子或挑战者模块，不能自动替换 Champion。"])
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path
