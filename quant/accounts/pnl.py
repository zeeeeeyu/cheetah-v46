"""V4.6 盈亏计算服务。

明确区分：
- 本金变化（capital flow）不计入利润
- 已实现盈亏（realized PnL）
- 未实现盈亏（unrealized PnL）
- 累计费用
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import DEFAULT_DB_PATH, ensure_schema, get_connection, now_iso
from .position_service import aggregate_positions


@dataclass
class PnLReport:
    account_id: str
    as_of_date: str
    total_capital_in: float
    additional_capital: float
    withdrawn_capital: float
    net_capital_flow: float
    realized_pnl: float
    unrealized_pnl: float
    total_fees: float
    portfolio_value: float
    total_return_amount: float
    total_return_pct: float
    holdings: list[dict[str, Any]] = field(default_factory=list)


def compute_pnl(
    account_id: str,
    as_of_date: str,
    market_prices: dict[str, float] | None = None,
    db_path: Path | str | None = None,
) -> PnLReport:
    """计算账户盈亏。

    设计原则：
    - 本金变化（TRANSFER_IN / TRANSFER_OUT）不参与盈亏计算
    - 已实现盈亏来自 BUY/SELL 交易匹配
    - 未实现盈亏来自当前市价 vs 平均成本
    """
    ensure_schema(db_path)
    conn = get_connection(db_path)

    # 1. Get account
    row = conn.execute("SELECT * FROM accounts WHERE account_id = ?", (account_id,)).fetchone()
    if row is None:
        raise ValueError(f"账户 {account_id} 不存在")

    # 2. Calculate capital flows from TRANSFER_IN/TRANSFER_OUT transactions
    capital_in = conn.execute(
        """SELECT COALESCE(SUM(gross_amount), 0) FROM transactions
           WHERE account_id = ? AND side = 'TRANSFER_IN'""",
        (account_id,),
    ).fetchone()[0] or 0.0

    capital_out = conn.execute(
        """SELECT COALESCE(SUM(gross_amount), 0) FROM transactions
           WHERE account_id = ? AND side = 'TRANSFER_OUT'""",
        (account_id,),
    ).fetchone()[0] or 0.0

    # 3. Calculate realized PnL from BUY/SELL pairs
    realized_pnl = conn.execute(
        """SELECT COALESCE(SUM(
               CASE WHEN side = 'SELL' THEN gross_amount ELSE 0 END
               -
               CASE WHEN side = 'SELL' THEN quantity * (
                   SELECT COALESCE(AVG(t2.price), 0)
                   FROM transactions t2
                   WHERE t2.account_id = t.account_id AND t2.symbol = t.symbol
                     AND t2.side = 'BUY' AND t2.transaction_id <= t.transaction_id
                     AND t2.reversal_for IS NULL
               ) ELSE 0 END
           ), 0) FROM transactions t
           WHERE account_id = ? AND side = 'SELL' AND reversal_for IS NULL""",
        (account_id,),
    ).fetchone()[0] or 0.0

    # 4. Aggregate current positions for unrealized PnL
    positions = aggregate_positions(account_id, market_prices, db_path=db_path)
    unrealized_pnl = sum(p.unrealized_pnl for p in positions)
    total_fees = sum(p.total_fees for p in positions)
    portfolio_value = sum(p.market_value for p in positions)

    # Also add cash balance
    cash = float(row["cash_balance"] or 0)
    total_portfolio = portfolio_value + cash

    # 5. Total return
    net_capital = float(capital_in - capital_out)
    total_return_amount = realized_pnl + unrealized_pnl - total_fees
    total_return_pct = (total_return_amount / net_capital * 100) if net_capital > 0 else 0.0

    holdings_list = []
    for p in positions:
        holdings_list.append({
            "symbol": p.symbol,
            "quantity": p.total_quantity,
            "avg_cost": p.average_cost,
            "market_value": p.market_value,
            "unrealized_pnl": p.unrealized_pnl,
            "realized_pnl": p.realized_pnl,
            "total_fees": p.total_fees,
            "return_pct": (p.unrealized_pnl / p.total_cost * 100) if p.total_cost > 0 else 0.0,
        })

    return PnLReport(
        account_id=account_id,
        as_of_date=as_of_date,
        total_capital_in=float(capital_in),
        additional_capital=float(capital_in - row["total_capital_in"]),
        withdrawn_capital=float(capital_out),
        net_capital_flow=float(net_capital),
        realized_pnl=float(realized_pnl),
        unrealized_pnl=float(unrealized_pnl),
        total_fees=float(total_fees),
        portfolio_value=float(total_portfolio),
        total_return_amount=float(total_return_amount),
        total_return_pct=float(total_return_pct),
        holdings=holdings_list,
    )
