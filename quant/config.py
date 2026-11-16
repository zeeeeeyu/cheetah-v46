"""V4.6 项目配置工具（独立版 — 与 V4.5.1 config.py 兼容）。"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

def project_path(*parts: str) -> Path:
    return ROOT.joinpath(*parts)

def load_json(relative_path: str) -> Any:
    path = project_path(relative_path)
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)

def save_json(relative_path: str, payload: Any) -> Path:
    path = project_path(relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    return path
