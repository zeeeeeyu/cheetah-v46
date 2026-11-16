"""V4.6 运行快照（Run Snapshot）—— 可观测性与可追溯性的物理落点。

为什么需要它
============
V4.5.1 的输出是"覆盖式"的：每次运行覆盖上一次的结果，历史无法复盘，
出问题也无法定位到"是哪一次运行、用了哪些输入、产出了什么"。V4.6 要求
每一次运行都在 ``artifacts/YYYY-MM-DD/<run_id>/`` 下留下**不可覆盖**的快照：

    artifacts/
      2026-08-01/
        20260801-031500-daily-a1b2c3/
          manifest.json      # 运行清单（输入指纹、输出哈希、状态、指标）
          <各类产出物>.json / .csv ...

设计要点
--------
- **绝不覆盖历史**：run_id 唯一 → 目录唯一；若目录已存在则直接报错。
- **输入可追溯**：register_input 记录每个输入的身份与指纹（哈希/as_of）。
- **输出可校验**：write_json / register_output 落盘后记录 sha256 与字节数。
- **失败也留痕**：即便运行中途抛异常，manifest 也会以 status=FAILED 收尾。
- **无外部依赖**：仅用标准库，保证在任何环境下都能写快照。
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .run_id import RunId, new_run_id

__all__ = [
    "RunStatus",
    "ArtifactRecord",
    "InputRecord",
    "RunManifest",
    "RunContext",
    "SnapshotError",
]


class SnapshotError(RuntimeError):
    """运行快照相关的硬错误（如目录已存在、越权覆盖）。"""


class RunStatus:
    """运行状态常量（字符串，便于序列化）。"""

    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class InputRecord:
    """一条输入的身份与指纹（用于血缘追踪）。"""

    name: str
    kind: str  # dataset / config / forecast / parameter ...
    identity: str  # 逻辑身份，例如 "asset_master.json" 或 symbol
    fingerprint: str  # 内容哈希或版本号
    as_of: str | None = None  # 数据截止时间

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "identity": self.identity,
            "fingerprint": self.fingerprint,
            "as_of": self.as_of,
        }


@dataclass(frozen=True)
class ArtifactRecord:
    """一条产出物的落盘记录（含哈希，便于事后校验）。"""

    name: str
    relative_path: str
    sha256: str
    size_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "relative_path": self.relative_path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


@dataclass
class RunManifest:
    """运行清单 —— 一次运行的完整可追溯档案。"""

    run_id: RunId
    environment: str
    started_at: str
    status: str = RunStatus.RUNNING
    finished_at: str | None = None
    code_version: str | None = None
    inputs: list[InputRecord] = field(default_factory=list)
    outputs: list[ArtifactRecord] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id.to_dict(),
            "environment": self.environment,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "code_version": self.code_version,
            "inputs": [item.to_dict() for item in self.inputs],
            "outputs": [item.to_dict() for item in self.outputs],
            "metrics": dict(self.metrics),
            "notes": list(self.notes),
            "error": self.error,
        }


class RunContext:
    """运行上下文（上下文管理器）—— 一次运行期间的快照写入器。

    典型用法::

        with RunContext.create("daily", environment="PAPER") as run:
            run.register_input("assets", "config", "asset_master.json", fp, as_of)
            run.write_json("targets", target_payload)
            run.record_metric("n_orders", 3)
        # 退出时自动写入最终 manifest（COMPLETED 或 FAILED）

    绝不覆盖历史：run_id 唯一 → 目录唯一；若目录已存在直接抛错。
    """

    MANIFEST_NAME = "manifest.json"

    def __init__(self, manifest: RunManifest, run_dir: Path) -> None:
        self.manifest = manifest
        self.run_dir = run_dir

    # ── 构造 ────────────────────────────────────────────────────────
    @classmethod
    def create(
        cls,
        run_type: str,
        *,
        environment: str,
        artifacts_root: Path | str = "artifacts",
        code_version: str | None = None,
        run_id: RunId | None = None,
        now: datetime | None = None,
    ) -> RunContext:
        """创建一次运行的快照目录并返回上下文。

        目录形如 ``<artifacts_root>/<YYYY-MM-DD>/<run_id>/``；若目录已存在
        则抛 ``SnapshotError``，从物理上杜绝"覆盖历史"。
        """
        rid = run_id or new_run_id(run_type, now=now)
        root = Path(artifacts_root)
        run_dir = root / rid.trading_date / rid.value
        if run_dir.exists():
            raise SnapshotError(
                f"运行目录已存在，拒绝覆盖历史：{run_dir}"
            )
        run_dir.mkdir(parents=True, exist_ok=False)

        manifest = RunManifest(
            run_id=rid,
            environment=environment,
            started_at=(now or datetime.now()).isoformat(timespec="seconds"),
            code_version=code_version,
        )
        ctx = cls(manifest=manifest, run_dir=run_dir)
        ctx._flush_manifest()  # 先落一份 RUNNING 状态，崩溃也有痕迹
        return ctx

    # ── 输入/输出/指标登记 ───────────────────────────────────────────
    def register_input(
        self,
        name: str,
        kind: str,
        identity: str,
        fingerprint: str,
        as_of: str | None = None,
    ) -> InputRecord:
        record = InputRecord(name, kind, identity, fingerprint, as_of)
        self.manifest.inputs.append(record)
        return record

    def write_json(self, name: str, payload: Any, *, filename: str | None = None) -> ArtifactRecord:
        """把一个可 JSON 序列化的产出物写入 run 目录并登记哈希。"""
        fname = filename or f"{name}.json"
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        target = self.run_dir / fname
        target.write_bytes(data)
        record = ArtifactRecord(
            name=name,
            relative_path=fname,
            sha256=_sha256_bytes(data),
            size_bytes=len(data),
        )
        self.manifest.outputs.append(record)
        self._flush_manifest()
        return record

    def register_output(self, name: str, source: Path | str, *, filename: str | None = None) -> ArtifactRecord:
        """把一个已存在的外部文件复制进 run 目录并登记哈希。"""
        src = Path(source)
        if not src.is_file():
            raise SnapshotError(f"待登记的产出物不存在：{src}")
        fname = filename or src.name
        target = self.run_dir / fname
        shutil.copy2(src, target)
        record = ArtifactRecord(
            name=name,
            relative_path=fname,
            sha256=_sha256_file(target),
            size_bytes=target.stat().st_size,
        )
        self.manifest.outputs.append(record)
        self._flush_manifest()
        return record

    def record_metric(self, key: str, value: Any) -> None:
        self.manifest.metrics[key] = value

    def add_note(self, note: str) -> None:
        self.manifest.notes.append(note)

    # ── manifest 落盘 ────────────────────────────────────────────────
    @property
    def manifest_path(self) -> Path:
        return self.run_dir / self.MANIFEST_NAME

    def _flush_manifest(self) -> None:
        data = json.dumps(self.manifest.to_dict(), ensure_ascii=False, indent=2)
        self.manifest_path.write_text(data, encoding="utf-8")

    def finalize(self, status: str, *, error: str | None = None, now: datetime | None = None) -> None:
        self.manifest.status = status
        self.manifest.error = error
        self.manifest.finished_at = (now or datetime.now()).isoformat(timespec="seconds")
        self._flush_manifest()

    # ── 上下文管理器协议 ─────────────────────────────────────────────
    def __enter__(self) -> RunContext:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            self.finalize(RunStatus.COMPLETED)
        else:
            self.finalize(RunStatus.FAILED, error=f"{exc_type.__name__}: {exc}")
        return False  # 不吞异常
