"""场外基金 11 态订单状态机测试（V4.6 阶段 3 / 审计建议修改顺序第 3 步）。

覆盖：
  * 11 态合法/非法转换、不可跳态；
  * 两阶段人工审批（计划批准 vs 成交确认令牌互斥）；
  * 未知价原则保全；
  * 赎回份额不得超过可赎回份额；
  * 过期信号不可提交；
  * 撤单/拒绝/过期无持仓影响；
  * 只有 CONFIRMED 改真实持仓；
  * 重复对账幂等；
  * 计划 vs 实际差异报告。
"""

from __future__ import annotations

import pytest

from quant.domain.enums import Environment
from quant.fund_orders import (
    FundApprovalError,
    FundApprovalRecord,
    FundApprovalToken,
    FundOrder,
    FundOrderSide,
    FundOrderStatus,
    FundOrderTransitionError,
    FundReconciler,
    FundReconciliationError,
    RedemptionExceedsAvailableError,
    UnknownNavViolationError,
    advance_to_waiting_nav,
    allowed_transitions,
    apply_execution_confirmation,
    apply_plan_approval,
    build_execution_difference,
    cancel,
    expire,
    flag_data_mismatch,
    record_nav,
    reject,
    submit_for_review,
    submit_manually,
    validate_transition,
)


# ── fixtures ────────────────────────────────────────────────────────

@pytest.fixture
def subscribe_order() -> FundOrder:
    return FundOrder(
        order_id="FND-SUB-001",
        account_id="MAIN",
        strategy_id="CORE_FUND",
        symbol="005658",
        side=FundOrderSide.SUBSCRIBE,
        target_amount=10000.0,
        signal_date="2026-07-21",
        expiration_date="2026-07-31",
        environment=Environment.LIVE,
    )


@pytest.fixture
def redeem_order() -> FundOrder:
    return FundOrder(
        order_id="FND-RED-001",
        account_id="MAIN",
        strategy_id="CORE_FUND",
        symbol="005658",
        side=FundOrderSide.REDEEM,
        target_units=5000.0,
        available_units=8000.0,
        signal_date="2026-07-21",
        expiration_date="2026-07-31",
        environment=Environment.LIVE,
    )


def _drive_to_confirm(order: FundOrder, *, confirmed_units: float, confirmed_amount: float) -> FundOrder:
    """把订单推进到 CONFIRMED（测试辅助）。"""
    o = submit_for_review(order)
    o = apply_plan_approval(o, FundApprovalRecord(order.order_id, FundApprovalToken.MANUAL_LIVE_PLAN_APPROVED))
    o = submit_manually(o, application_date="2026-07-22")
    o = advance_to_waiting_nav(o)
    o = record_nav(o, nav=1.25, nav_date="2026-07-23")
    o = apply_execution_confirmation(o, FundApprovalRecord(
        order.order_id, FundApprovalToken.MANUAL_EXECUTION_CONFIRMED,
        confirmed_nav=1.25, confirmed_units=confirmed_units,
        confirmed_amount=confirmed_amount, share_confirmation_date="2026-07-24",
    ))
    return o


# ── 组 A：11 态与合法转换 ────────────────────────────────────────────

