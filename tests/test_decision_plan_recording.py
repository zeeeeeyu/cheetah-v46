"""W2-3 / W2-4：决策计划账（第二本账）生产接线 + 订单链生产可达。

与 test_model_target_recording.py 对称：验证第二本账在生产链路真实写入、
「目标 → 决策」血缘可回溯、守卫白名单仍生效，以及 order_factory 首次
拥有生产调用方、permission_gate 在订单生成时真正 Fail-Closed。
"""

from __future__ import annotations

import inspect
import shutil
import tempfile
from pathlib import Path

import pytest

from quant.domain.assets import Asset
from quant.domain.enums import AssetType, CapitalPermission, Environment, OrderStatus
from quant.ledgers.decision_plans import read_decision_proposal, write_decision_proposal
from quant.ledgers.guards import LedgerWriteViolation
from quant.ledgers.recorder import (
    build_decision_proposal,
    record_daily_proposals,
)


@pytest.fixture
def temp_db():
    td = tempfile.mkdtemp(prefix="test_dp_")
    db = Path(td) / "decision_plans.db"
    yield db
    shutil.rmtree(td, ignore_errors=True)


def _paper_etf_registry() -> dict[str, Asset]:
    """确定性资产目录：510300 授权(PAPER)，用于 W2-4 订单生成测试。"""
    return {
        "510300": Asset(
            symbol="510300", name="测试ETF", asset_type=AssetType.ETF,
            execution_eligible=True, permission=CapitalPermission.PAPER,
        ),
    }


def test_proposal_links_to_model_target_lineage(temp_db):
    """source_portfolio_id 必须指回 W2-2 写入模型目标账的 portfolio_id。"""
    written = record_daily_proposals(
        as_of="2026-08-01", account_id="ACC_X",
        decisions={"CORE_FUND": {"004813": 0.6, "CASH": 0.4}},
        environment=Environment.PAPER, db_path=temp_db,
    )
    proposal = read_decision_proposal(written["CORE_FUND"], db_path=temp_db)
    assert proposal is not None
    assert proposal.source_portfolio_id == "CORE_FUND-PAPER-2026-08-01"


def test_decision_plan_written_in_production_path(temp_db):
    """record_daily_proposals 真实落账并可回读（第二本账通电）。"""
    written = record_daily_proposals(
        as_of="2026-08-01", account_id="ACC_X",
        decisions={"CORE_FUND": {"004813": 1.0}},
        environment=Environment.PAPER, db_path=temp_db,
    )
    assert len(written) == 1
    proposal = read_decision_proposal(written["CORE_FUND"], db_path=temp_db)
    assert proposal is not None
    assert proposal.environment == Environment.PAPER
    assert proposal.approval_required is True


def test_decision_plan_guard_rejects_non_whitelisted_source(temp_db):
    """decision_plans 守卫：非白名单来源（STRATEGY）必须被拒。"""
    proposal = build_decision_proposal(
        strategy_id="CORE_FUND", account_id="ACC_X", as_of="2026-08-01",
        environment=Environment.PAPER, target_weights={"004813": 1.0},
        source_portfolio_id="CORE_FUND-PAPER-2026-08-01",
    )
    with pytest.raises(LedgerWriteViolation):
        write_decision_proposal(proposal, source="STRATEGY", db_path=temp_db)


def test_proposal_has_no_execution_fields(temp_db):
    """DecisionProposal 不得携带成交/持仓字段（三本账隔离）。"""
    written = record_daily_proposals(
        as_of="2026-08-01", account_id="ACC_X",
        decisions={"CORE_FUND": {"004813": 1.0}},
        environment=Environment.PAPER, db_path=temp_db,
    )
    proposal = read_decision_proposal(written["CORE_FUND"], db_path=temp_db)
    for forbidden in ("quantity", "filled_quantity", "order_id", "actual_position", "market_value"):
        assert not hasattr(proposal, forbidden), f"DecisionProposal 混入 {forbidden}"


def test_record_daily_proposals_idempotent(temp_db):
    """同日同策略重跑覆盖而非堆积。"""
    record_daily_proposals(
        as_of="2026-08-01", account_id="ACC_X",
        decisions={"CORE_FUND": {"004813": 0.6, "CASH": 0.4}},
        environment=Environment.PAPER, db_path=temp_db,
    )
    record_daily_proposals(
        as_of="2026-08-01", account_id="ACC_X",
        decisions={"CORE_FUND": {"004813": 0.9}},
        environment=Environment.PAPER, db_path=temp_db,
    )
    proposal = read_decision_proposal("DP-CORE_FUND-PAPER-2026-08-01", db_path=temp_db)
    assert proposal is not None
    assert proposal.metadata["target_weights"] == {"004813": 0.9}


