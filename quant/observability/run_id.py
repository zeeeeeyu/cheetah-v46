"""V4.6 运行标识（run_id）—— 每一次运行的唯一、可追溯身份。

设计原则
========
- **唯一性**：同一秒内多次运行也不会碰撞（时间戳 + 单调计数 + 随机后缀）。
- **可读性**：run_id 自带交易日与运行类型，人肉即可辨认，例如
  ``20260801-031500-daily-a1b2c3``。
- **Windows 安全**：只使用 ``[0-9A-Za-z-]``，绝不含 ``:`` 等非法文件名字符，
  可直接作为目录名落到 ``artifacts/YYYY-MM-DD/<run_id>/``。
- **不可变**：RunId 是 frozen 值对象，一旦生成不可篡改。

run_id 是"运行快照/可观测性"与"三本账"之间的黏合剂：真实持仓
(``ActualPosition``) 必须携带 ``reconciliation_run_id`` 才能证明其来源，
而那个 run_id 就由本模块生成。
"""

from __future__ import annotations

import itertools
import re
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime

__all__ = ["RunId", "new_run_id", "RUN_ID_PATTERN"]

# run_id 的合法形态：YYYYMMDD-HHMMSS-<type>-<suffix>
RUN_ID_PATTERN = re.compile(r"^\d{8}-\d{6}-[a-z0-9_]+-[0-9a-f]{6,}$")

# 进程内单调计数器 + 锁，保证同一进程内绝不碰撞。
_counter = itertools.count()
_lock = threading.Lock()

# 运行类型只允许小写字母、数字与下划线，避免污染目录名。
_RUN_TYPE_RE = re.compile(r"[^a-z0-9_]+")


def _normalize_run_type(run_type: str) -> str:
    cleaned = _RUN_TYPE_RE.sub("_", run_type.strip().lower()).strip("_")
    return cleaned or "run"


@dataclass(frozen=True)
class RunId:
    """一次运行的唯一身份（不可变值对象）。"""

    value: str
    """完整 run_id 字符串，例如 ``20260801-031500-daily-a1b2c3``。"""

    trading_date: str
    """该次运行归属的交易日（``YYYY-MM-DD``），用于 artifacts 日期分区。"""

    run_type: str
    """规范化后的运行类型（如 daily / paper / reconcile / backtest）。"""

    created_at: str
    """创建时间（ISO 8601，本地时区），用于审计。"""

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value

    def to_dict(self) -> dict[str, str]:
        return {
            "value": self.value,
            "trading_date": self.trading_date,
            "run_type": self.run_type,
            "created_at": self.created_at,
        }


def new_run_id(run_type: str = "run", *, now: datetime | None = None) -> RunId:
    """生成一个新的、保证唯一的 RunId。

    参数
    ----
    run_type:
        运行类型标签（daily/paper/reconcile/backtest/...），会被规范化为
        安全的目录名片段。
    now:
        注入的时间（便于测试确定性）。默认取当前本地时间。

    唯一性保证：时间戳精确到秒，再叠加进程内单调计数与 3 字节随机数，
    因此即便在同一秒内高频调用也不会产生相同 run_id。
    """
    moment = now or datetime.now()
    normalized = _normalize_run_type(run_type)

    with _lock:
        seq = next(_counter)
    # 3 字节随机 = 6 位十六进制；再拼上单调计数避免同一进程内碰撞。
    suffix = f"{secrets.token_hex(3)}{seq:x}"

    value = (
        f"{moment:%Y%m%d}-{moment:%H%M%S}-{normalized}-{suffix}"
    )
    return RunId(
        value=value,
        trading_date=moment.strftime("%Y-%m-%d"),
        run_type=normalized,
        created_at=moment.isoformat(timespec="seconds"),
    )
