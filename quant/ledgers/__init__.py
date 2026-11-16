"""V4.6 三本账物理隔离层。

系统必须严格区分三类事实，且在物理上互相隔离（各自独立的 SQLite 数据库文件）：

  1. 模型目标账 model_targets   —— "模型/策略想要什么"（TargetPortfolio / ForecastBundle）
  2. 决策计划账 decision_plans   —— "系统决定做什么"（DecisionProposal / OrderIntent）
  3. 真实持仓账 actual_positions —— "账户实际持有什么"（ActualPosition，仅来自对账）

物理隔离的意义：
- 三本账落在三个独立 db 文件，任何一本账的写入 API 都不可能触及另一本账的表。
- 真实持仓账只接受来源为 RECONCILIATION 的写入（写权限守卫 + 领域层护栏双重保险）。
- 目标、决策、订单意图（含 SUBMITTED）都无法直接改变真实持仓。

这是"账户事实优先""决策/成交/持仓分离"两条铁律的工程落点。
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

# ── 三本账各自独立的物理文件 ──────────────────────────────────────

DEFAULT_LEDGER_DIR = Path(__file__).resolve().parents[2] / "storage" / "ledgers"

MODEL_TARGETS_DB = DEFAULT_LEDGER_DIR / "model_targets.db"
DECISION_PLANS_DB = DEFAULT_LEDGER_DIR / "decision_plans.db"
ACTUAL_POSITIONS_DB = DEFAULT_LEDGER_DIR / "actual_positions.db"

# 账本逻辑名 -> 默认物理路径。物理隔离由不同文件保证。
LEDGER_PATHS: dict[str, Path] = {
    "model_targets": MODEL_TARGETS_DB,
    "decision_plans": DECISION_PLANS_DB,
    "actual_positions": ACTUAL_POSITIONS_DB,
}

# ── 线程本地连接（按物理文件区分） ────────────────────────────────

_local = threading.local()


def get_connection(db_path: Path | str) -> sqlite3.Connection:
    """获取指定物理文件的 SQLite 连接（线程本地缓存）。"""
    key = str(db_path)
    conns: dict[str, sqlite3.Connection] = getattr(_local, "conns", None)
    if conns is None:
        conns = {}
        _local.conns = conns
    if key not in conns:
        path = Path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), detect_types=sqlite3.PARSE_DECLTYPES)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        conns[key] = conn
    return conns[key]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


__all__ = [
    "DEFAULT_LEDGER_DIR",
    "MODEL_TARGETS_DB",
    "DECISION_PLANS_DB",
    "ACTUAL_POSITIONS_DB",
    "LEDGER_PATHS",
    "get_connection",
    "now_iso",
]
