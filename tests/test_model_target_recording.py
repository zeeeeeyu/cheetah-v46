"""W2-2：模型目标账（第一本账）生产接线测试。

背景：`docs/audit/V46_PRODUCTION_WIRING_AUDIT.md` 发现 `model_targets` 与
`decision_plans` 两本账在生产中恒为空——三本账只有第三本通电。本轮补上第一本。

本文件同时守住两件事：
1. **接线**：`app/run_daily.py` 真的会调用记账器（防止日后被悄悄摘掉）。
2. **隔离不变量**：写模型目标账**绝不**触碰真实持仓账——这是三本账物理隔离的第一道墙。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from quant.domain.enums import Environment
from quant.ledgers.guards import LedgerWriteViolation
from quant.ledgers.model_targets import (
    list_target_portfolios,
    read_target_portfolio,
    write_target_portfolio,
)
from quant.ledgers.recorder import (
    build_target_portfolio,
    record_daily_decisions,
    record_target_portfolio,
)


@pytest.fixture
def targets_db(tmp_path: Path) -> Path:
    return tmp_path / "model_targets.db"


# ── 构造：CASH 必须被分离为 cash_weight ────────────────────────────

def test_cash_is_separated_into_cash_weight():
    tp = build_target_portfolio(
        strategy_id="CORE_FUND",
        account_id="ACC1",
        as_of="2026-08-01",
        target_weights={"005658": 0.6, "004813": 0.25, "CASH": 0.15},
    )
    assert tp.target_weights == {"005658": 0.6, "004813": 0.25}
    assert tp.cash_weight == pytest.approx(0.15)
    assert "CASH" not in tp.target_weights
    assert tp.total_weight == pytest.approx(1.0)


def test_target_portfolio_carries_no_execution_fields():
    """第一本账只记『想要什么』，不得混入成交字段。"""
    tp = build_target_portfolio(
        strategy_id="CORE_FUND", account_id="ACC1", as_of="2026-08-01",
        target_weights={"005658": 1.0},
    )
    for forbidden in ("quantity", "filled_quantity", "order_id", "filled_price", "status"):
        assert not hasattr(tp, forbidden), f"TargetPortfolio 不应有执行字段 {forbidden}"


# ── 落账与回读 ────────────────────────────────────────────────────

def test_record_and_read_back(targets_db: Path):
    pid = record_target_portfolio(
        strategy_id="CORE_FUND", account_id="ACC1", as_of="2026-08-01",
        target_weights={"005658": 0.7, "CASH": 0.3},
        environment=Environment.PAPER, db_path=targets_db,
    )
    assert pid == "CORE_FUND-PAPER-2026-08-01"

    got = read_target_portfolio(pid, db_path=targets_db)
    assert got is not None
    assert got.strategy_id == "CORE_FUND"
    assert got.target_weights == {"005658": 0.7}
    assert got.cash_weight == pytest.approx(0.3)
    assert got.environment is Environment.PAPER


def test_same_day_rerun_is_idempotent(targets_db: Path):
    """同日重跑覆盖而非堆积——否则账本会被重复运行污染。"""
    for weight in (0.5, 0.8):
        record_target_portfolio(
            strategy_id="CORE_FUND", account_id="ACC1", as_of="2026-08-01",
            target_weights={"005658": weight}, db_path=targets_db,
        )
    rows = list_target_portfolios("ACC1", db_path=targets_db)
    assert len(rows) == 1, "同日重跑产生了重复记录"
    assert rows[0].target_weights == {"005658": 0.8}, "重跑未覆盖为最新值"


def test_record_daily_decisions_skips_empty(targets_db: Path):
    written = record_daily_decisions(
        as_of="2026-08-01", account_id="ACC1",
        decisions={
            "CORE_FUND": {"005658": 1.0},
            "TACTICAL_ETF": {},      # 空权重不应落账
            "EVENT": None,           # None 同理
        },
        db_path=targets_db,
    )
    assert set(written) == {"CORE_FUND"}
    assert len(list_target_portfolios("ACC1", db_path=targets_db)) == 1


def test_two_strategies_recorded_separately(targets_db: Path):
    written = record_daily_decisions(
        as_of="2026-08-01", account_id="ACC1",
        decisions={"CORE_FUND": {"005658": 1.0}, "TACTICAL_ETF": {"510300": 1.0}},
        db_path=targets_db,
    )
    assert len(written) == 2
    assert len(list_target_portfolios("ACC1", db_path=targets_db)) == 2


# ── 守卫：非白名单来源必须被拒 ─────────────────────────────────────

def test_non_whitelisted_source_is_rejected(targets_db: Path):
    tp = build_target_portfolio(
        strategy_id="CORE_FUND", account_id="ACC1", as_of="2026-08-01",
        target_weights={"005658": 1.0},
    )
    with pytest.raises(LedgerWriteViolation):
        write_target_portfolio(tp, source="EXECUTION_SERVICE", db_path=targets_db)
    with pytest.raises(LedgerWriteViolation):
        write_target_portfolio(tp, source="RECONCILIATION", db_path=targets_db)


# ── 隔离不变量：写第一本账绝不触碰真实持仓 ──────────────────────────

def test_writing_target_never_touches_actual_positions(targets_db: Path, tmp_path: Path):
    """三本账第一道墙：模型『想要』不等于账户『持有』。"""
    from quant.accounts import ensure_schema as ensure_account_schema, get_connection

    account_db = tmp_path / "account.db"
    ensure_account_schema(account_db)
    conn = get_connection(account_db)
    conn.execute(
        "INSERT OR IGNORE INTO accounts (account_id, account_mode, cash_balance) VALUES ('ACC1','MICRO',10000)"
    )
    conn.commit()
    before = conn.execute("SELECT COUNT(*) FROM position_snapshots").fetchone()[0]

    record_target_portfolio(
        strategy_id="CORE_FUND", account_id="ACC1", as_of="2026-08-01",
        target_weights={"005658": 1.0}, db_path=targets_db,
    )

    after = conn.execute("SELECT COUNT(*) FROM position_snapshots").fetchone()[0]
    assert after == before == 0, "写模型目标账竟改变了真实持仓——三本账隔离被击穿"


# ── 生产接线：run_daily 必须真的调用记账器 ──────────────────────────

def test_run_daily_wires_the_recorder():
    """接线证明：防止记账器日后被悄悄摘除而账本重新变空。"""
    import inspect

    import app.run_daily as rd

    assert hasattr(rd, "_record_model_targets"), "run_daily 缺少模型目标账记账函数"
    main_src = inspect.getsource(rd.main)
    assert "_record_model_targets" in main_src, "记账器未在 main() 中被调用"


def test_recorder_extracts_weights_from_payload(monkeypatch):
    """记账器应从两条策略链路的输出中各取一份目标权重。"""
    import app.run_daily as rd

    captured: dict = {}

    def _fake(**kwargs):
        captured.update(kwargs)
        return {k: f"{k}-PID" for k in kwargs["decisions"]}

    monkeypatch.setattr(rd, "record_daily_decisions", _fake)

    payload = {
        "core_fund": {"target_weights": {"005658": 1.0}},
        "tactical_etf": {"target_weights": {"510300": 1.0}},
    }
    rd._record_model_targets(payload, "2026-08-01")

    assert set(captured["decisions"]) == {"CORE_FUND", "TACTICAL_ETF"}
    assert payload["model_targets_recorded"] == {
        "CORE_FUND": "CORE_FUND-PID", "TACTICAL_ETF": "TACTICAL_ETF-PID",
    }


def test_recorder_failure_does_not_break_daily_run(monkeypatch):
    """记账失败必须告警但不中断分析主流程（与运行快照一致的 best-effort 约定）。"""
    import app.run_daily as rd

    def _boom(**_kwargs):
        raise RuntimeError("ledger down")

    monkeypatch.setattr(rd, "record_daily_decisions", _boom)
    payload = {"core_fund": {"target_weights": {"005658": 1.0}}, "tactical_etf": None}

    rd._record_model_targets(payload, "2026-08-01")  # 不应抛出

    assert "error" in payload["model_targets_recorded"]
    assert "ledger down" in payload["model_targets_recorded"]["error"]
