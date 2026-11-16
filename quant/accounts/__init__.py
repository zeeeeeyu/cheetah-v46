"""V4.6 账户 Ledger — SQLite 数据库引擎。

核心契约：
- Transaction 为不可变事件，禁止覆盖/修改
- 错误修正通过冲正记录完成
- 所有写入使用事务
- 重复导入幂等（transaction_id 去重）
- 每条记录关联来源 + 审计时间戳
- 资产代码使用 TEXT（保留前导零）
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── 精度常量 ──────────────────────────────────────────────────────

AMOUNT_DECIMAL = "DECIMAL(18,4)"   # 金额/份额精度
PRICE_DECIMAL = "DECIMAL(12,6)"    # 价格/净值精度

# ── 路径 ──────────────────────────────────────────────────────────

DEFAULT_DB_DIR = Path(__file__).resolve().parents[2] / "storage" / "ledgers"
DEFAULT_DB_PATH = DEFAULT_DB_DIR / "account.db"
DEFAULT_ACCOUNT_ID = "MAIN"

# ── 线程本地连接 ──────────────────────────────────────────────────

_local = threading.local()


def get_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    """获取当前线程的 SQLite 连接。"""
    key = db_path or str(DEFAULT_DB_PATH)
    if not hasattr(_local, "conn") or _local.conn_path != key:
        path = Path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), detect_types=sqlite3.PARSE_DECLTYPES)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        _local.conn = conn
        _local.conn_path = key
    return _local.conn


# ── Schema 定义 ──────────────────────────────────────────────────

SCHEMA_VERSION = 1

SCHEMA_SQL = """
-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER NOT NULL,
    applied_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    note        TEXT
);

-- ================================================================
-- 1. accounts: 账户主表
-- ================================================================
CREATE TABLE IF NOT EXISTS accounts (
    account_id          TEXT PRIMARY KEY,
    account_mode        TEXT    NOT NULL DEFAULT 'MICRO',
    total_capital_in    DECIMAL(18,4) NOT NULL DEFAULT 0,
    additional_capital  DECIMAL(18,4) NOT NULL DEFAULT 0,
    withdrawn_capital   DECIMAL(18,4) NOT NULL DEFAULT 0,
    total_assets        DECIMAL(18,4) NOT NULL DEFAULT 0,
    cash_balance        DECIMAL(18,4) NOT NULL DEFAULT 0,
    realized_pnl        DECIMAL(18,4) NOT NULL DEFAULT 0,
    unrealized_pnl      DECIMAL(18,4) NOT NULL DEFAULT 0,
    total_fees          DECIMAL(18,4) NOT NULL DEFAULT 0,
    total_taxes         DECIMAL(18,4) NOT NULL DEFAULT 0,
    live_order_permission INTEGER NOT NULL DEFAULT 0,
    currency            TEXT    NOT NULL DEFAULT 'CNY',
    created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    note                TEXT
);

-- ================================================================
-- 2. assets: 资产主数据（Ledger 本地副本）
-- ================================================================
CREATE TABLE IF NOT EXISTS assets (
    symbol              TEXT    NOT NULL,
    name                TEXT    NOT NULL,
    asset_type          TEXT    NOT NULL,
    exchange            TEXT,
    currency            TEXT    NOT NULL DEFAULT 'CNY',
    lot_size            INTEGER NOT NULL DEFAULT 1,
    risk_clusters       TEXT,
    strategy_eligible   INTEGER NOT NULL DEFAULT 1,
    execution_eligible  INTEGER NOT NULL DEFAULT 1,
    source              TEXT    NOT NULL DEFAULT 'MANUAL',
    updated_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (symbol)
);

-- ================================================================
-- 3. transactions: 不可变交易事件
-- ================================================================
CREATE TABLE IF NOT EXISTS transactions (
    transaction_id      TEXT    NOT NULL,
    account_id          TEXT    NOT NULL,
    symbol              TEXT    NOT NULL,
    strategy_id         TEXT,
    side                TEXT    NOT NULL CHECK (side IN ('BUY','SELL','DIVIDEND','FEE','TRANSFER_IN','TRANSFER_OUT','REVERSAL')),
    quantity            DECIMAL(18,4) NOT NULL,
    price               DECIMAL(12,6),
    gross_amount        DECIMAL(18,4) NOT NULL,
    fee                 DECIMAL(18,4) NOT NULL DEFAULT 0,
    execution_date      TEXT    NOT NULL,
    confirmation_date   TEXT,
    source              TEXT    NOT NULL DEFAULT 'MANUAL',
    reversal_for        TEXT,
    note                TEXT,
    created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (transaction_id, account_id),
    FOREIGN KEY (account_id) REFERENCES accounts(account_id)
);

-- ================================================================
-- 4. orders: 订单记录
-- ================================================================
CREATE TABLE IF NOT EXISTS orders (
    order_id            TEXT    NOT NULL,
    account_id          TEXT    NOT NULL,
    strategy_id         TEXT,
    symbol              TEXT    NOT NULL,
    side                TEXT    NOT NULL CHECK (side IN ('BUY','SELL')),
    target_amount       DECIMAL(18,4) NOT NULL,
    estimated_quantity  DECIMAL(18,4),
    status              TEXT    NOT NULL DEFAULT 'DRAFT',
    environment         TEXT    NOT NULL DEFAULT 'LIVE',
    signal_date         TEXT,
    earliest_exec_date  TEXT,
    expiration_date     TEXT,
    estimated_cost      DECIMAL(18,4) DEFAULT 0,
    parent_decision_id  TEXT,
    source              TEXT    NOT NULL DEFAULT 'SYSTEM',
    created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (order_id, account_id),
    FOREIGN KEY (account_id) REFERENCES accounts(account_id)
);

