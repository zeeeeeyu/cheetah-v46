"""V4.6 旧状态只读导入器。

导入 V4.2 / V4.5.1 旧状态文件为 UNVERIFIED ledger 条目。
所有旧状态初始标记为 UNVERIFIED，不会自动启用 LIVE 订单权限。
导入后不得自动生成真实成交历史。
重复导入幂等。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any

from . import (
    AMOUNT_DECIMAL,
    DEFAULT_DB_PATH,
    ensure_schema,
    get_connection,
    now_iso,
    write_audit_log,
)


# ── 导入结果 ──────────────────────────────────────────────────────

@dataclass
class ImportResult:
    status: str                         # SUCCESS / PARTIAL / FAILED
    account_id: str
    transactions_imported: int
    positions_imported: int
    legacy_cash: float
    warnings: list[str]
    errors: list[str]


@dataclass
class LegacyAccountSnapshot:
    """解析后的旧状态快照，包含 fund 和 ETF 两个分支的状态。"""
    account_id: str = "LEGACY_MAIN"
    fund_weights: dict[str, float] = field(default_factory=dict)
    etf_weights: dict[str, float] = field(default_factory=dict)
    cash_weight: float = 1.0
    last_trade_date: str | None = None
    portfolio_value: float | None = None
    source_files: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def parse_v42_portfolio_state(path: Path) -> LegacyAccountSnapshot:
    """解析 V4.2 根目录的 portfolio_state.json。"""
    if not path.exists():
        raise FileNotFoundError(f"V4.2 状态文件不存在: {path}")
    snapshot = LegacyAccountSnapshot(source_files=[str(path)])
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    holdings = data.get("current_holdings", {})
    # V4.2 uses percentage weights (sum ~100), we normalize
    total = sum(float(v) for v in holdings.values()) or 1.0

    for symbol, weight_pct in holdings.items():
        weight = float(weight_pct) / total
        if symbol in ("现金/货基", "CASH", "cash"):
            snapshot.cash_weight = weight
        elif symbol.isdigit():
            snapshot.fund_weights[symbol] = weight
        else:
            snapshot.warnings.append(f"V4.2 未知资产代码: {symbol}")

    snapshot.last_trade_date = data.get("last_rebalance_date")
    return snapshot


def parse_v451_portfolio_state(path: Path) -> LegacyAccountSnapshot:
    """解析 V4.5.1 ETF 分支的 state/portfolio_state.json。"""
    if not path.exists():
        raise FileNotFoundError(f"V4.5.1 ETF 状态文件不存在: {path}")
    snapshot = LegacyAccountSnapshot(source_files=[str(path)])
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    weights = data.get("current_weights", {})
    for symbol, weight in weights.items():
        weight = float(weight)
        if symbol in ("CASH", "cash"):
            snapshot.cash_weight += weight
        elif symbol.isdigit():
            snapshot.etf_weights[symbol] = weight
        else:
            snapshot.warnings.append(f"V4.5.1 ETF 未知代码: {symbol}")

    snapshot.last_trade_date = data.get("last_executed_trade_date")
    return snapshot


def parse_v451_fund_portfolio_state(path: Path) -> LegacyAccountSnapshot:
    """解析 V4.5.1 基金分支的 state/fund_portfolio_state.json。"""
    if not path.exists():
        raise FileNotFoundError(f"V4.5.1 基金状态文件不存在: {path}")
    snapshot = LegacyAccountSnapshot(source_files=[str(path)])
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    weights = data.get("current_weights", {})
    for symbol, weight in weights.items():
        weight = float(weight)
        if symbol in ("CASH", "cash"):
            snapshot.cash_weight += weight
        elif symbol.isdigit():
            snapshot.fund_weights[symbol] = weight
        else:
            snapshot.warnings.append(f"V4.5.1 基金未知代码: {symbol}")

    snapshot.last_trade_date = data.get("last_executed_trade_date")
    snapshot.portfolio_value = data.get("portfolio_value_cny")
    return snapshot


def merge_legacy_snapshots(snapshots: list[LegacyAccountSnapshot]) -> LegacyAccountSnapshot:
    """合并多个旧状态快照（V4.2 + V4.5.1 ETF + V4.5.1 基金）。"""
    if not snapshots:
        return LegacyAccountSnapshot()
    merged = LegacyAccountSnapshot()
    merged.source_files = []
    total_fund = {}
    total_etf = {}
    total_cash = 0.0
    seen_warnings = set()

    for snap in snapshots:
        merged.source_files.extend(snap.source_files)
        for k, v in snap.fund_weights.items():
            total_fund[k] = total_fund.get(k, 0.0) + v
        for k, v in snap.etf_weights.items():
            total_etf[k] = total_etf.get(k, 0.0) + v
        total_cash += snap.cash_weight
        for w in snap.warnings:
            if w not in seen_warnings:
                merged.warnings.append(w)
                seen_warnings.add(w)
        merged.last_trade_date = merged.last_trade_date or snap.last_trade_date
        merged.portfolio_value = merged.portfolio_value or snap.portfolio_value

    # Normalize combined weights
    merged.fund_weights = total_fund
    merged.etf_weights = total_etf
    merged.cash_weight = total_cash

    # Check total — log warning if > 1.0
    total = sum(total_fund.values()) + sum(total_etf.values()) + total_cash
    if abs(total - 1.0) > 0.01:
        merged.warnings.append(
            f"合并后权重总和为 {total:.4f}，与 1.0 有偏差；可能两套系统同时记录了相同资产的权重"
        )

    return merged


def import_legacy_to_ledger(
    snapshot: LegacyAccountSnapshot,
    db_path: Path | str | None = None,
    mark_unverified: bool = True,
) -> ImportResult:
    """将旧状态快照导入 Ledger，初始标记为 UNVERIFIED。

    不会自动生成真实成交历史。
    重复导入幂等（transaction_id 去重）。
    """
    from dataclasses import asdict

    db = Path(db_path) if db_path else DEFAULT_DB_PATH
    ensure_schema(db)
    conn = get_connection(db)
    account_id = snapshot.account_id
    imported_tx = 0
    imported_pos = 0
    errors: list[str] = []
    warnings = list(snapshot.warnings)

    try:
        conn.execute("BEGIN TRANSACTION")

        # 1. Ensure account exists (UNVERIFIED)
        cursor = conn.execute("SELECT account_id FROM accounts WHERE account_id = ?", (account_id,))
        if cursor.fetchone() is None:
            conn.execute(
                """INSERT INTO accounts (account_id, account_mode, total_assets, cash_balance,
                   live_order_permission, note, created_at, updated_at)
                   VALUES (?, 'MICRO', ?, ?, 0, ?, ?, ?)""",
                (
                    account_id,
                    snapshot.portfolio_value or 0,
                    snapshot.cash_weight * (snapshot.portfolio_value or 0),
                    f"旧状态导入 — UNVERIFIED。来源: {', '.join(snapshot.source_files)}",
                    now_iso(),
                    now_iso(),
                ),
            )
            write_audit_log(account_id, "ACCOUNT_CREATED", "account", account_id,
                            new_value=json.dumps({"source_files": snapshot.source_files, "status": "UNVERIFIED"}),
                            source="LEGACY_IMPORT", db_path=db)

        # 2. Import each fund position as a single UNVERIFIED transaction
        for symbol, weight in snapshot.fund_weights.items():
            tx_id = f"LEGACY_IMPORT_{account_id}_{symbol}_FUND"
            cursor = conn.execute("SELECT 1 FROM transactions WHERE transaction_id = ? AND account_id = ?",
                                  (tx_id, account_id))
            if cursor.fetchone() is not None:
                continue  # Idempotent skip

            value = weight * (snapshot.portfolio_value or 0)
            conn.execute(
                """INSERT INTO transactions (transaction_id, account_id, symbol, strategy_id, side,
                   quantity, gross_amount, fee, execution_date, source, note, created_at)
                   VALUES (?, ?, ?, 'LEGACY', 'TRANSFER_IN', 0, ?, 0, ?, ?, ?, ?)""",
                (tx_id, account_id, symbol, value,
                 snapshot.last_trade_date or "1970-01-01",
                 "LEGACY_IMPORT",
                 f"旧状态导入 — UNVERIFIED。权重 {weight:.4%}",
                 now_iso()),
            )
            imported_tx += 1

            # D-03（采纳方案 B）：LEGACY_IMPORT 不写 position_snapshots。
            # 旧状态仅落入不可变交易账（transactions）；真实持仓由
            # rebuild_from_transactions() 从交易账聚合生成，确保"持仓只是交易账的投影"。
            # 因此此处不再调用 assert_write_allowed("position_snapshots", "LEGACY_IMPORT")，
            # 且 LEGACY_IMPORT 已从 guards 的 position_snapshots 白名单移除（Fail-Closed）。

        # 3. Import ETF positions
        for symbol, weight in snapshot.etf_weights.items():
            tx_id = f"LEGACY_IMPORT_{account_id}_{symbol}_ETF"
            cursor = conn.execute("SELECT 1 FROM transactions WHERE transaction_id = ? AND account_id = ?",
                                  (tx_id, account_id))
            if cursor.fetchone() is not None:
                continue

            value = weight * (snapshot.portfolio_value or 0)
            conn.execute(
                """INSERT INTO transactions (transaction_id, account_id, symbol, strategy_id, side,
                   quantity, gross_amount, fee, execution_date, source, note, created_at)
                   VALUES (?, ?, ?, 'LEGACY', 'TRANSFER_IN', 0, ?, 0, ?, ?, ?, ?)""",
                (tx_id, account_id, symbol, value,
                 snapshot.last_trade_date or "1970-01-01",
                 "LEGACY_IMPORT",
                 f"旧状态导入 — UNVERIFIED。权重 {weight:.4%}",
                 now_iso()),
            )
            imported_tx += 1

            # D-03（采纳方案 B）：同基金分支，LEGACY_IMPORT 不写 position_snapshots，
            # 仅写交易账；持仓经由 rebuild_from_transactions() 聚合。

        # 4. Update audit
        # D-03（采纳方案 B）：positions_imported 恒为 0——旧状态只落入交易账，
        # 真实持仓由 rebuild_from_transactions() 从交易账聚合生成，不在此处直接写持仓表。
        write_audit_log(
            account_id, "LEGACY_STATE_IMPORTED", "account", account_id,
            new_value=json.dumps({
                "transactions_imported": imported_tx,
                "positions_imported": imported_pos,
                "positions_derived_via": "rebuild_from_transactions",
                "status": "UNVERIFIED",
                "source_files": snapshot.source_files,
            }, ensure_ascii=False),
            source="LEGACY_IMPORT", db_path=db,
        )

        conn.commit()
        status = "SUCCESS" if not errors else "PARTIAL"

    except Exception as exc:
        conn.rollback()
        errors.append(str(exc))
        status = "FAILED"

    return ImportResult(
        status=status,
        account_id=account_id,
        transactions_imported=imported_tx,
        positions_imported=imported_pos,
        legacy_cash=snapshot.cash_weight * (snapshot.portfolio_value or 0),
        warnings=warnings,
        errors=errors,
    )


def run_legacy_import(
    v42_path: Path | None = None,
    v451_etf_path: Path | None = None,
    v451_fund_path: Path | None = None,
    db_path: Path | str | None = None,
    account_id: str | None = None,
) -> ImportResult:
    """一站式导入所有旧状态文件。"""
    snapshots: list[LegacyAccountSnapshot] = []

    if v42_path and v42_path.exists():
        snapshots.append(parse_v42_portfolio_state(v42_path))
    if v451_etf_path and v451_etf_path.exists():
        snapshots.append(parse_v451_portfolio_state(v451_etf_path))
    if v451_fund_path and v451_fund_path.exists():
        snapshots.append(parse_v451_fund_portfolio_state(v451_fund_path))

    if not snapshots:
        return ImportResult("FAILED", "NONE", 0, 0, 0.0, [], ["未找到任何旧状态文件"])

    merged = merge_legacy_snapshots(snapshots)
    if account_id:
        merged.account_id = account_id
    return import_legacy_to_ledger(merged, db_path)
