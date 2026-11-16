"""D-02 / Wave 1：双轨语义边界（position_snapshots=生产表，actual_positions=审计账）。

D-02 决策（采纳方案 A）：以 position_snapshots 为唯一生产持仓表，actual_positions 降级为
审计快照（严格守卫，只接受 RECONCILIATION）。本测试锁定该边界，确保：
- 生产持仓写入只能来自对账（RECONCILIATION / USER_RECONCILIATION）；
- actual_positions 仍是最严格账本；
- LEGACY_IMPORT 已被移除出生产持仓白名单（与 D-03 一致）。
"""

from __future__ import annotations

import pytest

from quant.ledgers.guards import (
    LedgerWriteViolation,
    RECONCILIATION_SOURCE,
    allowed_sources,
    assert_write_allowed,
)


def test_position_snapshots_accepts_production_sources():
    assert_write_allowed("position_snapshots", "RECONCILIATION")
    assert_write_allowed("position_snapshots", "USER_RECONCILIATION")


def test_position_snapshots_rejects_legacy_import():
    # D-03（采纳方案 B）后，旧状态迁移不得直接写生产持仓表
    with pytest.raises(LedgerWriteViolation):
        assert_write_allowed("position_snapshots", "LEGACY_IMPORT")
    assert "LEGACY_IMPORT" not in allowed_sources("position_snapshots")


def test_actual_positions_strict_audit_only():
    # 实际持仓账：唯一合法来源 RECONCILIATION（须带 run_id，见 assert_reconciliation_write）
    assert allowed_sources("actual_positions") == frozenset({RECONCILIATION_SOURCE})
    with pytest.raises(LedgerWriteViolation):
        assert_write_allowed("actual_positions", "LEGACY_IMPORT")
    with pytest.raises(LedgerWriteViolation):
        assert_write_allowed("actual_positions", "USER_RECONCILIATION")