-- ================================================================
-- 5. executions: 执行记录
-- ================================================================
CREATE TABLE IF NOT EXISTS executions (
    execution_id        TEXT    NOT NULL,
    order_id            TEXT,
    account_id          TEXT    NOT NULL,
    symbol              TEXT    NOT NULL,
    side                TEXT    NOT NULL CHECK (side IN ('BUY','SELL')),
    filled_quantity     DECIMAL(18,4) NOT NULL,
    filled_price        DECIMAL(12,6),
    filled_amount       DECIMAL(18,4) NOT NULL,
    fee                 DECIMAL(18,4) NOT NULL DEFAULT 0,
    execution_date      TEXT    NOT NULL,
    settlement_date     TEXT,
    source              TEXT    NOT NULL DEFAULT 'BROKER',
    created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (execution_id, account_id),
    FOREIGN KEY (account_id) REFERENCES accounts(account_id),
    FOREIGN KEY (order_id, account_id) REFERENCES orders(order_id, account_id)
);

-- ================================================================
-- 6. position_snapshots: 持仓快照（由对账生成）
-- ================================================================
CREATE TABLE IF NOT EXISTS position_snapshots (
    snapshot_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id          TEXT    NOT NULL,
    symbol              TEXT    NOT NULL,
    quantity            DECIMAL(18,4) NOT NULL DEFAULT 0,
    available_quantity  DECIMAL(18,4) NOT NULL DEFAULT 0,
    average_cost        DECIMAL(12,6),
    market_value        DECIMAL(18,4) NOT NULL DEFAULT 0,
    strategy_allocations TEXT,
    snapshot_date       TEXT    NOT NULL,
    source              TEXT    NOT NULL DEFAULT 'RECONCILIATION',
    created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (account_id) REFERENCES accounts(account_id)
);

-- ================================================================
-- 7. reconciliation_runs: 对账运行记录
-- ================================================================
CREATE TABLE IF NOT EXISTS reconciliation_runs (
    run_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id          TEXT    NOT NULL,
    run_date            TEXT    NOT NULL,
    status              TEXT    NOT NULL DEFAULT 'PENDING',
    ledger_cash         DECIMAL(18,4),
    declared_cash       DECIMAL(18,4),
    cash_diff           DECIMAL(18,4),
    ledger_value        DECIMAL(18,4),
    declared_value      DECIMAL(18,4),
    value_diff          DECIMAL(18,4),
    mismatches          TEXT,
    resolved            INTEGER NOT NULL DEFAULT 0,
    note                TEXT,
    created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (account_id) REFERENCES accounts(account_id)
);

-- ================================================================
-- 8. audit_log: 审计日志（记录所有关键状态变更）
-- ================================================================
CREATE TABLE IF NOT EXISTS audit_log (
    log_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id          TEXT,
    action              TEXT    NOT NULL,
    entity_type         TEXT,
    entity_id           TEXT,
    old_value           TEXT,
    new_value           TEXT,
    source              TEXT    NOT NULL DEFAULT 'SYSTEM',
    created_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_transactions_account ON transactions(account_id);
CREATE INDEX IF NOT EXISTS idx_transactions_symbol ON transactions(symbol);
CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(execution_date);
CREATE INDEX IF NOT EXISTS idx_orders_account ON orders(account_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_executions_account ON executions(account_id);
CREATE INDEX IF NOT EXISTS idx_executions_order ON executions(order_id);
CREATE INDEX IF NOT EXISTS idx_position_snapshots_account ON position_snapshots(account_id, snapshot_date);
CREATE INDEX IF NOT EXISTS idx_reconciliation_runs_account ON reconciliation_runs(account_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_account ON audit_log(account_id);
"""


# ── Schema 管理 ──────────────────────────────────────────────────

def ensure_schema(db_path: Path | str | None = None) -> int:
    """确保数据库 schema 存在，返回当前 schema 版本。"""
    conn = get_connection(db_path)
    conn.executescript(SCHEMA_SQL)

    cursor = conn.execute("SELECT version FROM schema_version ORDER BY applied_at DESC LIMIT 1")
    row = cursor.fetchone()
    if row is None:
        conn.execute("INSERT INTO schema_version (version, note) VALUES (?, ?)", (SCHEMA_VERSION, "V4.6 Ledger 初始 schema"))
        conn.commit()
        return SCHEMA_VERSION
    return int(row["version"])


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_audit_log(
    account_id: str | None,
    action: str,
    entity_type: str | None = None,
    entity_id: str | None = None,
    old_value: str | None = None,
    new_value: str | None = None,
    source: str = "SYSTEM",
    db_path: Path | str | None = None,
) -> int:
    conn = get_connection(db_path)
    cursor = conn.execute(
        """INSERT INTO audit_log (account_id, action, entity_type, entity_id, old_value, new_value, source)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (account_id, action, entity_type, entity_id, old_value, new_value, source),
    )
    conn.commit()
    return cursor.lastrowid
