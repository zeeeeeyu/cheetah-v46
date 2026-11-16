"""GAP-4 台账白名单绕过修复 —— 专项测试。

验证所有 position_snapshots 和 fund_execution_state 的写入路径
都经过 quant.ledgers.guards 的白名单校验，不存在绕过。
"""

from __future__ import annotations

import inspect
import json
from unittest.mock import patch

import pytest

from quant.ledgers.guards import (
    LedgerWriteViolation,
    RECONCILIATION_SOURCE,
    _ALLOWED_SOURCES,
    allowed_sources,
    assert_write_allowed,
)


# ── 白名单完整性 ──────────────────────────────────────────────


class TestWhitelistRegistry:
    """白名单注册的完整性。"""

    def test_position_snapshots_registered(self):
        assert "position_snapshots" in _ALLOWED_SOURCES

    def test_position_snapshots_allowed_sources(self):
        # D-03（采纳方案 B）：LEGACY_IMPORT 已从白名单移除——旧状态只写交易账，不写持仓表
        assert allowed_sources("position_snapshots") == frozenset({
            RECONCILIATION_SOURCE, "USER_RECONCILIATION",
        })

    def test_fund_execution_state_registered(self):
        assert "fund_execution_state" in _ALLOWED_SOURCES

    def test_fund_execution_state_allowed_sources(self):
        assert allowed_sources("fund_execution_state") == frozenset({
            "EXECUTION_SERVICE", "MANUAL",
        })

    def test_actual_positions_still_strict(self):
        """回归保护：actual_positions 仍只接受 RECONCILIATION。"""
        assert allowed_sources("actual_positions") == frozenset({RECONCILIATION_SOURCE})


# ── 守卫拦截 ──────────────────────────────────────────────────


class TestGuardEnforcement:
    """assert_write_allowed 对每个账本的拦截行为。"""

    @pytest.mark.parametrize("ledger,source", [
        ("position_snapshots", "HACK"),
        ("position_snapshots", ""),
        ("position_snapshots", "MANUAL"),
        ("position_snapshots", "STRATEGY"),
        ("fund_execution_state", "HACK"),
        ("fund_execution_state", "RECONCILIATION"),
        ("fund_execution_state", ""),
        ("actual_positions", "USER_RECONCILIATION"),
        ("actual_positions", "LEGACY_IMPORT"),
        ("actual_positions", "MANUAL"),
    ])
    def test_illegal_source_rejected(self, ledger, source):
        with pytest.raises(LedgerWriteViolation):
            assert_write_allowed(ledger, source)

    @pytest.mark.parametrize("ledger,source", [
        ("position_snapshots", "RECONCILIATION"),
        ("position_snapshots", "USER_RECONCILIATION"),
        ("fund_execution_state", "EXECUTION_SERVICE"),
        ("fund_execution_state", "MANUAL"),
        ("actual_positions", "RECONCILIATION"),
        ("model_targets", "STRATEGY"),
        ("decision_plans", "DECISION_ENGINE"),
    ])
    def test_legal_source_accepted(self, ledger, source):
        assert_write_allowed(ledger, source) is None

    def test_unknown_ledger_rejected(self):
        with pytest.raises(LedgerWriteViolation, match="未知账本"):
            assert_write_allowed("nonexistent_ledger", "whatever")


# ── save_fund_state 守卫 ──────────────────────────────────────


class TestSaveFundStateGuard:
    """save_fund_state 的白名单校验。"""

    def test_default_source_accepted(self, tmp_path):
        from quant.fund_execution import save_fund_state
        with patch("quant.fund_execution.project_path", return_value=tmp_path / "state.json"):
            path = save_fund_state({"test": True})
            assert json.loads(path.read_text(encoding="utf-8")) == {"test": True}

    def test_manual_source_accepted(self, tmp_path):
        from quant.fund_execution import save_fund_state
        with patch("quant.fund_execution.project_path", return_value=tmp_path / "state.json"):
            path = save_fund_state({"test": True}, source="MANUAL")
            assert path.exists()

    def test_illegal_source_rejected(self, tmp_path):
        from quant.fund_execution import save_fund_state
        with patch("quant.fund_execution.project_path", return_value=tmp_path / "state.json"):
            with pytest.raises(LedgerWriteViolation):
                save_fund_state({"test": True}, source="HACK")

    def test_no_bypass_params(self):
        from quant.fund_execution import save_fund_state
        sig = inspect.signature(save_fund_state)
        forbidden = {"bypass", "skip", "enforce", "force", "unsafe", "no_guard"}
        actual = set(sig.parameters.keys())
        assert not (actual & forbidden), f"存在绕过参数: {actual & forbidden}"