class TestFundStateMachine:
    def test_eleven_states_exist(self):
        assert len(FundOrderStatus) == 11

    def test_full_subscribe_lifecycle_reaches_confirmed(self, subscribe_order):
        o = _drive_to_confirm(subscribe_order, confirmed_units=7936.5, confirmed_amount=9920.63)
        assert o.status == FundOrderStatus.CONFIRMED
        assert o.is_terminal

    def test_order_cannot_skip_required_states(self, subscribe_order):
        """不可跳态：DRAFT 不能直接到 CONFIRMED，SUBMITTED 不能直接到 CONFIRMED。"""
        with pytest.raises(FundOrderTransitionError):
            subscribe_order.transition_to(FundOrderStatus.CONFIRMED)
        # SUBMITTED_MANUALLY 不能跳过 WAITING_NAV / WAITING_SHARE_CONFIRMATION
        o = submit_for_review(subscribe_order)
        o = apply_plan_approval(o, FundApprovalRecord(o.order_id, FundApprovalToken.MANUAL_LIVE_PLAN_APPROVED))
        o = submit_manually(o, application_date="2026-07-22")
        with pytest.raises(FundOrderTransitionError):
            o.transition_to(FundOrderStatus.CONFIRMED)

    def test_waiting_nav_cannot_be_cancelled(self, subscribe_order):
        """净值截止后（WAITING_NAV）不再可撤单。"""
        o = submit_for_review(subscribe_order)
        o = apply_plan_approval(o, FundApprovalRecord(o.order_id, FundApprovalToken.MANUAL_LIVE_PLAN_APPROVED))
        o = submit_manually(o, application_date="2026-07-22")
        o = advance_to_waiting_nav(o)
        assert FundOrderStatus.CANCELLED not in allowed_transitions(o.status)
        with pytest.raises(FundOrderTransitionError):
            cancel(o, reason="too late")

    def test_terminal_states_have_no_transitions(self):
        for term in (FundOrderStatus.CONFIRMED, FundOrderStatus.REJECTED,
                     FundOrderStatus.CANCELLED, FundOrderStatus.EXPIRED):
            assert allowed_transitions(term) == frozenset()

    def test_data_mismatch_is_recoverable(self, subscribe_order):
        """DATA_MISMATCH 可修复回确认流程。"""
        o = submit_for_review(subscribe_order)
        o = apply_plan_approval(o, FundApprovalRecord(o.order_id, FundApprovalToken.MANUAL_LIVE_PLAN_APPROVED))
        o = submit_manually(o, application_date="2026-07-22")
        o = advance_to_waiting_nav(o)
        o = record_nav(o, nav=1.25, nav_date="2026-07-23")
        o = flag_data_mismatch(o, reason="平台份额与预期偏差过大")
        assert o.status == FundOrderStatus.DATA_MISMATCH
        # 人工核对后可回到 WAITING_SHARE_CONFIRMATION
        o2 = o.transition_to(FundOrderStatus.WAITING_SHARE_CONFIRMATION)
        assert o2.status == FundOrderStatus.WAITING_SHARE_CONFIRMATION


# ── 组 B：两阶段人工审批 ────────────────────────────────────────────

class TestTwoPhaseApproval:
    def test_live_order_requires_plan_approval(self, subscribe_order):
        """LIVE 基金订单必须先经计划批准才能到 APPROVED。"""
        o = submit_for_review(subscribe_order)
        assert o.status == FundOrderStatus.PENDING_REVIEW
        o = apply_plan_approval(o, FundApprovalRecord(o.order_id, FundApprovalToken.MANUAL_LIVE_PLAN_APPROVED))
        assert o.status == FundOrderStatus.APPROVED

    def test_plan_approval_is_not_execution_confirmation(self, subscribe_order):
        """计划批准令牌不能用于成交确认。"""
        o = submit_for_review(subscribe_order)
        o = apply_plan_approval(o, FundApprovalRecord(o.order_id, FundApprovalToken.MANUAL_LIVE_PLAN_APPROVED))
        o = submit_manually(o, application_date="2026-07-22")
        o = advance_to_waiting_nav(o)
        o = record_nav(o, nav=1.25, nav_date="2026-07-23")
        # 用计划批准令牌去做成交确认 → 拒绝
        with pytest.raises(FundApprovalError):
            apply_execution_confirmation(o, FundApprovalRecord(
                o.order_id, FundApprovalToken.MANUAL_LIVE_PLAN_APPROVED,
                confirmed_nav=1.25, confirmed_units=100.0, confirmed_amount=125.0))

    def test_execution_confirmation_token_cannot_approve_plan(self, subscribe_order):
        """成交确认令牌不能用于计划批准。"""
        o = submit_for_review(subscribe_order)
        with pytest.raises(FundApprovalError):
            apply_plan_approval(o, FundApprovalRecord(
                o.order_id, FundApprovalToken.MANUAL_EXECUTION_CONFIRMED))

    def test_execution_confirmation_requires_confirmed_units_and_amount(self, subscribe_order):
        """成交确认必须提供确认份额与金额。"""
        o = submit_for_review(subscribe_order)
        o = apply_plan_approval(o, FundApprovalRecord(o.order_id, FundApprovalToken.MANUAL_LIVE_PLAN_APPROVED))
        o = submit_manually(o, application_date="2026-07-22")
        o = advance_to_waiting_nav(o)
        o = record_nav(o, nav=1.25, nav_date="2026-07-23")
        with pytest.raises(FundApprovalError):
            apply_execution_confirmation(o, FundApprovalRecord(
                o.order_id, FundApprovalToken.MANUAL_EXECUTION_CONFIRMED,
                confirmed_nav=1.25, confirmed_units=None, confirmed_amount=None))


# ── 组 C：未知价原则 ────────────────────────────────────────────────

