"""V4.6 成交服务和对账。"""
from __future__ import annotations
from dataclasses import dataclass, field; from datetime import datetime; from typing import Any
from quant.domain.enums import OrderStatus, OrderSide; from quant.domain.orders import OrderIntent
from quant.accounts import get_connection, ensure_schema, now_iso, write_audit_log, DEFAULT_DB_PATH
from quant.ledgers.guards import assert_write_allowed

@dataclass
class ExecutionRecord:
    execution_id: str; order_id: str; symbol: str; side: str; filled_quantity: float; filled_price: float
    filled_amount: float; fee: float = 0.0; execution_date: str = ""; source: str = "MANUAL"
    def to_dict(self) -> dict[str, Any]: return {"execution_id": self.execution_id, "order_id": self.order_id, "symbol": self.symbol, "side": self.side, "filled_quantity": self.filled_quantity, "filled_price": self.filled_price, "filled_amount": self.filled_amount, "fee": self.fee, "execution_date": self.execution_date, "source": self.source}

@dataclass
class ReconciliationReport:
    order_id: str; symbol: str; expected_quantity: float; actual_quantity: float; expected_amount: float; actual_amount: float
    differences: list[str] = field(default_factory=list); reconciled: bool = False
    def to_dict(self) -> dict[str, Any]: return {"order_id": self.order_id, "symbol": self.symbol, "expected_quantity": self.expected_quantity, "actual_quantity": self.actual_quantity, "expected_amount": self.expected_amount, "actual_amount": self.actual_amount, "differences": list(self.differences), "reconciled": self.reconciled}

class ExecutionService:
    @staticmethod
    def confirm_execution(order, execution):
        """确认成交。

        只有 SUBMITTED 状态的订单可以接收成交。
        DRAFT / RISK_APPROVED / USER_APPROVED 必须先提交。
        CANCELLED / EXPIRED / FAILED / RECONCILED 不可成交。
        """
        if order.status not in (OrderStatus.SUBMITTED, OrderStatus.PARTIALLY_FILLED):
            raise ValueError(f"只有 SUBMITTED 或 PARTIALLY_FILLED 订单可确认成交，当前: {order.status}")

        warnings = []; qty = order.estimated_quantity; fill = min(execution.filled_quantity, qty)
        if execution.filled_quantity > qty: warnings.append(f"成交 {execution.filled_quantity} > 订单 {qty}")
        ns = OrderStatus.PARTIALLY_FILLED if 0 < fill < qty else OrderStatus.FILLED if fill >= qty else None
        if ns is None: raise ValueError(f"无效成交量: {execution.filled_quantity}")
        return OrderIntent(order_id=order.order_id, account_id=order.account_id, strategy_id=order.strategy_id, symbol=order.symbol, side=order.side, target_amount=order.target_amount, estimated_quantity=qty - fill if ns == OrderStatus.PARTIALLY_FILLED else 0, signal_date=order.signal_date, earliest_execution_date=order.earliest_execution_date, expiration_date=order.expiration_date, estimated_cost=order.estimated_cost + execution.fee, status=ns, environment=order.environment, reasons=order.reasons + (f"已成交 {fill}/{qty}",), parent_decision_id=order.parent_decision_id, updated_at=now_iso()), warnings

    @staticmethod
    def expire_order(order): return OrderIntent(order_id=order.order_id, account_id=order.account_id, strategy_id=order.strategy_id, symbol=order.symbol, side=order.side, target_amount=order.target_amount, estimated_quantity=order.estimated_quantity, signal_date=order.signal_date, earliest_execution_date=order.earliest_execution_date, expiration_date=order.expiration_date, estimated_cost=order.estimated_cost, status=OrderStatus.EXPIRED, environment=order.environment, reasons=order.reasons + ("过期",), parent_decision_id=order.parent_decision_id, updated_at=now_iso())

    @staticmethod
    def cancel_order(order): return OrderIntent(order_id=order.order_id, account_id=order.account_id, strategy_id=order.strategy_id, symbol=order.symbol, side=order.side, target_amount=order.target_amount, estimated_quantity=order.estimated_quantity, signal_date=order.signal_date, earliest_execution_date=order.earliest_execution_date, expiration_date=order.expiration_date, estimated_cost=order.estimated_cost, status=OrderStatus.CANCELLED, environment=order.environment, reasons=order.reasons + ("取消",), parent_decision_id=order.parent_decision_id, updated_at=now_iso())

