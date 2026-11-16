"""运行快照 / 可观测性 测试。

覆盖：
- run_id 唯一性与 Windows 安全字符
- artifacts/YYYY-MM-DD/<run_id>/ 目录结构
- manifest 记录输入指纹 / 输出哈希 / 指标
- 绝不覆盖历史（目录已存在即报错）
- 失败运行也以 FAILED 收尾
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from quant.observability import (
    RUN_ID_PATTERN,
    RunContext,
    RunStatus,
    SnapshotError,
    new_run_id,
)


class TestRunId:
    def test_format_is_windows_safe_and_matches_pattern(self):
        rid = new_run_id("daily", now=datetime(2026, 8, 1, 3, 15, 0))
        assert rid.value.startswith("20260801-031500-daily-")
        assert ":" not in rid.value  # Windows 文件名禁止冒号
        assert RUN_ID_PATTERN.match(rid.value)
        assert rid.trading_date == "2026-08-01"
        assert rid.run_type == "daily"

    def test_run_type_normalized(self):
        rid = new_run_id("Daily Reconcile!!", now=datetime(2026, 8, 1, 3, 15, 0))
        assert rid.run_type == "daily_reconcile"
        assert ":" not in rid.value and " " not in rid.value

    def test_uniqueness_same_second(self):
        moment = datetime(2026, 8, 1, 3, 15, 0)
        ids = {new_run_id("daily", now=moment).value for _ in range(500)}
        assert len(ids) == 500  # 同一秒内 500 次也不碰撞


class TestRunSnapshotDir:
    def test_creates_dated_run_directory(self, tmp_path):
        ctx = RunContext.create(
            "daily",
            environment="PAPER",
            artifacts_root=tmp_path,
            now=datetime(2026, 8, 1, 3, 15, 0),
        )
        assert ctx.run_dir.parent.name == "2026-08-01"
        assert ctx.run_dir.name == ctx.manifest.run_id.value
        assert ctx.manifest_path.is_file()

    def test_initial_manifest_is_running(self, tmp_path):
        ctx = RunContext.create("daily", environment="PAPER", artifacts_root=tmp_path)
        data = json.loads(ctx.manifest_path.read_text(encoding="utf-8"))
        assert data["status"] == RunStatus.RUNNING
        assert data["environment"] == "PAPER"

    def test_never_overwrites_existing_run(self, tmp_path):
        moment = datetime(2026, 8, 1, 3, 15, 0)
        rid = new_run_id("daily", now=moment)
        RunContext.create(
            "daily", environment="PAPER", artifacts_root=tmp_path, run_id=rid
        )
        with pytest.raises(SnapshotError):
            RunContext.create(
                "daily", environment="PAPER", artifacts_root=tmp_path, run_id=rid
            )


class TestManifestContents:
    def test_records_inputs_outputs_and_metrics(self, tmp_path):
        with RunContext.create(
            "daily", environment="PAPER", artifacts_root=tmp_path
        ) as ctx:
            ctx.register_input(
                "assets", "config", "asset_master.json", "abc123", as_of="2026-07-31"
            )
            rec = ctx.write_json("targets", {"510300": 0.4, "cash": 0.6})
            ctx.record_metric("n_targets", 2)
            ctx.add_note("smoke run")

        data = json.loads(ctx.manifest_path.read_text(encoding="utf-8"))
        assert data["status"] == RunStatus.COMPLETED
        assert data["finished_at"] is not None
        assert data["inputs"][0]["identity"] == "asset_master.json"
        assert data["inputs"][0]["fingerprint"] == "abc123"
        assert data["metrics"]["n_targets"] == 2
        assert data["notes"] == ["smoke run"]
        # 输出文件真实落盘且哈希非空
        out_file = ctx.run_dir / "targets.json"
        assert out_file.is_file()
        assert len(rec.sha256) == 64
        assert rec.size_bytes > 0

    def test_write_json_is_readable_back(self, tmp_path):
        with RunContext.create(
            "paper", environment="PAPER", artifacts_root=tmp_path
        ) as ctx:
            ctx.write_json("plan", {"orders": [{"symbol": "510300", "side": "BUY"}]})
        payload = json.loads((ctx.run_dir / "plan.json").read_text(encoding="utf-8"))
        assert payload["orders"][0]["symbol"] == "510300"

    def test_register_output_copies_external_file(self, tmp_path):
        external = tmp_path / "external.csv"
        external.write_text("a,b\n1,2\n", encoding="utf-8")
        with RunContext.create(
            "backtest", environment="BACKTEST", artifacts_root=tmp_path
        ) as ctx:
            rec = ctx.register_output("bt", external)
        assert (ctx.run_dir / "external.csv").is_file()
        assert rec.size_bytes > 0


class TestFailureHandling:
    def test_failed_run_is_marked_failed(self, tmp_path):
        ctx_ref = {}
        with pytest.raises(ValueError):
            with RunContext.create(
                "daily", environment="PAPER", artifacts_root=tmp_path
            ) as ctx:
                ctx_ref["ctx"] = ctx
                raise ValueError("boom")
        ctx = ctx_ref["ctx"]
        data = json.loads(ctx.manifest_path.read_text(encoding="utf-8"))
        assert data["status"] == RunStatus.FAILED
        assert "boom" in data["error"]
        assert data["finished_at"] is not None