class TestUnknownNavPrinciple:
    def test_fund_unknown_price_is_preserved(self, subscribe_order):
        """未知价保全：确认前净值/份额/金额必须为 None，全程不假设。"""
        o = submit_for_review(subscribe_order)
        o = apply_plan_approval(o, FundApprovalRecord(o.order_id, FundApprovalToken.MANUAL_LIVE_PLAN_APPROVED))
        o = submit_manually(o, application_date="2026-07-22")
        assert o.confirmed_nav is None
        o = advance_to_waiting_nav(o)
        assert o.confirmed_nav is None and o.confirmed_units is None and o.confirmed_amount is None

    def test_cannot_construct_order_with_nav_in_unknown_stage(self):
        """在未知价阶段构造带确认净值的订单 → 被拒绝。"""
        with pytest.raises(UnknownNavViolationError):
            FundOrder(
                order_id="FND-BAD", account_id="MAIN", strategy_id="CORE_FUND",
                symbol="005658", side=FundOrderSide.SUBSCRIBE, target_amount=10000.0,
                signal_date="2026-07-21", status=FundOrderStatus.SUBMITTED_MANUALLY,
                confirmed_nav=1.25,  # 违反未知价
            )

    def test_nav_only_set_at_nav_confirmation(self, subscribe_order):
        """净值只能在 record_nav（WAITING_NAV → WAITING_SHARE_CONFIRMATION）时写入。"""
        o = submit_for_review(subscribe_order)
        o = apply_plan_approval(o, FundApprovalRecord(o.order_id, FundApprovalToken.MANUAL_LIVE_PLAN_APPROVED))
        o = submit_manually(o, application_date="2026-07-22")
        o = advance_to_waiting_nav(o)
        o = record_nav(o, nav=1.25, nav_date="2026-07-23")
        assert o.confirmed_nav == 1.25
        assert o.status == FundOrderStatus.WAITING_SHARE_CONFIRMATION


# ── 组 D：赎回护栏 ──────────────────────────────────────────────────

class TestRedemptionGuards:
    def test_fund_redemption_cannot_exceed_available_units(self):
        """赎回份额不得超过可赎回份额。"""
        with pytest.raises(RedemptionExceedsAvailableError):
            FundOrder(
                order_id="FND-RED-BAD", account_id="MAIN", strategy_id="CORE_FUND",
                symbol="005658", side=FundOrderSide.REDEEM,
                target_units=9000.0, available_units=8000.0,  # 超出
                signal_date="2026-07-21",
            )

    def test_valid_redemption_within_available_units(self, redeem_order):
        assert redeem_order.side == FundOrderSide.REDEEM
        assert redeem_order.target_units <= redeem_order.available_units

    def test_redemption_reconcile_reduces_units(self, redeem_order):
        o = _drive_to_confirm(redeem_order, confirmed_units=5000.0, confirmed_amount=6250.0)
        rec = FundReconciler()
        delta = rec.reconcile(o, reconciliation_run_id="RUN-R", as_of="2026-07-24")
        assert delta.units_delta == -5000.0   # 赎回减少份额
        assert delta.amount_delta == 6250.0    # 现金流入


# ── 组 E：过期与提交护栏 ────────────────────────────────────────────

class TestExpiryGuards:
    def test_expired_signal_cannot_be_submitted(self, subscribe_order):
        """过期信号不可提交。"""
        o = submit_for_review(subscribe_order)
        o = apply_plan_approval(o, FundApprovalRecord(o.order_id, FundApprovalToken.MANUAL_LIVE_PLAN_APPROVED))
        # 提交日晚于有效期
        with pytest.raises(FundApprovalError):
            submit_manually(o, application_date="2026-08-05")  # > 2026-07-31

    def test_expired_order_cannot_be_submitted(self, subscribe_order):
        o = submit_for_review(subscribe_order)
        o = apply_plan_approval(o, FundApprovalRecord(o.order_id, FundApprovalToken.MANUAL_LIVE_PLAN_APPROVED))
        o = expire(o)
        assert o.status == FundOrderStatus.EXPIRED
        with pytest.raises(FundApprovalError):
            submit_manually(o, application_date="2026-07-22")


# ── 组 F：持仓影响 / 对账 ────────────────────────────────────────────

