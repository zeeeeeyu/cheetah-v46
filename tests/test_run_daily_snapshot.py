"""run_daily 运行快照接入 测试。

验证：
- open_run_snapshot 在 artifacts 下创建不可覆盖的运行目录
- _write_run_snapshot 落盘 manifest + 载荷，且记录指标
- _write_run_snapshot 为 best-effort：快照失败绝不拖垮主流程
"""

from __future__ import annotations

import json

import app.run_daily as run_daily
from app.common import open_run_snapshot
from quant.observability import RunContext


def test_open_run_snapshot_creates_artifacts_dir(tmp_path, monkeypatch):
    # 把 artifacts 根重定向到临时目录，避免污染仓库
    monkeypatch.setattr(
        "app.common.project_path",
        lambda *parts: tmp_path.joinpath(*parts),
    )
    snap = open_run_snapshot("daily", environment="PAPER")
    assert snap.run_dir.exists()
    assert snap.run_dir.parent.parent.name == "artifacts"
    assert snap.manifest.environment == "PAPER"


def test_write_run_snapshot_records_payload(tmp_path, monkeypatch):
    captured = {}

    def fake_open(run_type, *, environment="PAPER"):
        ctx = RunContext.create(run_type, environment=environment, artifacts_root=tmp_path)
        captured["ctx"] = ctx
        return ctx

    monkeypatch.setattr(run_daily, "open_run_snapshot", fake_open)

    payload = {"environment": "PAPER", "errors": [], "live_orders_created": False,
               "core_fund": {"x": 1}}
    run_daily._write_run_snapshot(payload, "2026-08-01")

    ctx = captured["ctx"]
    data = json.loads(ctx.manifest_path.read_text(encoding="utf-8"))
    assert data["status"] == "COMPLETED"
    assert data["metrics"]["has_errors"] is False
    assert data["metrics"]["live_orders_created"] is False
    assert (ctx.run_dir / "daily_paper.json").is_file()


def test_write_run_snapshot_is_best_effort(monkeypatch, capsys):
    # 快照打开即抛错 → _write_run_snapshot 必须吞掉，不向上传播
    def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(run_daily, "open_run_snapshot", boom)
    # 不应抛异常
    run_daily._write_run_snapshot({"errors": []}, "2026-08-01")
    out = capsys.readouterr().out
    assert "运行快照写入失败" in out
