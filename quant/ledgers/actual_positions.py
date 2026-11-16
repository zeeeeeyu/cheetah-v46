"""V4.6 真实持仓账（第三本账）—— 持久化 ActualPosition。

这是三本账里最严格的一本：
- 物理隔离：只连接 actual_positions.db。
- 写入唯一合法路径：对账（RECONCILIATION）。
- 双重护栏：领域层（ActualPosition.__post_init__）+ 账本层（guards）。
- 提供 rebuild_from_transactions()：从不可变交易账聚合出真实持仓，
  这是真实持仓进入本账的唯一"官方"入口。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..domain.positions import ActualPosition
from . import ACTUAL_POSITIONS_DB, get_connection, now_iso
from .guards import RECONCILIATION_SOURCE, assert_reconciliation_write

_LEDGER = "actual_positions"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS actual_positions (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id            TEXT NOT NULL,
    symbol                TEXT NOT NULL,
    quantity              DECIMAL(18,4) NOT NULL DEFAULT 0,
    available_quantity    DECIMAL(18,4) NOT NULL DEFAULT 0,
    average_cost          DECIMAL(12,6) NOT NULL DEFAULT 0,
    market_value          DECIMAL(18,4) NOT NULL DEFAULT 0,
    as_of                 TEXT NOT NULL,
    reconciliation_run_id TEXT NOT NULL,
    source                TEXT NOT NULL,
    strategy_allocations  TEXT NOT NULL DEFAULT '{}',
    created_at            TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_actual_positions_account ON actual_positions(account_id, as_of);
CREATE INDEX IF NOT EXISTS idx_actual_positions_run ON actual_positions(reconciliation_run_id);
"""


def ensure_schema(db_path: Path | str | None = None) -> None:
    conn = get_connection(db_path or ACTUAL_POSITIONS_DB)
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def write_actual_position(
    position: ActualPosition,
    source: str = RECONCILIATION_SOURCE,
    db_path: Path | str | None = None,
) -> int:
    """写入一条真实持仓。

    双重护栏：
    1. position 本身在 __post_init__ 已确保 source==RECONCILIATION 且带 run_id；
    2. 此处再用账本守卫复核 source 与 run_id，Fail-Closed。
    """
    assert_reconciliation_write(source, position.reconciliation_run_id)
    # 领域对象与写入来源必须一致，杜绝"偷梁换柱"
    if position.source != source:
        from .guards import LedgerWriteViolation
        raise LedgerWriteViolation(
            f"持仓对象来源({position.source!r})与写入来源({source!r})不一致，拒绝写入。"
        )
    ensure_schema(db_path)
    conn = get_connection(db_path or ACTUAL_POSITIONS_DB)
    cur = conn.execute(
        """INSERT INTO actual_positions
           (account_id, symbol, quantity, available_quantity, average_cost, market_value,
            as_of, reconciliation_run_id, source, strategy_allocations, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (
            position.account_id, position.symbol, position.quantity, position.available_quantity,
            position.average_cost, position.market_value, position.as_of,
            position.reconciliation_run_id, position.source,
            json.dumps(position.strategy_allocations, ensure_ascii=False), now_iso(),
        ),
    )
    conn.commit()
    return cur.lastrowid


def get_latest_positions(account_id: str, db_path: Path | str | None = None) -> list[ActualPosition]:
    """读取账户最近一次对账运行产生的真实持仓。"""
    ensure_schema(db_path)
    conn = get_connection(db_path or ACTUAL_POSITIONS_DB)
    latest = conn.execute(
        """SELECT reconciliation_run_id FROM actual_positions
           WHERE account_id = ? ORDER BY as_of DESC, id DESC LIMIT 1""",
        (account_id,),
    ).fetchone()
    if latest is None:
        return []
    run_id = latest["reconciliation_run_id"]
    rows = conn.execute(
        "SELECT * FROM actual_positions WHERE account_id = ? AND reconciliation_run_id = ? ORDER BY symbol",
        (account_id, run_id),
    ).fetchall()
    return [_row_to_position(r) for r in rows]


def _row_to_position(row: Any) -> ActualPosition:
    return ActualPosition(
        account_id=row["account_id"],
        symbol=row["symbol"],
        quantity=float(row["quantity"]),
        available_quantity=float(row["available_quantity"]),
        average_cost=float(row["average_cost"]),
        market_value=float(row["market_value"]),
        as_of=row["as_of"],
        reconciliation_run_id=row["reconciliation_run_id"],
        source=row["source"],
        strategy_allocations=json.loads(row["strategy_allocations"]),
    )


def rebuild_from_transactions(
    account_id: str,
    reconciliation_run_id: str,
    as_of: str,
    market_prices: dict[str, float] | None = None,
    account_db_path: Path | str | None = None,
    db_path: Path | str | None = None,
) -> list[ActualPosition]:
    """真实持仓进入本账的唯一官方入口：从不可变交易账聚合。

    调用 quant.accounts.position_service.aggregate_positions（只读交易账），
    把结果封装为带 reconciliation_run_id 的 ActualPosition 并写入本账。
    这确保：真实持仓永远只是交易账事实的投影，而非目标/决策的投影。
    """
    from ..accounts.position_service import aggregate_positions

    aggregated = aggregate_positions(
        account_id=account_id,
        market_prices=market_prices,
        as_of_date=as_of,
        db_path=account_db_path,
    )
    positions: list[ActualPosition] = []
    for ap in aggregated:
        pos = ActualPosition(
            account_id=account_id,
            symbol=ap.symbol,
            quantity=ap.total_quantity,
            available_quantity=ap.available_quantity,
            average_cost=ap.average_cost,
            market_value=ap.market_value,
            as_of=as_of,
            reconciliation_run_id=reconciliation_run_id,
            source=RECONCILIATION_SOURCE,
            strategy_allocations=dict(ap.strategy_allocations),
        )
        write_actual_position(pos, source=RECONCILIATION_SOURCE, db_path=db_path)
        positions.append(pos)
    return positions
