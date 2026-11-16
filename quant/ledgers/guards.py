"""V4.6 三本账写权限守卫。

每本账只接受"合法来源"的写入。守卫在写入前拦截一切越权写入，
从工程上杜绝"把目标/决策/订单意图当作真实持仓写进去"这类致命错误。

设计原则：
- 白名单制：未在白名单内的来源一律拒绝（Fail-Closed）。
- 真实持仓账最严格：只允许 RECONCILIATION，且必须携带对账运行键。
- 守卫抛出 LedgerWriteViolation，调用方不得吞掉该异常。
"""

from __future__ import annotations

# ── 各账本允许的写入来源白名单 ────────────────────────────────────

RECONCILIATION_SOURCE = "RECONCILIATION"

_ALLOWED_SOURCES: dict[str, frozenset[str]] = {
    # 模型目标账：策略、模型、研究、人工均可提出目标
    "model_targets": frozenset({"STRATEGY", "MODEL", "RESEARCH", "MANUAL"}),
    # 决策计划账：决策引擎、冲突仲裁、风险否决、人工审批可写
    "decision_plans": frozenset({"DECISION_ENGINE", "CONFLICT_RESOLVER", "RISK_VETO", "MANUAL"}),
    # 真实持仓账（V4.6 独立账本）：只允许对账，别无他途
    "actual_positions": frozenset({RECONCILIATION_SOURCE}),
    # 生产 position_snapshots 表（遗留表，但仍是生产持仓的写入目标）：
    # GAP-4 修复——此前多条直接 SQL INSERT 绕过了守卫。现在统一收编。
    # RECONCILIATION: 执行服务对账写入
    # USER_RECONCILIATION: 人工对账
    # 注意（D-03 采纳方案 B）：LEGACY_IMPORT 不再写 position_snapshots。旧状态只写
    # transactions（不可变交易账），真实持仓由 rebuild_from_transactions() 聚合生成，
    # 因此 LEGACY_IMPORT 已从本白名单移除——任何试图以 LEGACY_IMPORT 写本表的代码都会
    # 被 Fail-Closed 拒绝，从工程上杜绝"未对账迁移"混入生产持仓。
    "position_snapshots": frozenset({RECONCILIATION_SOURCE, "USER_RECONCILIATION"}),
    # 基金执行状态（申购/赎回状态快照）：
    # GAP-4 修复——save_fund_state() 此前直接落盘 JSON，无来源校验。
    # 只允许执行服务写入，人工不可直接修改。
    "fund_execution_state": frozenset({"EXECUTION_SERVICE", "MANUAL"}),
}


class LedgerWriteViolation(RuntimeError):
    """越权写入账本时抛出。这是必须让其冒泡的严重错误。"""


def assert_write_allowed(ledger: str, source: str) -> None:
    """校验 source 是否有权写入 ledger，否则抛出 LedgerWriteViolation。"""
    allowed = _ALLOWED_SOURCES.get(ledger)
    if allowed is None:
        raise LedgerWriteViolation(f"未知账本：{ledger!r}，拒绝写入。")
    if source not in allowed:
        raise LedgerWriteViolation(
            f"账本 {ledger!r} 拒绝来源 {source!r} 的写入。"
            f"合法来源仅：{sorted(allowed)}。"
        )


def assert_reconciliation_write(source: str, reconciliation_run_id: str | None) -> None:
    """真实持仓账专用护栏：来源必须为 RECONCILIATION 且必须带对账运行键。"""
    assert_write_allowed("actual_positions", source)
    if not reconciliation_run_id or not str(reconciliation_run_id).strip():
        raise LedgerWriteViolation(
            "真实持仓写入缺少 reconciliation_run_id：无法证明来自一次对账，拒绝写入。"
        )


def allowed_sources(ledger: str) -> frozenset[str]:
    """返回某账本的合法来源集合（用于测试与自省）。"""
    return _ALLOWED_SOURCES.get(ledger, frozenset())
