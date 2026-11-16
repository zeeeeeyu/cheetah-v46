"""V4.6 持仓聚合服务。

持仓只能由 Transaction Ledger 聚合生成。
不得直接把目标权重存为真实持仓。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import DEFAULT_DB_PATH, ensure_schema, get_connection, now_iso, write_audit_log


@dataclass
class AggregatedPosition:
    """聚合后的持仓视图（由 Ledger 计算，非持久化）。"""
    symbol: str
    total_quantity: float
    available_quantity: float
    average_cost: float
    total_cost: float
    market_value: float
    unrealized_pnl: float
    realized_pnl: float
    total_fees: float
    strategy_allocations: dict[str, float] = field(default_factory=dict)


def aggregate_positions(
    account_id: str,
    market_prices: dict[str, float] | None = None,
    as_of_date: str | None = None,
    db_path: Path | str | None = None,
) -> list[AggregatedPosition]:
    """从 Transaction Ledger 聚合当前持仓。

    通过累加所有已成交且未被冲正的交易来计算。
    冲正记录通过 reversal_for 字段关联。

    Args:
        account_id: 账户 ID
        market_prices: 可选，当前市价字典 {symbol: price}，用于计算未实现盈亏
        as_of_date: 可选，截至日期（用于历史快照）
        db_path: 数据库路径
    """
    ensure_schema(db_path)
    conn = get_connection(db_path)

    # Aggregate BUY/SELL transactions by symbol (excluding reversals)
    date_filter = ""
    params: list[Any] = [account_id]
    if as_of_date:
        date_filter = " AND execution_date <= ?"
        params.append(as_of_date)

    # Get all non-reversed transactions
    rows = conn.execute(
        f"""SELECT t.symbol, t.side, t.quantity, t.price, t.gross_amount, t.fee,
                   t.reversal_for, t.transaction_id
            FROM transactions t
            WHERE t.account_id = ? AND t.side IN ('BUY','SELL','TRANSFER_IN'){date_filter}
            ORDER BY t.execution_date ASC, t.created_at ASC""",
        params,
    ).fetchall()

    # Track reversals
    reversed_ids: set[str] = set()
    tx_map: dict[str, dict] = {}
    for row in rows:
        tx = dict(row)
        tx_map[tx["transaction_id"]] = tx
        if tx["reversal_for"]:
            reversed_ids.add(tx["reversal_for"])

    # Aggregate by symbol
    symbol_data: dict[str, dict[str, float]] = {}
    for tx_id, tx in tx_map.items():
        if tx_id in reversed_ids:
            continue
        sym = tx["symbol"]
        if sym not in symbol_data:
            symbol_data[sym] = {
                "buy_qty": 0.0, "buy_cost": 0.0, "sell_qty": 0.0, "sell_proceeds": 0.0,
                "total_fees": 0.0, "realized_pnl": 0.0, "total_cost": 0.0,
            }
        d = symbol_data[sym]
        qty = float(tx["quantity"] or 0)
        amount = float(tx["gross_amount"] or 0)
        fee = float(tx["fee"] or 0)
        price = float(tx["price"]) if tx["price"] else 0

        if tx["side"] == "BUY":
            d["buy_qty"] += qty
            d["buy_cost"] += amount
            d["total_fees"] += fee
        elif tx["side"] == "SELL":
            d["sell_qty"] += qty
            d["sell_proceeds"] += amount
            d["total_fees"] += fee
            # Realized P&L = proceeds - (sold_qty * avg_cost)
            avg_cost_before = d["buy_cost"] / d["buy_qty"] if d["buy_qty"] > 0 else 0
            d["realized_pnl"] += amount - (qty * avg_cost_before)
        elif tx["side"] == "TRANSFER_IN":
            # 旧状态迁移（D-03 采纳方案 B）：以金额（gross_amount）为价值单位的持仓投影。
            # 真实持仓由 rebuild_from_transactions() 聚合生成，TRANSFER_IN 代表一次价值迁入，
            # 此处把金额作为数量单位（price=1.0），使聚合结果 market_value == 迁入价值。
            d["buy_qty"] += amount
            d["buy_cost"] += amount
            d["total_fees"] += fee

    # Build positions
    positions: list[AggregatedPosition] = []
    for symbol, d in symbol_data.items():
        total_qty = d["buy_qty"] - d["sell_qty"]
        if total_qty <= 0 and d["buy_qty"] == 0:
            continue
        avg_cost = d["buy_cost"] / d["buy_qty"] if d["buy_qty"] > 0 else 0
        market_price = float(market_prices.get(symbol, avg_cost)) if market_prices else avg_cost
        market_value = total_qty * market_price
        total_cost = total_qty * avg_cost
        unrealized = market_value - total_cost

        positions.append(AggregatedPosition(
            symbol=symbol,
            total_quantity=total_qty,
            available_quantity=total_qty,
            average_cost=avg_cost,
            total_cost=total_cost,
            market_value=market_value,
            unrealized_pnl=unrealized,
            realized_pnl=d["realized_pnl"],
            total_fees=d["total_fees"],
        ))

    return positions


def get_account_summary(
    account_id: str,
    db_path: Path | str | None = None,
) -> dict[str, Any]:
    """获取账户摘要。"""
    ensure_schema(db_path)
    conn = get_connection(db_path)
    row = conn.execute(
        """SELECT * FROM accounts WHERE account_id = ?""", (account_id,)
    ).fetchone()
    if row is None:
        return {}
    return dict(row)
