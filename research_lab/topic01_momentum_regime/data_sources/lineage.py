"""数据集血缘清单（LineageManifest）。

用途：
- 记录每个冻结数据集的来源、复权、上市日、首尾日期、行数、sha256、schema 版本与
  校验状态。LocalSnapshotProvider 在加载前据此判定资产是否可用于正式研究。
- compute_dataset_version：对（排序后的 file hashes + schema_version + 元数据）
  做确定性哈希，相同输入必定得到相同 id（可复现）。
- verify_sha256：用真实文件内容校验记录中的 sha256，防止数据集被静默篡改。

边界：
- source_endpoint_id / request_parameters 本轮允许为 "NOT_FETCHED"，不伪造真实端点。
- 本文件不涉及任何网络访问。
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from typing import Optional

from .schemas import LineageRecord, SCHEMA_VERSION


class LineageManifest:
    """血缘清单：加载 / 保存 / 增改 / 校验。"""

    def __init__(self, records: Optional[list[LineageRecord]] = None):
        self._records: dict[str, LineageRecord] = {}
        for r in (records or []):
            self.add_record(r)

    # ── 持久化 ──
    @classmethod
    def load(cls, path: str) -> "LineageManifest":
        if not os.path.exists(path):
            raise FileNotFoundError(f"血缘清单不存在: {path}")
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        recs = [LineageRecord(**r) for r in data.get("records", [])]
        return cls(recs)

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "records": [asdict(r) for r in self._records.values()],
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)

    # ── 增改查 ──
    def add_record(self, rec: LineageRecord) -> None:
        self._records[rec.asset_id] = rec

    def get_record(self, asset_id: str) -> Optional[LineageRecord]:
        return self._records.get(asset_id)

    def all_records(self) -> list[LineageRecord]:
        return list(self._records.values())

    # ── 数据集版本（确定性）──
    @staticmethod
    def compute_dataset_version(records: list[LineageRecord]) -> str:
        """对排序后的 (asset_id, sha256, schema_version, validation_status) 做哈希。
        相同输入 -> 相同输出（可复现、可审计）。
        """
        items = sorted(
            (
                (r.asset_id, r.sha256, r.schema_version, r.validation_status)
                for r in records
            ),
            key=lambda x: x[0],
        )
        blob = json.dumps(items, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()[:16]

    def dataset_version(self) -> str:
        return self.compute_dataset_version(self.all_records())

    # ── sha256 校验 ──
    def verify_sha256(self, rec: LineageRecord, content: Optional[bytes] = None) -> bool:
        """校验记录 sha256。

        - 若提供 content（文件字节），直接计算并比对。
        - 否则尝试按 rec.dataset_id 推断本地路径读取（可选，依赖 _data_root）。
        """
        if content is None:
            return False
        return hashlib.sha256(content).hexdigest() == rec.sha256