def test_record_skips_none_weights():
    """weights 为 None 的策略被跳过，不写空提案。"""
    td = tempfile.mkdtemp(prefix="test_dp_skip_")
    db = Path(td) / "decision_plans.db"
    try:
        written = record_daily_proposals(
            as_of="2026-08-01", account_id="ACC_X",
            decisions={"CORE_FUND": None, "TACTICAL_ETF": {"510300": 0.5}},
            environment=Environment.PAPER, db_path=db,
        )
        assert "CORE_FUND" not in written
        assert "TACTICAL_ETF" in written
    finally:
        shutil.rmtree(td, ignore_errors=True)


def test_generate_orders_produces_draft_intents(temp_db):
    """W2-4：order_factory 经本链路生成 DRAFT 订单意图。"""
    written = record_daily_proposals(
        as_of="2026-08-01", account_id="ACC_X",
        decisions={"TACTICAL_ETF": {"510300": 0.05}},
        environment=Environment.PAPER, generate_orders=True,
        portfolio_value=1_000_000.0, current_prices={"510300": 4.0},
        asset_registry=_paper_etf_registry(), db_path=temp_db,
    )
    proposal = read_decision_proposal(written["TACTICAL_ETF"], db_path=temp_db)
    assert len(proposal.order_intents) == 1
    assert proposal.order_intents[0].status == OrderStatus.DRAFT
    assert proposal.order_intents[0].environment == Environment.PAPER


def test_generate_orders_permission_gate_rejects_unknown(temp_db):
    """W2-4：权限闸门 Fail-Closed 拦截未知标的，记入 rejected_reasons。"""
    written = record_daily_proposals(
        as_of="2026-08-01", account_id="ACC_X",
        decisions={"TACTICAL_ETF": {"510300": 0.05, "999999": 0.05}},
        environment=Environment.PAPER, generate_orders=True,
        portfolio_value=1_000_000.0, current_prices={"510300": 4.0},
        asset_registry=_paper_etf_registry(), db_path=temp_db,
    )
    proposal = read_decision_proposal(written["TACTICAL_ETF"], db_path=temp_db)
    drafted = [o for o in proposal.order_intents if o.status == OrderStatus.DRAFT]
    assert len(drafted) == 1 and drafted[0].symbol == "510300"
    rejected = proposal.metadata.get("rejected_reasons", [])
    assert any("999999" in r for r in rejected)


def test_record_daily_proposals_with_orders_attaches_intents(temp_db):
    """generate_orders=True 时订单意图随提案落账（不提交、状态 DRAFT）。"""
    written = record_daily_proposals(
        as_of="2026-08-01", account_id="ACC_X",
        decisions={"CORE_FUND": {"004813": 0.5, "999999": 0.1}},
        environment=Environment.PAPER, generate_orders=True,
        portfolio_value=1_000_000.0,
        asset_registry={
            "004813": Asset(
                symbol="004813", name="测试基金", asset_type=AssetType.ACTIVE_FUND,
                execution_eligible=True, permission=CapitalPermission.PAPER,
            ),
        },
        db_path=temp_db,
    )
    proposal = read_decision_proposal(written["CORE_FUND"], db_path=temp_db)
    # 基金订单无需价格即可生成（未知价原则，estimated_quantity=0）
    assert len(proposal.order_intents) == 1
    assert proposal.order_intents[0].status == OrderStatus.DRAFT
    # 999999 被拒
    rejected = proposal.metadata.get("rejected_reasons", [])
    assert any("999999" in r for r in rejected)


def test_run_daily_wires_decision_plans():
    """run_daily.main 真正调用决策计划账记账器（W2-3 接线）。"""
    import app.run_daily as rd
    assert "_record_decision_plans" in inspect.getsource(rd.main)


def test_run_daily_wires_order_factory():
    """run_daily 真正激活订单生成路径（W2-4 使订单链生产可达）。

    注：run_daily 不直接调用 order_factory，而是通过
    ``record_daily_proposals(generate_orders=True)`` 激活其生产调用方。
    因此接线证明是「generate_orders=True 被生产路径置位」。
    """
    import app.run_daily as rd
    src = inspect.getsource(rd._record_decision_plans)
    assert "generate_orders=True" in src