# ── ReconciliationService 守卫 ────────────────────────────────


class TestReconciliationServiceGuard:
    """ReconciliationService.write_to_ledger 的白名单校验。"""

    def test_write_to_ledger_calls_guard(self):
        """write_to_ledger 在写 position_snapshots 前调用了 assert_write_allowed。"""
        from quant.exe.execution_service import ReconciliationService
        source = inspect.getsource(ReconciliationService.write_to_ledger)
        assert "assert_write_allowed" in source
        assert "position_snapshots" in source

    def test_no_bypass_params_in_write_to_ledger(self):
        from quant.exe.execution_service import ReconciliationService
        sig = inspect.signature(ReconciliationService.write_to_ledger)
        forbidden = {"bypass", "skip", "enforce", "force", "unsafe", "no_guard"}
        actual = set(sig.parameters.keys())
        assert not (actual & forbidden)


# ── reconcile_account 守卫 ────────────────────────────────────


class TestReconcileAccountGuard:
    """reconcile_account 的白名单校验。"""

    def test_reconcile_calls_guard(self):
        import app.reconcile_account as mod
        source = inspect.getsource(mod)
        assert "assert_write_allowed" in source
        assert "position_snapshots" in source
        assert "USER_RECONCILIATION" in source


# ── legacy_import 守卫 ────────────────────────────────────────


class TestLegacyImportGuard:
    """legacy_import 的物理隔离（D-03 采纳方案 B）。

    旧状态只写不可变交易账（transactions），**不写** position_snapshots；
    真实持仓由 rebuild_from_transactions() 聚合生成。
    """

    def test_legacy_import_no_position_snapshots_write(self):
        """AST 级校验：模块内不存在任何以 position_snapshots 为目标的写入调用。

        采用 AST 而非纯文本匹配，避免注释/文档字符串中的说明性文字造成误判，
        同时确保真实的调用节点被严格拦截。
        """
        import ast

        from quant.accounts import legacy_import

        tree = ast.parse(inspect.getsource(legacy_import))

        # 1. 不得存在 assert_write_allowed("position_snapshots", ...) 的实际调用
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            fname = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
            if fname != "assert_write_allowed":
                continue
            if node.args and isinstance(node.args[0], ast.Constant):
                assert node.args[0].value != "position_snapshots", (
                    "legacy_import 不得再以任何身份申请写入 position_snapshots（D-03 方案 B）"
                )

        # 2. 不得存在写 position_snapshots 的 SQL 字符串字面量（注释不算）
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                sql = node.value.upper()
                assert not ("INSERT INTO POSITION_SNAPSHOTS" in sql
                            or "UPDATE POSITION_SNAPSHOTS" in sql), (
                    "legacy_import 不得包含写入 position_snapshots 的 SQL"
                )

    def test_legacy_import_writes_transactions(self):
        from quant.accounts import legacy_import
        source = inspect.getsource(legacy_import)
        assert "INSERT INTO transactions" in source
        assert "'TRANSFER_IN'" in source

    def test_legacy_import_documents_rebuild(self):
        from quant.accounts import legacy_import
        source = inspect.getsource(legacy_import)
        assert "rebuild_from_transactions" in source


# ── 无绕过参数参数化扫描 ─────────────────────────────────────


FORBIDDEN_PARAMS = frozenset({"bypass", "skip", "enforce", "force", "unsafe", "no_guard"})


def _get_params(obj) -> set[str]:
    return set(inspect.signature(obj).parameters.keys())


class TestNoBypassParams:
    """所有被修函数签名中不存在绕过类关键字参数。"""

    def test_guards_assert_write_allowed(self):
        from quant.ledgers.guards import assert_write_allowed
        assert not (_get_params(assert_write_allowed) & FORBIDDEN_PARAMS)

    def test_guards_assert_reconciliation_write(self):
        from quant.ledgers.guards import assert_reconciliation_write
        assert not (_get_params(assert_reconciliation_write) & FORBIDDEN_PARAMS)

    def test_save_fund_state(self):
        from quant.fund_execution import save_fund_state
        assert not (_get_params(save_fund_state) & FORBIDDEN_PARAMS)

    def test_reconciliation_service_write_to_ledger(self):
        from quant.exe.execution_service import ReconciliationService
        assert not (_get_params(ReconciliationService.write_to_ledger) & FORBIDDEN_PARAMS)