class ReconciliationService:
    def __init__(self, db_path: str | None = None): self.db_path = db_path or str(DEFAULT_DB_PATH)
    def reconcile(self, order, execution):
        r = ReconciliationReport(order_id=order.order_id, symbol=order.symbol, expected_quantity=order.estimated_quantity, actual_quantity=execution.filled_quantity, expected_amount=order.target_amount, actual_amount=execution.filled_amount)
        if abs(execution.filled_quantity - order.estimated_quantity) > 1e-6: r.differences.append(f"数量差: 预期 {order.estimated_quantity:.2f} 实际 {execution.filled_quantity:.2f}")
        if abs(execution.filled_amount - order.target_amount) > 1e-6: r.differences.append(f"金额差: 预期 {order.target_amount:.2f} 实际 {execution.filled_amount:.2f}")
        r.reconciled = len(r.differences) == 0; return r
    def write_to_ledger(self, order, execution):
        """将已对账的成交写入 Ledger。

        position_snapshot 保存的是聚合后的总持仓数量（原持仓 + 本次成交量），
        不是本次成交数量。
        """
        if order.status != OrderStatus.RECONCILED: raise ValueError(f"需 RECONCILED 状态")
        conn = get_connection(self.db_path); ensure_schema(self.db_path)
        try:
            conn.execute("BEGIN TRANSACTION")
            c = conn.execute("SELECT 1 FROM executions WHERE execution_id=? AND account_id=?", (execution.execution_id, order.account_id))
            if c.fetchone() is not None: conn.rollback(); return
            conn.execute("INSERT INTO executions (execution_id, order_id, account_id, symbol, side, filled_quantity, filled_price, filled_amount, fee, execution_date, source, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (execution.execution_id, order.order_id, order.account_id, order.symbol, execution.side, execution.filled_quantity, execution.filled_price, execution.filled_amount, execution.fee, execution.execution_date, execution.source, now_iso()))
            conn.execute("INSERT OR IGNORE INTO transactions (transaction_id, account_id, symbol, strategy_id, side, quantity, price, gross_amount, fee, execution_date, source, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (f"TX-{execution.execution_id}", order.account_id, order.symbol, order.strategy_id, execution.side, execution.filled_quantity, execution.filled_price, execution.filled_amount, execution.fee, execution.execution_date, execution.source, now_iso()))
            # position_snapshot 使用聚合后的总持仓
            # GAP-4: 通过台账白名单守卫校验来源，硬编码 'RECONCILIATION' 不再绕过守卫
            assert_write_allowed("position_snapshots", "RECONCILIATION")
            prev = conn.execute("SELECT SUM(quantity) as total_qty, SUM(gross_amount) as total_amt FROM transactions WHERE account_id=? AND symbol=? AND side='BUY' AND reversal_for IS NULL", (order.account_id, order.symbol)).fetchone()
            total_qty = float(prev["total_qty"] or 0)
            total_amt = float(prev["total_amt"] or 0)
            avg_cost = total_amt / total_qty if total_qty > 0 else execution.filled_price
            market_value = total_qty * execution.filled_price
            conn.execute("INSERT INTO position_snapshots (account_id, symbol, quantity, available_quantity, average_cost, market_value, snapshot_date, source, created_at) VALUES (?,?,?,?,?,?,?,?,?)", (order.account_id, order.symbol, total_qty, total_qty, avg_cost, market_value, execution.execution_date, 'RECONCILIATION', now_iso()))
            write_audit_log(order.account_id, "EXECUTION_RECONCILED", "execution", execution.execution_id, new_value=str(execution.to_dict()), source="RECONCILIATION", db_path=self.db_path)
            conn.commit()
        except Exception:
            conn.rollback(); raise
