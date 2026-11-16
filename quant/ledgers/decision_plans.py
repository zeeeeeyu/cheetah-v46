"""V4.6 决策计划账（第二本账）—— 持久化 DecisionProposal 及其 OrderIntent。

物理隔离：本模块只连接 decision_plans.db。
写入受 guards.assert_write_allowed('decision_plans', source) 约束。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..domain.enums import Environment, OrderSide, OrderStatus
from ..domain.orders import OrderIntent
from ..domain.proposals import DecisionProposal
from . import DECISION_PLANS_DB, get_connection, now_iso
from .guards import assert_write_allowed

_LEDGER = "decision_plans"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS decision_proposals (
    proposal_id         TEXT PRIMARY KEY,
    account_id          TEXT NOT NULL,
    as_of               TEXT NOT NULL,
    environment         TEXT NOT NULL,
    source_portfolio_id TEXT,
    approval_required   INTEGER NOT NULL DEFAULT 1,
    approval_status     TEXT NOT NULL DEFAULT 'PENDING',
    risk_metrics        TEXT NOT NULL DEFAULT '{}',
    reasons             TEXT NOT NULL DEFAULT '[]',
    metadata            TEXT NOT NULL DEFAULT '{}',
    source              TEXT NOT NULL,
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS proposal_order_intents (
    order_id            TEXT NOT NULL,
    proposal_id         TEXT NOT NULL,
    account_id          TEXT NOT NULL,
    strategy_id         TEXT NOT NULL,
    symbol              TEXT NOT NULL,
    side                TEXT NOT NULL,
    target_amount       DECIMAL(18,4) NOT NULL,
    estimated_quantity  DECIMAL(18,4) NOT NULL DEFAULT 0,
    signal_date         TEXT,
    earliest_execution_date TEXT,
    expiration_date     TEXT,
    estimated_cost      DECIMAL(18,4) NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'DRAFT',
    environment         TEXT NOT NULL DEFAULT 'LIVE',
    reasons             TEXT NOT NULL DEFAULT '[]',
    parent_decision_id  TEXT,
    updated_at          TEXT,
    PRIMARY KEY (order_id, proposal_id),
    FOREIGN KEY (proposal_id) REFERENCES decision_proposals(proposal_id)
);

CREATE INDEX IF NOT EXISTS idx_decision_proposals_account ON decision_proposals(account_id, as_of);
CREATE INDEX IF NOT EXISTS idx_proposal_orders_proposal ON proposal_order_intents(proposal_id);
"""


def ensure_schema(db_path: Path | str | None = None) -> None:
    conn = get_connection(db_path or DECISION_PLANS_DB)
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def _order_to_row(o: OrderIntent, proposal_id: str) -> tuple:
    return (
        o.order_id, proposal_id, o.account_id, o.strategy_id, o.symbol, o.side.value,
        o.target_amount, o.estimated_quantity, o.signal_date, o.earliest_execution_date,
        o.expiration_date, o.estimated_cost, o.status.value, o.environment.value,
        json.dumps(list(o.reasons), ensure_ascii=False), o.parent_decision_id, o.updated_at,
    )


def _row_to_order(row: Any) -> OrderIntent:
    return OrderIntent(
        order_id=row["order_id"],
        account_id=row["account_id"],
        strategy_id=row["strategy_id"],
        symbol=row["symbol"],
        side=OrderSide(row["side"]),
        target_amount=float(row["target_amount"]),
        estimated_quantity=float(row["estimated_quantity"]),
        signal_date=row["signal_date"],
        earliest_execution_date=row["earliest_execution_date"],
        expiration_date=row["expiration_date"],
        estimated_cost=float(row["estimated_cost"]),
        status=OrderStatus(row["status"]),
        environment=Environment(row["environment"]),
        reasons=tuple(json.loads(row["reasons"])),
        parent_decision_id=row["parent_decision_id"],
        updated_at=row["updated_at"],
    )


def write_decision_proposal(
    proposal: DecisionProposal,
    source: str = "DECISION_ENGINE",
    db_path: Path | str | None = None,
) -> str:
    """写入一个决策提案及其订单意图（幂等：proposal_id 冲突则覆盖）。"""
    assert_write_allowed(_LEDGER, source)
    ensure_schema(db_path)
    conn = get_connection(db_path or DECISION_PLANS_DB)
    conn.execute(
        """INSERT OR REPLACE INTO decision_proposals
           (proposal_id, account_id, as_of, environment, source_portfolio_id,
            approval_required, approval_status, risk_metrics, reasons, metadata, source, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            proposal.proposal_id, proposal.account_id, proposal.as_of, proposal.environment.value,
            proposal.source_portfolio_id, 1 if proposal.approval_required else 0,
            proposal.approval_status, json.dumps(proposal.risk_metrics, ensure_ascii=False),
            json.dumps(list(proposal.reasons), ensure_ascii=False),
            json.dumps(proposal.metadata, ensure_ascii=False), source, now_iso(),
        ),
    )
    # 覆盖式重写该提案的订单意图
    conn.execute("DELETE FROM proposal_order_intents WHERE proposal_id = ?", (proposal.proposal_id,))
    for o in proposal.order_intents:
        conn.execute(
            """INSERT INTO proposal_order_intents
               (order_id, proposal_id, account_id, strategy_id, symbol, side, target_amount,
                estimated_quantity, signal_date, earliest_execution_date, expiration_date,
                estimated_cost, status, environment, reasons, parent_decision_id, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            _order_to_row(o, proposal.proposal_id),
        )
    conn.commit()
    return proposal.proposal_id


def read_decision_proposal(proposal_id: str, db_path: Path | str | None = None) -> DecisionProposal | None:
    ensure_schema(db_path)
    conn = get_connection(db_path or DECISION_PLANS_DB)
    row = conn.execute("SELECT * FROM decision_proposals WHERE proposal_id = ?", (proposal_id,)).fetchone()
    if row is None:
        return None
    order_rows = conn.execute(
        "SELECT * FROM proposal_order_intents WHERE proposal_id = ? ORDER BY order_id", (proposal_id,)
    ).fetchall()
    orders = tuple(_row_to_order(r) for r in order_rows)
    return DecisionProposal(
        proposal_id=row["proposal_id"],
        account_id=row["account_id"],
        as_of=row["as_of"],
        environment=Environment(row["environment"]),
        source_portfolio_id=row["source_portfolio_id"],
        order_intents=orders,
        approval_required=bool(row["approval_required"]),
        approval_status=row["approval_status"],
        risk_metrics=json.loads(row["risk_metrics"]),
        reasons=tuple(json.loads(row["reasons"])),
        metadata=json.loads(row["metadata"]),
    )
