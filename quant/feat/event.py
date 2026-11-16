"""V4.6 事件领域模型和仓库。

事件至少包含：
- event_id / event_time / published_time / tradable_time
- event_type / title / source
- related_themes / related_assets
- direction / confidence / expected_horizon / review_status

事件必须有发布时间（published_time）。
回测只能在 tradable_time 之后使用事件。
人工事件必须标记来源和复核状态。
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quant.config import project_path


# ═══════════════════════════════════════════════════════════════
# 事件领域模型
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Event:
    """不可变事件记录。

    事件必须有 published_time（发布时间）。
    回测只能在 tradable_time 之后使用事件数据。
    """
    event_id: str
    event_time: str                          # 事件实际发生时间
    published_time: str                      # 事件发布时间（必须）
    tradable_time: str | None = None         # 可交易时间（回测使用）
    event_type: str = "GENERAL"              # POLICY / INDUSTRY / MACRO / BLACK_SWAN / EARNINGS / OTHER
    title: str = ""
    source: str = "MANUAL"                   # MANUAL / NEWS / SOCIAL_MEDIA / GOVERNMENT
    related_themes: tuple[str, ...] = field(default_factory=tuple)   # 关联主题
    related_assets: tuple[str, ...] = field(default_factory=tuple)   # 关联资产代码
    direction: str = "NEUTRAL"               # BULLISH / BEARISH / NEUTRAL
    confidence: float = 0.5                  # [0, 1]
    expected_horizon: str = "SHORT"          # SHORT / MEDIUM / LONG
    review_status: str = "UNREVIEWED"        # UNREVIEWED / REVIEWED / REJECTED / CONFIRMED
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_actionable(self, as_of_date: str | None = None) -> bool:
        """事件是否可被用于交易决策。

        需要同时满足：
        - 已复核（REVIEWED 或 CONFIRMED）
        - 置信度 >= 0.3
        - 未过期
        """
        if self.review_status not in ("REVIEWED", "CONFIRMED"):
            return False
        if self.confidence < 0.3:
            return False
        if as_of_date and self.tradable_time:
            if as_of_date < self.tradable_time[:10]:
                return False
        return True

    def is_expired(self, as_of_date: str, max_age_days: int = 30) -> bool:
        from datetime import datetime
        event_dt = datetime.strptime(self.event_time[:10], "%Y-%m-%d") if self.event_time else datetime.now()
        as_of_dt = datetime.strptime(as_of_date, "%Y-%m-%d")
        return (as_of_dt - event_dt).days > max_age_days

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_time": self.event_time,
            "published_time": self.published_time,
            "tradable_time": self.tradable_time,
            "event_type": self.event_type,
            "title": self.title,
            "source": self.source,
            "related_themes": list(self.related_themes),
            "related_assets": list(self.related_assets),
            "direction": self.direction,
            "confidence": self.confidence,
            "expected_horizon": self.expected_horizon,
            "review_status": self.review_status,
        }


# ═══════════════════════════════════════════════════════════════
# 事件仓库
# ═══════════════════════════════════════════════════════════════

class EventRepository:
    """事件存储和检索。"""

    def __init__(self, storage_dir: Path | None = None):
        self.storage_dir = storage_dir or project_path("storage", "events")
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._events: dict[str, Event] = {}

    def save(self, event: Event) -> Path:
        self._events[event.event_id] = event
        path = self.storage_dir / f"{event.event_id}.json"
        path.write_text(json.dumps(event.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def load(self, event_id: str) -> Event | None:
        if event_id in self._events:
            return self._events[event_id]
        path = self.storage_dir / f"{event_id}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        event = self._from_dict(data)
        self._events[event_id] = event
        return event

    def find_by_date(self, as_of_date: str) -> list[Event]:
        results = []
        for path in self.storage_dir.glob("*.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            pub_date = data.get("published_time", "")[:10]
            if pub_date == as_of_date[:10]:
                results.append(self._from_dict(data))
        return sorted(results, key=lambda e: e.published_time, reverse=True)

    def find_by_theme(self, theme: str) -> list[Event]:
        return [e for e in self._events.values() if theme in e.related_themes]

    def find_actionable(self, as_of_date: str) -> list[Event]:
        """查找当前可交易的事件。"""
        return [e for e in self._events.values() if e.is_actionable(as_of_date) and not e.is_expired(as_of_date)]

    def import_csv(self, csv_path: str) -> list[Event]:
        """从 CSV 导入事件。

        CSV 格式：event_id, event_time, published_time, event_type, title, source, themes, assets, direction, confidence, horizon
        """
        imported = []
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                event = Event(
                    event_id=row.get("event_id", f"CSV-{row.get('title', '')[:20]}-{datetime.now().strftime('%H%M%S')}"),
                    event_time=row.get("event_time", row.get("published_time", "")),
                    published_time=row.get("published_time", ""),
                    event_type=row.get("event_type", "GENERAL"),
                    title=row.get("title", ""),
                    source=row.get("source", "CSV_IMPORT"),
                    related_themes=tuple(t.strip() for t in row.get("themes", "").split(";") if t.strip()),
                    related_assets=tuple(a.strip() for a in row.get("assets", "").split(";") if a.strip()),
                    direction=row.get("direction", "NEUTRAL"),
                    confidence=float(row.get("confidence", 0.5)),
                    expected_horizon=row.get("horizon", "SHORT"),
                    review_status=row.get("review_status", "UNREVIEWED"),
                )
                self.save(event)
                imported.append(event)
        return imported

    def all_events(self) -> list[Event]:
        return list(self._events.values())

    @staticmethod
    def _from_dict(data: dict) -> Event:
        return Event(
            event_id=data["event_id"], event_time=data.get("event_time", ""),
            published_time=data.get("published_time", ""),
            tradable_time=data.get("tradable_time"),
            event_type=data.get("event_type", "GENERAL"), title=data.get("title", ""),
            source=data.get("source", "MANUAL"),
            related_themes=tuple(data.get("related_themes", [])),
            related_assets=tuple(data.get("related_assets", [])),
            direction=data.get("direction", "NEUTRAL"),
            confidence=float(data.get("confidence", 0.5)),
            expected_horizon=data.get("expected_horizon", "SHORT"),
            review_status=data.get("review_status", "UNREVIEWED"),
        )
