"""D-03 落地（采纳方案 B）：LEGACY_IMPORT 物理隔离 + 持仓由重建聚合生成。

验证：
1. import_legacy_to_ledger 只写 transactions，不写 position_snapshots（物理隔离）。
2. aggregate_positions 能把 TRANSFER_IN（零份额、金额在 gross_amount）重建成价值加权持仓。
3. rebuild_from_transactions 能从交易账聚合出真实持仓到 actual_positions 账本。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from quant.accounts.legacy_import import (
    ImportResult,
    LegacyAccountSnapshot,
    import_legacy_to_ledger,
)
from quant.accounts.position_service import aggregate_positions
from quant.ledgers.actual_positions import rebuild_from_transactions


def _snapshot(portfolio_value: float = 100_000.0) -> LegacyAccountSnapshot:
    return LegacyAccountSnapshot(
        account_id="LEGACY_TEST",
        fund_weights={"600000": 0.6},
        etf_weights={"510300": 0.4},
        cash_weight=0.0,
        last_trade_date="2024-01-01",
        portfolio_value=portfolio_value,
        source_files=["dummy.json"],
    )


def _count_rows(db_path: Path, table: str) -> int:
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def test_import_writes_transactions_not_positions(tmp_path: Path):
    db = tmp_path / "accounts.db"
    result = import_legacy_to_ledger(_snapshot(), db_path=db)

    assert isinstance(result, ImportResult)
    # 写了两条交易（基金 + ETF），但 position_snapshots 必须为 0 行
    assert result.transactions_imported == 2
    assert result.positions_imported == 0
    assert _count_rows(db, "transactions") == 2
    assert _count_rows(db, "position_snapshots") == 0


def test_aggregate_reconstructs_transfer_in(tmp_path: Path):
    db = tmp_path / "accounts.db"
    import_legacy_to_ledger(_snapshot(100_000.0), db_path=db)

    positions = aggregate_positions("LEGACY_TEST", db_path=db)
    by_symbol = {p.symbol: p for p in positions}

    # TRANSFER_IN（gross_amount=价值）被重建成价值单位持仓：数量==价值，价格基准 1.0
    assert "600000" in by_symbol
    assert "510300" in by_symbol
    assert abs(by_symbol["600000"].total_quantity - 60_000.0) < 1e-6
    assert abs(by_symbol["510300"].total_quantity - 40_000.0) < 1e-6
    assert abs(by_symbol["600000"].market_value - 60_000.0) < 1e-6


def test_rebuild_from_transactions_populates_actual_positions(tmp_path: Path):
    account_db = tmp_path / "accounts.db"
    actual_db = tmp_path / "actual.db"
    import_legacy_to_ledger(_snapshot(100_000.0), db_path=account_db)

    positions = rebuild_from_transactions(
        account_id="LEGACY_TEST",
        reconciliation_run_id="RUN_D03_TEST",
        as_of="2024-01-01",
        account_db_path=account_db,
        db_path=actual_db,
    )
    by_symbol = {p.symbol: p for p in positions}
    assert "600000" in by_symbol
    assert "510300" in by_symbol
    # actual_positions 账本只接受 RECONCILIATION 来源（守卫已验证）
    assert by_symbol["600000"].source == "RECONCILIATION"
    assert by_symbol["600000"].reconciliation_run_id == "RUN_D03_TEST"
