"""V4.6 可观测性与运行快照。

对外统一入口：run_id 生成 + 不可覆盖的运行快照目录 + manifest 清单。
"""

from __future__ import annotations

from .run_id import RUN_ID_PATTERN, RunId, new_run_id
from .snapshot import (
    ArtifactRecord,
    InputRecord,
    RunContext,
    RunManifest,
    RunStatus,
    SnapshotError,
)

__all__ = [
    "RunId",
    "new_run_id",
    "RUN_ID_PATTERN",
    "RunContext",
    "RunManifest",
    "RunStatus",
    "InputRecord",
    "ArtifactRecord",
    "SnapshotError",
]
