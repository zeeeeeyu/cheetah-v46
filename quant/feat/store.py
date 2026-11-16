"""V4.6 FeatureSnapshot 存储接口。"""
from __future__ import annotations
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any
from quant.config import project_path

@dataclass
class FeatureSnapshot:
    """特征快照 — 包含完整的数据可得时间和版本信息。"""
    snapshot_id: str
    as_of_date: str                 # 特征计算日期
    symbol: str                     # 资产代码
    features: dict[str, float | str | None] = field(default_factory=dict)
    event_time: str | None = None   # 数据事件发生时间
    availability_time: str | None = None  # 数据对策略可用的时间
    retrieved_time: str | None = None     # 数据实际获取时间
    data_version: str = ""          # 数据集版本
    feature_version: str = "1.0.0"
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    
    def to_dict(self) -> dict[str, Any]: return asdict(self)
    
    def is_available_for(self, decision_time: str) -> bool:
        """判断特征在决策时间是否可用。"""
        if not self.availability_time:
            return True
        return self.availability_time <= decision_time

class FeatureSnapshotStore:
    def __init__(self, storage_dir: Path | None = None):
        self.storage_dir = storage_dir or project_path("storage", "features")
        self.storage_dir.mkdir(parents=True, exist_ok=True)
    def save(self, snapshot: FeatureSnapshot) -> Path:
        path = self.storage_dir / f"{snapshot.snapshot_id}.json"
        path.write_text(json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"); return path
    def load(self, snapshot_id: str) -> FeatureSnapshot | None:
        path = self.storage_dir / f"{snapshot_id}.json"
        return None if not path.exists() else FeatureSnapshot(**json.loads(path.read_text(encoding="utf-8")))
    def find_by_date(self, as_of_date: str) -> list[FeatureSnapshot]:
        results = []
        for path in self.storage_dir.glob("*.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("as_of_date") == as_of_date: results.append(FeatureSnapshot(**data))
        return sorted(results, key=lambda x: x.symbol)