class TestPositionEffectAndReconciliation:
    def test_draft_order_does_not_update_position(self, subscribe_order):
        assert subscribe_order.status == FundOrderStatus.DRAFT
        assert subscribe_order.produces_position_effect is False

    def test_submitted_order_does_not_update_position(self, subscribe_order):
        o = submit_for_review(subscribe_order)
        o = apply_plan_approval(o, FundApprovalRecord(o.order_id, FundApprovalToken.MANUAL_LIVE_PLAN_APPROVED))
        o = submit_manually(o, application_date="2026-07-22")
        assert o.produces_position_effect is False

    def test_waiting_confirmation_does_not_update_position(self, subscribe_order):
        o = submit_for_review(subscribe_order)
        o = apply_plan_approval(o, FundApprovalRecord(o.order_id, FundApprovalToken.MANUAL_LIVE_PLAN_APPROVED))
        o = submit_manually(o, application_date="2026-07-22")
        o = advance_to_waiting_nav(o)
        o = record_nav(o, nav=1.25, nav_date="2026-07-23")
        assert o.status == FundOrderStatus.WAITING_SHARE_CONFIRMATION
        assert o.produces_position_effect is False

    def test_confirmed_order_updates_position_once(self, subscribe_order):
        o = _drive_to_confirm(subscribe_order, confirmed_units=7936.5, confirmed_amount=9920.63)
        assert o.produces_position_effect is True
        rec = FundReconciler()
        delta = rec.reconcile(o, reconciliation_run_id="RUN-1", as_of="2026-07-24")
        assert delta is not None and delta.units_delta == 7936.5

    def test_duplicate_execution_import_is_idempotent(self, subscribe_order):
        """重复对账幂等：第二次返回 None，不产生二次持仓变更。"""
        o = _drive_to_confirm(subscribe_order, confirmed_units=7936.5, confirmed_amount=9920.63)
        rec = FundReconciler()
        first = rec.reconcile(o, reconciliation_run_id="RUN-1", as_of="2026-07-24")
        second = rec.reconcile(o, reconciliation_run_id="RUN-1", as_of="2026-07-24")
        assert first is not None
        assert second is None
        assert rec.is_reconciled(o.order_id)

    def test_non_confirmed_order_cannot_be_reconciled(self, subscribe_order):
        """非 CONFIRMED 订单对账被拒绝（护栏：只有确认成交改持仓）。"""
        o = submit_for_review(subscribe_order)
        o = apply_plan_approval(o, FundApprovalRecord(o.order_id, FundApprovalToken.MANUAL_LIVE_PLAN_APPROVED))
        o = submit_manually(o, application_date="2026-07-22")
        rec = FundReconciler()
        with pytest.raises(FundReconciliationError):
            rec.reconcile(o, reconciliation_run_id="RUN-1", as_of="2026-07-24")

    def test_cancelled_order_has_no_position_effect(self, subscribe_order):
        """撤单无持仓影响。"""
        o = submit_for_review(subscribe_order)
        o = apply_plan_approval(o, FundApprovalRecord(o.order_id, FundApprovalToken.MANUAL_LIVE_PLAN_APPROVED))
        o = submit_manually(o, application_date="2026-07-22")
        o = cancel(o, reason="用户撤销")
        assert o.status == FundOrderStatus.CANCELLED
        assert o.produces_position_effect is False
        rec = FundReconciler()
        with pytest.raises(FundReconciliationError):
            rec.reconcile(o, reconciliation_run_id="RUN-1", as_of="2026-07-24")

    def test_rejected_order_has_no_position_effect(self, subscribe_order):
        o = submit_for_review(subscribe_order)
        o = reject(o, reason="风控否决")
        assert o.status == FundOrderStatus.REJECTED
        assert o.produces_position_effect is False


# ── 组 G：差异报告 ──────────────────────────────────────────────────

class TestExecutionDifference:
    def test_execution_difference_is_reported(self, subscribe_order):
        """申购计划金额 vs 实际确认金额差异被报告。"""
        o = _drive_to_confirm(subscribe_order, confirmed_units=7936.5, confirmed_amount=9920.63)
        diff = build_execution_difference(o)
        assert diff.planned_amount == 10000.0
        assert diff.confirmed_amount == 9920.63
        assert diff.amount_diff == pytest.approx(-79.37)

    def test_difference_requires_confirmed_order(self, subscribe_order):
        with pytest.raises(FundReconciliationError):
            build_execution_difference(subscribe_order)


# ── 组 H：审计追踪 ──────────────────────────────────────────────────

class TestAuditTrail:
    def test_every_transition_is_audited(self, subscribe_order):
        o = _drive_to_confirm(subscribe_order, confirmed_units=7936.5, confirmed_amount=9920.63)
        # 6 次转换：DRAFT→PENDING_REVIEW→APPROVED→SUBMITTED_MANUALLY→WAITING_NAV
        #           →WAITING_SHARE_CONFIRMATION→CONFIRMED
        assert len(o.audit_trail) == 6
        assert o.audit_trail[0].from_status == FundOrderStatus.DRAFT
        assert o.audit_trail[-1].to_status == FundOrderStatus.CONFIRMED

    def test_validate_transition_rejects_illegal(self):
        with pytest.raises(FundOrderTransitionError):
            validate_transition(FundOrderStatus.DRAFT, FundOrderStatus.CONFIRMED)
        # 合法转换不抛
        validate_transition(FundOrderStatus.DRAFT, FundOrderStatus.PENDING_REVIEW)
