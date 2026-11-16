"""V4.6 三本账物理隔离 —— 负向测试（P0 最高优先级）。

本测试组是"账户事实优先""决策/成交/持仓分离"两条铁律的守门人。
它从三个层面锁死不变量：

  1. 领域层：ActualPosition 只能以对账来源构造。
  2. 账本层：三本账写权限守卫（白名单 + Fail-Closed）。
  3. 物理层：三本账落在三个独立 db 文件，互不可写。

核心负向断言：
- 写目标组合永远不改变真实持仓。
- 决策提案 / 订单意图（含 SUBMITTED）永远不能直接变成真实持仓。
- 真实持仓只能由对账从不可变交易账聚合产生。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from quant.domain.enums import Environment, OrderSide, OrderStatus, TrustLevel, DataStatus
from quant.domain.forecasts import ForecastBundle
from quant.domain.portfolios import TargetPortfolio
from quant.domain.proposals import DecisionProposal
from quant.domain.positions import ActualPosition, ActualPositionProvenanceError
from quant.domain.orders import OrderIntent

from quant.ledgers import (
    MODEL_TARGETS_DB, DECISION_PLANS_DB, ACTUAL_POSITIONS_DB, LEDGER_PATHS,
)
from quant.ledgers.guards import (
    LedgerWriteViolation, assert_write_allowed, assert_reconciliation_write,
    allowed_sources, RECONCILIATION_SOURCE,
)
from quant.ledgers import model_targets as mt
from quant.ledgers import decision_plans as dp
from quant.ledgers import actual_positions as ap

from quant.accounts import ensure_schema as ensure_account_schema, get_connection as account_conn


# ── Fixtures：三本账各自独立的临时物理文件 ────────────────────────

@pytest.fixture
def ledger_dbs(tmp_path: Path) -> dict[str, Path]:
    return {
        "model_targets": tmp_path / "model_targets.db",
        "decision_plans": tmp_path / "decision_plans.db",
        "actual_positions": tmp_path / "actual_positions.db",
        "account": tmp_path / "account.db",
    }


@pytest.fixture
def account_id() -> str:
    return "TEST_ACCOUNT"


def _seed_account_with_buy(account_db: Path, account_id: str) -> None:
    """在交易账里放一笔真实 BUY 交易。"""
    ensure_account_schema(account_db)
    conn = account_conn(account_db)
    conn.execute(
        """INSERT OR IGNORE INTO accounts (account_id, account_mode, cash_balance)
           VALUES (?, 'MICRO', 10000)""",
        (account_id,),
    )
    conn.execute(
        """INSERT INTO transactions
           (transaction_id, account_id, symbol, strategy_id, side, quantity, price,
            gross_amount, fee, execution_date, source, created_at)
           VALUES ('TX-1', ?, '005658', 'CORE', 'BUY', 100, 1.5, 150.0, 0, '2026-07-20', 'TEST', datetime('now'))""",
        (account_id,),
    )
    conn.commit()


def _sample_target(account_id: str) -> TargetPortfolio:
    return TargetPortfolio(
        portfolio_id="TP-1", account_id=account_id, strategy_id="CORE",
        as_of="2026-07-21", environment=Environment.PAPER,
        target_weights={"005658": 0.6, "510300": 0.4}, cash_weight=0.0,
        reasons=("核心配置目标",),
    )


def _sample_order(account_id: str, status: OrderStatus = OrderStatus.DRAFT) -> OrderIntent:
    o = OrderIntent(
        order_id="ORD-1", account_id=account_id, strategy_id="CORE",
        symbol="005658", side=OrderSide.BUY, target_amount=6000.0,
        estimated_quantity=400, signal_date="2026-07-21",
        earliest_execution_date="2026-07-22", environment=Environment.PAPER,
    )
    # 推进到目标状态（仅用于测试构造）
    chain = {
        OrderStatus.RISK_APPROVED: [OrderStatus.RISK_APPROVED],
        OrderStatus.USER_APPROVED: [OrderStatus.RISK_APPROVED, OrderStatus.USER_APPROVED],
        OrderStatus.SUBMITTED: [OrderStatus.RISK_APPROVED, OrderStatus.USER_APPROVED, OrderStatus.SUBMITTED],
    }
    for step in chain.get(status, []):
        o = o.transition_to(step)
    return o


# =========================================================================
# 组 A：领域层护栏 —— ActualPosition 只能来自对账
# =========================================================================

class TestActualPositionProvenance:
    def test_actual_position_rejects_non_reconciliation_source(self):
        """以非对账来源构造真实持仓，领域层立即拒绝。"""
        with pytest.raises(ActualPositionProvenanceError):
            ActualPosition(
                account_id="A", symbol="005658", quantity=100, available_quantity=100,
                average_cost=1.5, market_value=150, as_of="2026-07-21",
                reconciliation_run_id="RUN-1", source="MODEL_TARGET",  # 非法
            )

    def test_actual_position_requires_run_id(self):
        """真实持仓必须携带非空 reconciliation_run_id。"""
        with pytest.raises(ActualPositionProvenanceError):
            ActualPosition(
                account_id="A", symbol="005658", quantity=100, available_quantity=100,
                average_cost=1.5, market_value=150, as_of="2026-07-21",
                reconciliation_run_id="   ", source=RECONCILIATION_SOURCE,  # run_id 空白
            )

    def test_valid_actual_position_from_reconciliation(self):
        """合法真实持仓：来源为对账 + 带 run_id。"""
        pos = ActualPosition(
            account_id="A", symbol="005658", quantity=100, available_quantity=100,
            average_cost=1.5, market_value=150, as_of="2026-07-21",
            reconciliation_run_id="RUN-1",
        )
        assert pos.source == RECONCILIATION_SOURCE
        assert pos.reconciliation_run_id == "RUN-1"


# =========================================================================
# 组 B：账本写权限守卫 —— 白名单 + Fail-Closed
# =========================================================================

class TestLedgerWriteGuards:
    def test_actual_positions_only_allows_reconciliation(self):
        """真实持仓账只接受 RECONCILIATION 来源。"""
        assert allowed_sources("actual_positions") == frozenset({RECONCILIATION_SOURCE})
        assert_write_allowed("actual_positions", RECONCILIATION_SOURCE)  # 不报错

    def test_decision_source_cannot_write_actual_positions(self):
        """决策引擎来源不能写真实持仓账。"""
        with pytest.raises(LedgerWriteViolation):
            assert_write_allowed("actual_positions", "DECISION_ENGINE")

    def test_model_source_cannot_write_actual_positions(self):
        """模型/策略来源不能写真实持仓账。"""
        for bad in ("MODEL", "STRATEGY", "RESEARCH", "MANUAL"):
            with pytest.raises(LedgerWriteViolation):
                assert_write_allowed("actual_positions", bad)

    def test_reconciliation_write_needs_run_id(self):
        """对账写入护栏：缺 run_id 直接拒绝。"""
        with pytest.raises(LedgerWriteViolation):
            assert_reconciliation_write(RECONCILIATION_SOURCE, None)
        with pytest.raises(LedgerWriteViolation):
            assert_reconciliation_write(RECONCILIATION_SOURCE, "")

    def test_unknown_ledger_rejected(self):
        with pytest.raises(LedgerWriteViolation):
            assert_write_allowed("secret_ledger", "MANUAL")


# =========================================================================
# 组 C：物理隔离 —— 写一本账不影响另一本账
# =========================================================================

class TestPhysicalIsolation:
    def test_three_ledgers_are_separate_files(self):
        """三本账默认落在三个不同物理文件。"""
        paths = {str(MODEL_TARGETS_DB), str(DECISION_PLANS_DB), str(ACTUAL_POSITIONS_DB)}
        assert len(paths) == 3, "三本账必须是三个独立文件"
        assert set(LEDGER_PATHS.keys()) == {"model_targets", "decision_plans", "actual_positions"}

    def test_target_weight_does_not_change_actual_position(self, ledger_dbs, account_id):
        """写目标组合永远不改变真实持仓账。"""
        # 写一个目标组合到模型目标账
        mt.write_target_portfolio(_sample_target(account_id), source="STRATEGY",
                                  db_path=ledger_dbs["model_targets"])
        # 真实持仓账此刻应为空
        positions = ap.get_latest_positions(account_id, db_path=ledger_dbs["actual_positions"])
        assert positions == [], "写目标组合不得在真实持仓账产生任何行"

    def test_decision_proposal_does_not_change_actual_position(self, ledger_dbs, account_id):
        """写决策提案（含 SUBMITTED 订单意图）永远不改变真实持仓账。"""
        proposal = DecisionProposal(
            proposal_id="DP-1", account_id=account_id, as_of="2026-07-21",
            environment=Environment.PAPER, source_portfolio_id="TP-1",
            order_intents=(_sample_order(account_id, OrderStatus.SUBMITTED),),
        )
        dp.write_decision_proposal(proposal, source="DECISION_ENGINE",
                                   db_path=ledger_dbs["decision_plans"])
        positions = ap.get_latest_positions(account_id, db_path=ledger_dbs["actual_positions"])
        assert positions == [], "写决策提案不得在真实持仓账产生任何行"

    def test_submitted_order_cannot_become_actual_position(self, ledger_dbs, account_id):
        """SUBMITTED 的订单意图不含真实持仓语义，无法直接落入真实持仓账。"""
        order = _sample_order(account_id, OrderStatus.SUBMITTED)
        assert order.status == OrderStatus.SUBMITTED
        # 订单意图对象本身没有任何"写入真实持仓"的能力
        assert not hasattr(order, "to_actual_position")
        assert not hasattr(order, "apply_to_position")


# =========================================================================
# 组 D：真实持仓的唯一官方入口 —— 从交易账对账聚合
# =========================================================================

class TestReconciliationIsOnlyPath:
    def test_only_reconciliation_populates_actual_positions(self, ledger_dbs, account_id):
        """真实持仓账在对账前为空，对账后才由交易账聚合产生。"""
        _seed_account_with_buy(ledger_dbs["account"], account_id)

        # 对账前：真实持仓账为空
        before = ap.get_latest_positions(account_id, db_path=ledger_dbs["actual_positions"])
        assert before == []

        # 执行对账：从交易账聚合
        produced = ap.rebuild_from_transactions(
            account_id=account_id,
            reconciliation_run_id="RUN-2026-07-21",
            as_of="2026-07-21",
            market_prices={"005658": 1.6},
            account_db_path=ledger_dbs["account"],
            db_path=ledger_dbs["actual_positions"],
        )
        assert len(produced) == 1
        assert produced[0].symbol == "005658"
        assert produced[0].reconciliation_run_id == "RUN-2026-07-21"

        # 对账后：真实持仓账可读出该持仓
        after = ap.get_latest_positions(account_id, db_path=ledger_dbs["actual_positions"])
        assert len(after) == 1
        assert after[0].symbol == "005658"
        assert abs(after[0].quantity - 100) < 1e-9
        assert after[0].source == RECONCILIATION_SOURCE

    def test_direct_write_with_bad_source_is_blocked(self, ledger_dbs, account_id):
        """即便绕过对账函数，直接调用 write_actual_position 也会被守卫拦截。"""
        good = ActualPosition(
            account_id=account_id, symbol="005658", quantity=100, available_quantity=100,
            average_cost=1.5, market_value=150, as_of="2026-07-21",
            reconciliation_run_id="RUN-X",
        )
        with pytest.raises(LedgerWriteViolation):
            ap.write_actual_position(good, source="DECISION_ENGINE",
                                     db_path=ledger_dbs["actual_positions"])


# =========================================================================
# 组 E：领域对象契约 —— 三者字段互不越界
# =========================================================================

class TestDomainSeparationOfConcerns:
    def test_target_portfolio_has_no_execution_or_position_fields(self, account_id):
        """目标组合只有权重，绝无成交/持仓字段。"""
        tp = _sample_target(account_id)
        for forbidden in ("quantity", "filled_quantity", "order_id", "average_cost", "market_value"):
            assert not hasattr(tp, forbidden), f"TargetPortfolio 不应有 {forbidden}"
        assert "005658" in tp.target_weights

    def test_decision_proposal_links_to_intents_not_positions(self, account_id):
        """决策提案链接订单意图，而非真实持仓。"""
        proposal = DecisionProposal(
            proposal_id="DP-2", account_id=account_id, as_of="2026-07-21",
            environment=Environment.PAPER,
            order_intents=(_sample_order(account_id, OrderStatus.USER_APPROVED),),
        )
        assert proposal.order_ids == ["ORD-1"]
        assert not hasattr(proposal, "actual_positions")
        assert proposal.approval_required is True

    def test_forecast_trust_levels_l0_to_l6(self):
        """ForecastBundle 可信级别覆盖 L0-L6，且生产门槛为 >= L4。"""
        assert [t.value for t in TrustLevel] == [0, 1, 2, 3, 4, 5, 6]
        low = ForecastBundle(bundle_id="F-0", model_id="KRONOS", model_version="1.0",
                             as_of="2026-07-21", horizon="SHORT",
                             predictions={"005658": 0.3}, trust_level=TrustLevel.L0_UNVERIFIED)
        assert low.is_production_eligible is False
        high = ForecastBundle(bundle_id="F-6", model_id="KRONOS", model_version="1.0",
                              as_of="2026-07-21", horizon="SHORT",
                              predictions={"005658": 0.3}, trust_level=TrustLevel.L6_PROVEN)
        assert high.is_production_eligible is True

    def test_forecast_default_is_frozen_and_untrusted(self):
        """ForecastBundle 默认冻结、默认最低可信级别（L0）。"""
        fb = ForecastBundle(bundle_id="F-D", model_id="KRONOS", model_version="1.0",
                            as_of="2026-07-21", horizon="SHORT")
        assert fb.is_frozen_model is True
        assert fb.trust_level == TrustLevel.L0_UNVERIFIED
        assert fb.is_production_eligible is False

    def test_unfrozen_model_not_production_eligible(self):
        """即便可信级别高，未冻结模型也不可进入生产。"""
        fb = ForecastBundle(bundle_id="F-U", model_id="KRONOS", model_version="1.0",
                            as_of="2026-07-21", horizon="SHORT",
                            trust_level=TrustLevel.L6_PROVEN, is_frozen_model=False)
        assert fb.is_production_eligible is False


# =========================================================================
# 组 F：三本账持久化往返
# =========================================================================

class TestLedgerRoundTrips:
    def test_target_portfolio_round_trip(self, ledger_dbs, account_id):
        original = _sample_target(account_id)
        mt.write_target_portfolio(original, source="STRATEGY", db_path=ledger_dbs["model_targets"])
        restored = mt.read_target_portfolio("TP-1", db_path=ledger_dbs["model_targets"])
        assert restored is not None
        assert restored.target_weights == original.target_weights
        assert restored.environment == Environment.PAPER

    def test_forecast_bundle_round_trip(self, ledger_dbs):
        fb = ForecastBundle(bundle_id="F-1", model_id="KRONOS", model_version="2.1",
                            as_of="2026-07-21", horizon="MEDIUM",
                            predictions={"005658": 0.42, "510300": -0.1},
                            trust_level=TrustLevel.L4_SHADOW, data_status=DataStatus.PASS)
        mt.write_forecast_bundle(fb, source="MODEL", db_path=ledger_dbs["model_targets"])
        restored = mt.read_forecast_bundle("F-1", db_path=ledger_dbs["model_targets"])
        assert restored is not None
        assert restored.trust_level == TrustLevel.L4_SHADOW
        assert abs(restored.predictions["005658"] - 0.42) < 1e-9

    def test_decision_proposal_round_trip(self, ledger_dbs, account_id):
        proposal = DecisionProposal(
            proposal_id="DP-3", account_id=account_id, as_of="2026-07-21",
            environment=Environment.PAPER, source_portfolio_id="TP-1",
            order_intents=(_sample_order(account_id, OrderStatus.USER_APPROVED),),
            reasons=("战术加仓",),
        )
        dp.write_decision_proposal(proposal, source="DECISION_ENGINE", db_path=ledger_dbs["decision_plans"])
        restored = dp.read_decision_proposal("DP-3", db_path=ledger_dbs["decision_plans"])
        assert restored is not None
        assert len(restored.order_intents) == 1
        assert restored.order_intents[0].status == OrderStatus.USER_APPROVED
        assert restored.source_portfolio_id == "TP-1"
