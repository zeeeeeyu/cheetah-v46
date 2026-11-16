"""V4.6 CLI common helpers."""
from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from quant.config import project_path, load_json
from quant.observability import RunContext

def runtime_config() -> dict[str, Any]:
    path = project_path("config", "runtime.json")
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"account_id": "MAIN", "default_environment": "PAPER", "allow_live_order_generation": False}

def account_id() -> str:
    return str(runtime_config().get("account_id", "MAIN"))

def save_json_report(category: str, name: str, payload: Any) -> Path:
    out = project_path("output", category)
    out.mkdir(parents=True, exist_ok=True)
    path = out / name
    if hasattr(payload, "to_dict"):
        payload = payload.to_dict()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path

def run_id(prefix: str) -> str:
    return f"{prefix}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"


def open_run_snapshot(run_type: str, *, environment: str = "PAPER") -> RunContext:
    """为一次调度运行打开不可覆盖的运行快照上下文。

    快照落在 ``artifacts/YYYY-MM-DD/<run_id>/``（与旧 ``output/`` 覆盖式产物
    并存、互不影响）。这是 V4.6「运行快照/可观测性」在调度入口的实际落点。
    """
    return RunContext.create(
        run_type,
        environment=environment,
        artifacts_root=project_path("artifacts"),
    )


def _to_jsonable(payload: Any) -> Any:
    """把任意决策对象转为可 JSON 序列化结构（复用 to_dict，兜底 default=str）。"""
    if hasattr(payload, "to_dict"):
        payload = payload.to_dict()
    return json.loads(json.dumps(payload, ensure_ascii=False, default=str))


def snapshot_run(
    run_type: str,
    payload: Any,
    *,
    environment: str = "PAPER",
    as_of: str | None = None,
    metrics: dict[str, Any] | None = None,
    report_path: Path | str | None = None,
) -> None:
    """尽力为一次运行写一份不可覆盖的运行快照；失败绝不拖垮主流程（best-effort）。

    - 把 ``payload`` 落为 ``<run_type>.json`` 并登记 sha256；
    - 若提供 ``report_path``（旧 ``output/`` 覆盖式产物），一并复制入库登记，
      使「不可覆盖快照」与「覆盖式产物」建立血缘对应；
    - 记录 ``as_of`` 输入指纹与自定义指标。
    """
    try:
        data = _to_jsonable(payload)
        with open_run_snapshot(run_type, environment=environment) as snap:
            if as_of is not None:
                snap.register_input("as_of", "parameter", "as_of", str(as_of), as_of=str(as_of))
            snap.write_json(run_type, data)
            if report_path is not None and Path(report_path).is_file():
                snap.register_output("legacy_report", report_path)
            for key, value in (metrics or {}).items():
                snap.record_metric(key, value)
    except Exception as exc:  # noqa: BLE001 - 可观测性绝不能拖垮主流程
        print(f"[warn] 运行快照写入失败（不影响主流程）: {exc}")
