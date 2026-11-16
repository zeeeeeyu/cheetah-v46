"""app.common.snapshot_run 通用运行快照助手 测试。

验证：
- snapshot_run 落盘 payload + manifest(COMPLETED) + 记录指标
- 提供 report_path 时把旧 output/ 覆盖式产物复制入库并登记血缘
- 支持带 .to_dict() 的决策对象
- best-effort：内部任何异常都被吞掉，绝不拖垮主流程
"""

from __future__ import annotations

import json

import app.common as common


def _redirect_artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.common.project_path",
        lambda *parts: tmp_path.joinpath(*parts),
    )


def _latest_manifest(tmp_path):
    manifests = list((tmp_path / "artifacts").rglob("manifest.json"))
    assert len(manifests) == 1, f"应恰好一个 manifest，实际 {len(manifests)}"
    return json.loads(manifests[0].read_text(encoding="utf-8")), manifests[0].parent


def test_snapshot_run_writes_payload_and_metrics(tmp_path, monkeypatch):
    _redirect_artifacts(tmp_path, monkeypatch)
    common.snapshot_run(
        "tactical_etf",
        {"target_weights": {"510300": 0.5}},
        environment="PAPER",
        as_of="2026-08-01",
        metrics={"regime": "RISK_ON"},
    )
    data, run_dir = _latest_manifest(tmp_path)
    assert data["status"] == "COMPLETED"
    assert data["environment"] == "PAPER"
    assert data["metrics"]["regime"] == "RISK_ON"
    assert data["inputs"][0]["identity"] == "as_of"
    assert (run_dir / "tactical_etf.json").is_file()


def test_snapshot_run_registers_legacy_report(tmp_path, monkeypatch):
    _redirect_artifacts(tmp_path, monkeypatch)
    legacy = tmp_path / "legacy_report.json"
    legacy.write_text('{"ok": true}', encoding="utf-8")
    common.snapshot_run("backtest", {"n": 1}, environment="RESEARCH", report_path=legacy)
    data, run_dir = _latest_manifest(tmp_path)
    names = {o["name"] for o in data["outputs"]}
    assert "legacy_report" in names
    assert (run_dir / "legacy_report.json").is_file()


def test_snapshot_run_supports_to_dict_objects(tmp_path, monkeypatch):
    _redirect_artifacts(tmp_path, monkeypatch)

    class Decision:
        def to_dict(self):
            return {"status": "PLAN_ONLY", "weights": {"a": 1}}

    common.snapshot_run("core_fund", Decision(), environment="PAPER")
    _, run_dir = _latest_manifest(tmp_path)
    payload = json.loads((run_dir / "core_fund.json").read_text(encoding="utf-8"))
    assert payload["status"] == "PLAN_ONLY"


def test_snapshot_run_is_best_effort(monkeypatch, capsys):
    # 让 open_run_snapshot 直接爆炸 → snapshot_run 必须吞掉且不抛
    def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(common, "open_run_snapshot", boom)
    common.snapshot_run("daily", {"x": 1})  # 不应抛异常
    assert "运行快照写入失败" in capsys.readouterr().out
