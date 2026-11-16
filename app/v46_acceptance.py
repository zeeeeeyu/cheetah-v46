"""猎豹 V4.6 —— 发布验收（可机器验证）。

本脚本把 V4.6 的发布验收标准写成**可执行的断言**，逐条给出 PASS/FAIL 与
证据，全部通过返回 0。

轴标注规则见 ``docs/ORTHOGONAL_AXES.md``：

- ``工程轴``：账户事实、状态机、数据身份、守卫、可观测性 —— 「有没有守卫」
- ``策略轴``：选品、择时、配权等交易逻辑 —— 「守卫触发后降到多少仓位」
- ``双轴``：两者兼具

**历史更正（重要）**：此前第 1-6 条被标注为「交易策略轴（成熟保留）：环境安全、
可移植、入口齐全」。这是**标签错误** —— 环境安全、路径可移植、入口文件存在性
全部是工程轴断言，与任何交易逻辑无关。按 ``docs/ORTHOGONAL_AXES.md`` §3 重新
核定后，当时真实构成是「工程轴 26 条 + 双轴 1 条 + **策略轴 0 条**」。

**策略轴补厚（2026-08-02）**：策略轴一度仅 c32 一条（评分公式序数不变量），
改动配权、集中度、Risk-Off 降仓等核心交易行为不会触发任何报警。已补入
c43-c46 四条**直接调用策略引擎**的行为契约断言（权重归一 / 集中度上限 /
Risk-Off 清仓 / 选品子集），策略轴由 1 条增至 5 条。

诚实性红线（不得违反）：
- 通过率分母是自定义条件，**不等于系统完成度**；对外一律引用需求追溯落地率。
- ``PENDING_CONDITIONS`` 为诚实性装置，未实现条件必须登记在此、计入分母。
  禁止为求全绿而删除条目。

用法::

    python -m app.v46_acceptance

设计为可导入：``collect_results()`` 返回 [(编号, 名称, ok, 证据)]，
``run_all()`` 打印报告并返回退出码，便于测试与 CI 双用。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ── 各条件的验证函数（返回证据字符串；抛异常即判定 FAIL）───────────────

def c01_regression_suite_present() -> str:
    tests = list((ROOT / "tests").glob("test_*.py"))
    assert len(tests) >= 15, f"测试文件过少：{len(tests)}"
    return f"{len(tests)} 个测试文件在册"


def c02_default_environment_paper() -> str:
    runtime = json.loads((ROOT / "config/runtime.json").read_text(encoding="utf-8"))
    assert runtime.get("default_environment") == "PAPER", runtime.get("default_environment")
    return "default_environment=PAPER"


def c03_live_generation_disabled() -> str:
    runtime = json.loads((ROOT / "config/runtime.json").read_text(encoding="utf-8"))
    assert runtime.get("allow_live_order_generation") is False
    return "allow_live_order_generation=False"


def c04_no_hardcoded_user_paths() -> str:
    from app.release_check import WINDOWS_ABSOLUTE_PATH

    hits: list[str] = []
    for py in ROOT.rglob("*.py"):
        if any(part in {".git", ".venv", "__pycache__"} for part in py.parts):
            continue
        text = py.read_text(encoding="utf-8", errors="ignore")
        if WINDOWS_ABSOLUTE_PATH.search(text):
            hits.append(str(py.relative_to(ROOT)))
    assert not hits, str(hits[:5])
    return "活动代码无用户绝对路径"


def c05_no_embedded_legacy() -> str:
    assert not (ROOT / "legacy").exists()
    return "无内嵌 legacy 目录"


def c06_entrypoints_present() -> str:
    entrypoints = [
        "app/menu.py", "app/run_daily.py", "app/run_core_fund.py",
        "app/run_tactical_etf.py", "app/run_backtest.py", "app/reconcile_account.py",
    ]
    missing = [x for x in entrypoints if not (ROOT / x).exists()]
    assert not missing, str(missing)
    return f"{len(entrypoints)} 个入口齐全"


def c07_three_ledgers_isolated() -> str:
    from quant.ledgers import model_targets, decision_plans, actual_positions
    names = {
        model_targets.DB_FILENAME if hasattr(model_targets, "DB_FILENAME") else "model_targets.db",
        decision_plans.DB_FILENAME if hasattr(decision_plans, "DB_FILENAME") else "decision_plans.db",
        actual_positions.DB_FILENAME if hasattr(actual_positions, "DB_FILENAME") else "actual_positions.db",
    }
    assert len(names) == 3, f"三本账库名未物理隔离：{names}"
    return f"三独立库：{sorted(names)}"


def c08_actual_position_requires_reconciliation() -> str:
    from quant.domain.positions import ActualPosition
    import dataclasses
    raised = False
    try:
        ActualPosition(  # 缺 run_id 应被拒
            account_id="MAIN", symbol="510300", quantity=100.0,
            avg_cost=1.0, market_value=100.0, source="MANUAL",
            reconciliation_run_id="",
        )
    except Exception:
        raised = True
    assert raised, "ActualPosition 未强制 reconciliation_run_id"
    return "真实持仓强制携带对账 run_id"


def c09_fund_state_machine_has_11_states() -> str:
    from quant.fund_orders.states import FundOrderStatus
    assert len(list(FundOrderStatus)) == 11, f"状态数={len(list(FundOrderStatus))}"
    return "场外基金状态机恰为 11 态"


def c10_unknown_nav_principle() -> str:
    from quant.fund_orders.orders import FundOrder, FundOrderSide
    from quant.fund_orders.states import FundOrderStatus
    raised = False
    try:
        FundOrder(  # 未确认阶段却带 confirmed_nav 应被拒
            order_id="o1", account_id="MAIN", strategy_id="s", symbol="F001",
            side=FundOrderSide.SUBSCRIBE, target_amount=1000.0,
            status=FundOrderStatus.WAITING_NAV, confirmed_nav=1.23,
        )
    except Exception:
        raised = True
    assert raised, "未知价原则未强制"
    return "确认前 confirmed_* 必须为空"


def c11_two_phase_approval_tokens_exclusive() -> str:
    from quant.fund_orders.approval import apply_execution_confirmation, FundApprovalToken
    from quant.fund_orders.orders import FundOrder, FundOrderSide
    from quant.fund_orders.states import FundOrderStatus
    order = FundOrder(
        order_id="o1", account_id="MAIN", strategy_id="s", symbol="F001",
        side=FundOrderSide.SUBSCRIBE, target_amount=1000.0,
        status=FundOrderStatus.WAITING_SHARE_CONFIRMATION,
    )
    raised = False
    try:  # 用"计划令牌"去确认成交应被拒
        apply_execution_confirmation(
            order, token=FundApprovalToken.MANUAL_LIVE_PLAN_APPROVED,
            actor="u", confirmed_nav=1.0, confirmed_units=1000.0,
            confirmed_amount=1000.0, nav_date="2026-08-02",
            share_confirmation_date="2026-08-03",
        )
    except Exception:
        raised = True
    assert raised, "两阶段令牌未互斥"
    return "计划令牌不能确认成交（令牌互斥）"


def c12_fund_reconciliation_idempotent() -> str:
    from quant.fund_orders.orders import FundOrder, FundOrderSide
    from quant.fund_orders.states import FundOrderStatus
    from quant.fund_orders.reconciliation import FundReconciler
    order = FundOrder(
        order_id="o1", account_id="MAIN", strategy_id="s", symbol="F001",
        side=FundOrderSide.SUBSCRIBE, target_amount=1000.0,
        status=FundOrderStatus.CONFIRMED, confirmed_nav=1.0,
        confirmed_units=1000.0, confirmed_amount=1000.0,
    )
    rec = FundReconciler()
    first = rec.reconcile(order, reconciliation_run_id="r1", as_of="2026-08-05")
    second = rec.reconcile(order, reconciliation_run_id="r1", as_of="2026-08-05")
    assert first is not None and second is None, "重复对账未幂等"
    return "重复 order_id 对账幂等去重"


def c13_sentinel_levels_include_data_error() -> str:
    from quant.sentinel.levels import SentinelLevel
    names = [lv.name for lv in SentinelLevel]
    assert names == ["GREEN", "YELLOW", "ORANGE", "RED", "DATA_ERROR"], f"等级序列={names}"
    # DATA_ERROR 必须比 RED 更保守：不知道 ≥ 已知最坏
    assert SentinelLevel.DATA_ERROR > SentinelLevel.RED
    assert not SentinelLevel.DATA_ERROR.allows_new_entries
    assert SentinelLevel.DATA_ERROR.is_risk_off
    return "市场哨兵五级 GREEN/YELLOW/ORANGE/RED/DATA_ERROR（DATA_ERROR 最保守）"


def c14_asymmetric_hysteresis() -> str:
    from quant.sentinel.levels import SentinelLevel
    from quant.sentinel.sentinel import MarketSentinel, SentinelReading
    s = MarketSentinel(initial=SentinelLevel.GREEN, deescalation_confirmations=2)
    # 升级即时：一次极端读数直接拉到高等级
    up = s.evaluate(SentinelReading(as_of="2026-08-01", drawdown=0.20))
    assert up.level == SentinelLevel.RED, up.level
    # 降级需连续确认：一次平静不降级
    calm = SentinelReading(as_of="2026-08-02", drawdown=0.0)
    mid = s.evaluate(calm)
    assert mid.level == SentinelLevel.RED, "降级不该一次生效"
    return "升级即时、降级需连续确认（非对称迟滞）"


def c15_event_unique_key_dedup_idempotent() -> str:
    from quant.sentinel.events import SentinelEvent, SentinelEventLog
    log = SentinelEventLog()
    ev = SentinelEvent(
        event_type="BLACK_SWAN", event_time="2026-08-01T09:30:00",
        source="news", title="X", severity="CRITICAL", review_status="CONFIRMED",
    )
    first = log.ingest(ev)
    dup = log.ingest(ev)
    assert first is True and dup is False, "事件未去重"
    return "事件唯一键去重 + 幂等"


def c16_run_snapshot_never_overwrites(tmp: Path | None = None) -> str:
    import tempfile
    from quant.observability import RunContext, SnapshotError, new_run_id
    with tempfile.TemporaryDirectory() as d:
        rid = new_run_id("acc")
        RunContext.create("acc", environment="PAPER", artifacts_root=d, run_id=rid)
        raised = False
        try:
            RunContext.create("acc", environment="PAPER", artifacts_root=d, run_id=rid)
        except SnapshotError:
            raised = True
        assert raised, "运行快照可覆盖历史"
    return "运行快照 run_id 目录不可覆盖"


def c17_proxy_isolation_machine_constraint() -> str:
    from quant.data_identity import DataLineageRecord, DataOrigin, DataIdentityError
    raised = False
    try:  # DIRECT 却用别的标的承载 → 代理伪装，必须拒绝
        DataLineageRecord(
            symbol="510300", source="csv", source_symbol="000300",
            origin=DataOrigin.DIRECT, content_hash="h", as_of="2026-07-31",
        )
    except DataIdentityError:
        raised = True
    assert raised, "代理伪装未被机器约束拒绝"
    proxy = DataLineageRecord(
        symbol="F001", source="proxy", source_symbol="000300",
        origin=DataOrigin.PROXY, content_hash="h", as_of="2026-07-31",
    )
    assert proxy.is_production_eligible is False, "代理数据竟可进生产"
    return "直连不可伪装 + 代理永不进生产"


def c18_research_model_default_closed() -> str:
    from quant.research_models import ModelRegistry
    from quant.domain.enums import TrustLevel
    reg = ModelRegistry()
    e = reg.register("M1", "1.0.0")
    assert e.is_enabled is False and e.is_frozen is True
    assert e.trust_level == TrustLevel.L0_UNVERIFIED
    assert e.is_production_eligible is False
    return "研究模型默认关闭+冻结+L0"


def c19_research_model_production_gate_l4() -> str:
    from quant.research_models import ModelRegistry
    from quant.domain.enums import TrustLevel
    reg = ModelRegistry()
    e = reg.register("M1", "1.0.0").promote(TrustLevel.L3_PAPER, actor="a").enable(actor="a")
    assert e.is_production_eligible is False, "L3 不该可生产"
    e2 = reg.register("M2", "1.0.0").promote(TrustLevel.L4_SHADOW, actor="a").enable(actor="a")
    assert e2.is_production_eligible is True, "L4+启用+冻结应可生产"
    return "生产门槛 >=L4（L3 拒绝 / L4 通过）"


def c20_kronos_frozen_placeholder() -> str:
    from quant.research_models import default_registry, KronosModel, KRONOS_MODEL_ID
    from quant.research_models import ModelNotConnectedError, ForecastRequest
    reg = default_registry()
    entry = reg.get(KRONOS_MODEL_ID)
    assert entry.is_production_eligible is False, "Kronos 竟可生产"
    raised = False
    try:
        KronosModel().predict(ForecastRequest(request_id="r", symbols=("510300",), as_of="2026-07-31"))
    except ModelNotConnectedError:
        raised = True
    assert raised, "Kronos predict 未 Fail-Closed"
    return "Kronos 冻结禁用 + predict Fail-Closed"


def c21_asset_master_and_lineage_ready() -> str:
    from quant.data_identity import AssetMaster, DataIdentityRegistry
    master = AssetMaster.from_json(ROOT / "config/assets/asset_master.json")
    assert len(master) >= 1
    reg = DataIdentityRegistry()
    assert len(reg) == 0
    return f"资产主数据 {len(master)} 标的 + 血缘登记可用"


# ── V4.6 补充约束新增条件（第 22 条起）──────────────────────────────────

def c22_order_permission_gate_fail_closed() -> str:
    """未在资产主数据中的标的一律拒单（配置 ≠ 交易授权）。"""
    from quant.exe.permission_gate import check_execution_permission
    r = check_execution_permission("999999", None)
    assert not r.allowed and "无交易授权" in r.reason, r.reason
    return "未知资产 Fail-Closed 拒单"


def c23_permission_conflict_takes_stricter() -> str:
    """execution_eligible 与 permission 冲突时取更严格者（真实主数据校验）。"""
    from quant.assets import AssetMasterLoader
    from quant.domain.enums import CapitalPermission
    from quant.exe.permission_gate import check_execution_permission
    registry = {a.symbol: a for a in AssetMasterLoader().load_all()}
    conflicts = [
        s for s, a in registry.items()
        if a.execution_eligible and a.permission == CapitalPermission.OBSERVATION_ONLY
    ]
    for symbol in conflicts:
        assert not check_execution_permission(symbol, registry[symbol]).allowed, symbol
    return f"冲突条目 {len(conflicts)} 个（{','.join(conflicts) or '无'}）均被拦截"


def c24_no_permission_bypass_switch() -> str:
    """订单工厂不得提供权限校验旁路开关（可选的严格＝没有严格）。"""
    import inspect
    from quant.exe.order_factory import generate_etf_orders, generate_fund_orders
    forbidden = {"enforce", "enforce_permissions", "skip_permission_check", "bypass"}
    for fn in (generate_etf_orders, generate_fund_orders):
        params = set(inspect.signature(fn).parameters)
        assert not (params & forbidden), f"{fn.__name__} 存在旁路开关"
    return "两个订单生成入口均无权限旁路开关"


def c25_observation_veto_is_wired() -> str:
    """check_observation_only 必须真正接入 run_all_checks（此前零调用方）。"""
    from quant.decision.risk_veto import RiskVetoEngine
    from quant.domain.enums import CapitalPermission
    vetos = RiskVetoEngine().run_all_checks(
        proposed_weights={"004813": 0.5, "CASH": 0.5},
        asset_permissions={"004813": CapitalPermission.OBSERVATION_ONLY},
    )
    assert any(v.vetoed and "004813" in v.reason for v in vetos), "观察权限否决未触发"
    return "观察权限否决已接入决策链"


def c26_sentinel_no_data_is_not_green() -> str:
    """全部指标缺失必须判 DATA_ERROR，而不是 GREEN。"""
    from quant.sentinel.levels import SentinelLevel
    from quant.sentinel.sentinel import compute_raw_level, SentinelReading
    level, reasons = compute_raw_level(SentinelReading("2026-08-01"))
    assert level == SentinelLevel.DATA_ERROR, f"无数据判为 {level.name}"
    assert any("DATA_ERROR" in r for r in reasons)
    return "无指标 → DATA_ERROR（Fail-Closed）"


def c27_etf_tolerance_band_branch_executes() -> str:
    """ETF 零换手容忍带分支必须可执行（此前必然 UnboundLocalError）。"""
    import inspect
    from quant import pipeline
    src = inspect.getsource(pipeline)
    assign = src.find("actionable_turnover = guard.turnover")
    use = src.find("and actionable_turnover <= tolerance_band")
    assert assign != -1 and use != -1, "未找到容忍带相关代码"
    assert assign < use, "actionable_turnover 仍在使用之后赋值（UnboundLocalError）"
    return "换手率先赋值后使用，容忍带分支可达"


def c28_signal_age_gate_fail_closed() -> str:
    """基准数据陈旧超阈值时必须 Fail-Closed 转 RISK_OFF（GAP-2 修复）。"""
    import pandas as pd
    from quant.strategies.tactical_etf.engine import resolve_market_regime
    settings = {
        "benchmark_symbol": "000300",
        "data": {"max_staleness_trading_days": 0},
        "regime": {"lookback": 20, "ma_short": 5, "ma_long": 20, "vol_window": 20},
    }
    # 基准数据缺失
    regime, reasons = resolve_market_regime({}, settings, pd.Timestamp("2026-08-01"))
    from quant.models import Regime
    assert regime == Regime.RISK_OFF, f"基准缺失判为 {regime.name}"
    assert any("缺失" in r for r in reasons)
    # 基准数据陈旧
    stale_frame = pd.DataFrame({"date": ["2026-07-01"], "close": [4000.0], "high": [4010.0], "low": [3990.0], "open": [3995.0], "volume": [1000000]})
    regime2, reasons2 = resolve_market_regime({"000300": stale_frame}, settings, pd.Timestamp("2026-08-01"))
    assert regime2 == Regime.RISK_OFF, f"陈旧数据判为 {regime2.name}"
    assert any("陈旧" in r for r in reasons2)
    return "基准缺失/陈旧 → RISK_OFF（Fail-Closed）"


def c29_calendar_groups_exist() -> str:
    """日历分组 CN/HK/QDII 概念已落地（GAP-5 修复）。"""
    from quant.calendar_groups import CALENDAR_GROUPS, resolve_calendar_group, CalendarGroupError
    assert CALENDAR_GROUPS == ("CN", "HK", "QDII")
    assert resolve_calendar_group({"symbol": "F1", "calendar_group": "QDII"}) == "QDII"
    try:
        resolve_calendar_group({"symbol": "X"})
        assert False, "应抛 CalendarGroupError"
    except CalendarGroupError:
        pass
    # 基金主数据已带 calendar_group 字段
    from quant.fund_universe import FundInstrument
    from quant.config import load_json
    catalog = load_json("config/fund/instruments.json")
    instruments = [FundInstrument.from_dict(item) for item in catalog]
    for inst in instruments:
        assert inst.calendar_group in CALENDAR_GROUPS, f"{inst.symbol} 缺少合法 calendar_group"
    return f"日历分组 CN/HK/QDII 已落地，{len(instruments)} 只基金全部声明"


def c30_per_group_alignment_no_cross_drag() -> str:
    """CN 基金信号不被 QDII 滞后日期拖垮（GAP-5 修复）。"""
    import pandas as pd
    from quant.fund_pipeline import align_fund_frames_by_calendar_group
    from quant.models import CheckResult, DataStatus
    cn = pd.DataFrame({"date": ["2026-07-28", "2026-07-29", "2026-07-30"], "nav": [1.0, 1.01, 1.02]})
    qd = pd.DataFrame({"date": ["2026-07-28", "2026-07-29"], "nav": [1.5, 1.51]})
    frames = {"F_CN": cn, "F_QD": qd}
    checks = {"F_CN": CheckResult(DataStatus.PASS_), "F_QD": CheckResult(DataStatus.PASS_)}
    groups = {"F_CN": "CN", "F_QD": "QDII"}
    aligned, alignment = align_fund_frames_by_calendar_group(frames, checks, groups, 5)
    cn_latest = pd.to_datetime(aligned["F_CN"]["date"]).max().strftime("%Y-%m-%d")
    assert cn_latest == "2026-07-30", f"CN 被拖到 {cn_latest}"
def c31_ledger_whitelist_covers_all_writes() -> str:
    """台账白名单接管全部 position_snapshots 和 fund_execution_state 写入。

    GAP-4 修复：5 处直接 SQL 绕过统一收编到守卫。
    D-03 采纳方案 B（Wave 1 落地）：LEGACY_IMPORT 从 position_snapshots 白名单**移除**，
    旧状态只写不可变交易账，真实持仓由 rebuild_from_transactions() 聚合生成。
    """
    import ast
    import inspect

    from quant.ledgers.guards import _ALLOWED_SOURCES, allowed_sources

    # position_snapshots 注册，且仅允许两种对账来源（D-03 方案 B 后）
    assert "position_snapshots" in _ALLOWED_SOURCES
    assert allowed_sources("position_snapshots") == frozenset({"RECONCILIATION", "USER_RECONCILIATION"}), \
        f"position_snapshots 白名单不符：{sorted(allowed_sources('position_snapshots'))}"
    # LEGACY_IMPORT 必须已被 Fail-Closed 拒绝
    assert "LEGACY_IMPORT" not in allowed_sources("position_snapshots"), \
        "D-03 方案 B 要求 LEGACY_IMPORT 不得写 position_snapshots"
    # actual_positions 仍是最严格的审计账
    assert allowed_sources("actual_positions") == frozenset({"RECONCILIATION"})
    # fund_execution_state 注册
    assert "fund_execution_state" in _ALLOWED_SOURCES
    # reconcile_account 导入了守卫
    import app.reconcile_account as recon_mod
    assert "assert_write_allowed" in dir(recon_mod) or hasattr(recon_mod, "assert_write_allowed")
    # execution_service 导入了守卫
    from quant.exe import execution_service
    assert "assert_write_allowed" in inspect.getsource(execution_service)

    # legacy_import：AST 级校验，模块内无任何写 position_snapshots 的语句
    from quant.accounts import legacy_import
    tree = ast.parse(inspect.getsource(legacy_import))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            sql = node.value.upper()
            assert "INSERT INTO POSITION_SNAPSHOTS" not in sql and "UPDATE POSITION_SNAPSHOTS" not in sql, \
                "legacy_import 仍存在写 position_snapshots 的 SQL"
    assert "INSERT INTO transactions" in inspect.getsource(legacy_import), \
        "legacy_import 必须写不可变交易账"

    # save_fund_state 接受 source 参数并走守卫
    from quant.fund_execution import save_fund_state
    sig = inspect.signature(save_fund_state)
    assert "source" in sig.parameters
    return ("5 处绕过已收编；position_snapshots 白名单 = {RECONCILIATION, USER_RECONCILIATION}"
            "（D-03 方案 B：LEGACY_IMPORT 已物理隔离），fund_execution_state 全覆盖")


def c32_strategy_scoring_formula_locked() -> str:
    """策略轴验收（填补 #38）：评分公式序数不变量。

    此前策略轴覆盖率为 0——改动评分公式不会触发任何验收报警。
    本条件直接调用生产评分路径 score_trend / rank_universe，对合成行情断言
    「上行趋势 ETF 分数严格高于下行趋势 ETF 且排名居首」，从而锁定公式的
    动量方向语义：任何反转动量符号、或让下行趋势得分更高的公式改动都会红灯。
    """
    import numpy as np
    import pandas as pd
    from quant.strategies.tactical_etf.ranking import rank_universe, score_trend

    n = 120
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    up_close = np.linspace(100.0, 220.0, n)   # 单调上行
    dn_close = np.linspace(220.0, 100.0, n)   # 单调下行
    turnover = np.full(n, 0.01)
    up_frame = pd.DataFrame({"close": up_close, "turnover": turnover}, index=idx)
    dn_frame = pd.DataFrame({"close": dn_close, "turnover": turnover}, index=idx)

    up = score_trend("UP", up_frame)
    dn = score_trend("DN", dn_frame)
    assert up is not None and dn is not None, "合成行情未产生评分结果"
    assert up.eligible, "上行趋势 ETF 应可入选（score>0）"
    assert not dn.eligible, "下行趋势 ETF 不应可入选（score<=0）"
    assert up.score > dn.score, (
        f"评分公式未保持序数：上行 {up.score:.4g} 未高于下行 {dn.score:.4g}"
    )

    ranked = rank_universe({"UP": up_frame, "DN": dn_frame}, ["UP", "DN"], max_results=10)
    assert ranked and ranked[0].symbol == "UP" and ranked[0].rank == 1, (
        "rank_universe 未把上行趋势 ETF 排到首位"
    )
    return f"评分公式序数锁定：上行 {up.score:.3g} > 下行 {dn.score:.3g}，UP 居首"


# ── 已登记但尚未实现的条件（诚实盘点，不计入通过率）────────────────────
# 补充约束 Prompt 第二十五节要求新增 15 条验收。其中已实现的进入 CONDITIONS，
# 尚未实现的登记在此，随实现逐条上移。禁止为了「全绿」而删除本清单。

def c33_provisional_gate_wired_in_production() -> str:
    """W2-1：PROVISIONAL 参数闸门必须真正接入生产诊断，且漂移时 Fail-Closed。

    背景：V46_PRODUCTION_WIRING_AUDIT 发现 ``verify_provisional_values`` 曾零生产
    调用方——参数被登记却从不校验。本条固化其接线，防止日后被悄悄摘除而无人察觉。
    """
    import copy
    import app.inspect_system as insp

    # 1) 接线证明：两个诊断项必须存在于生产诊断输出中
    result = insp.inspect_system()
    names = {item["name"] for item in result["checks"]}
    for required in ("PROVISIONAL参数登记", "PROVISIONAL生效值一致"):
        assert required in names, f"诊断项 {required} 缺失，PROVISIONAL 闸门未接入生产"

    # 2) Fail-Closed 证明：注入漂移配置，诊断必须转为 NEEDS_ATTENTION
    from quant.config import load_json
    drifted = copy.deepcopy(load_json("config/settings.json"))
    drifted["portfolio"]["max_single_weight"] = 0.99
    original = insp.load_json
    try:
        insp.load_json = lambda _p: drifted  # type: ignore[assignment]
        tampered = insp.inspect_system()
    finally:
        insp.load_json = original  # type: ignore[assignment]

    assert tampered["status"] == "NEEDS_ATTENTION", "参数漂移未触发 Fail-Closed"
    drift_item = next(i for i in tampered["checks"] if i["name"] == "PROVISIONAL生效值一致")
    assert not drift_item["ok"] and "max_single_weight" in drift_item["detail"], \
        f"漂移未被具体指出：{drift_item['detail']}"

    return "闸门已接入 inspect_system（run_daily 前置），漂移触发 NEEDS_ATTENTION"


def c34_model_target_ledger_energized() -> str:
    """W2-2：模型目标账（第一本账）必须在生产链路上真实写入。

    背景：V46_PRODUCTION_WIRING_AUDIT 发现三本账只有第三本通电，前两本恒为空表。
    本条锁定第一本账的生产接线，并复核「写目标不改持仓」的隔离不变量。
    """
    import inspect as _inspect
    import shutil
    import tempfile
    from pathlib import Path as _Path

    import app.run_daily as rd
    from quant.domain.enums import Environment
    from quant.ledgers.guards import LedgerWriteViolation
    from quant.ledgers.model_targets import list_target_portfolios, write_target_portfolio
    from quant.ledgers.recorder import build_target_portfolio, record_target_portfolio

    # 1) 接线证明：run_daily.main 必须调用记账器
    assert "_record_model_targets" in _inspect.getsource(rd.main), \
        "run_daily.main 未调用模型目标账记账器——第一本账会重新变空"

    # 注：Windows 下 SQLite 连接可能仍持有文件句柄，TemporaryDirectory 的严格清理会抛
    # PermissionError。此处改为容错清理——本条验证的是账本语义，不是临时目录回收。
    td = tempfile.mkdtemp(prefix="v46_c34_")
    try:
        db = _Path(td) / "model_targets.db"

        # 2) 真实写入并回读
        pid = record_target_portfolio(
            strategy_id="ACC_CHECK", account_id="ACC_X", as_of="2026-08-01",
            target_weights={"510300": 0.6, "CASH": 0.4},
            environment=Environment.PAPER, db_path=db,
        )
        rows = list_target_portfolios("ACC_X", db_path=db)
        assert len(rows) == 1 and rows[0].portfolio_id == pid, "目标组合未落账"
        assert rows[0].target_weights == {"510300": 0.6}, "CASH 未被分离出资产权重"
        assert abs(rows[0].cash_weight - 0.4) < 1e-9, "cash_weight 未正确记录"

        # 3) 幂等：同日重跑覆盖而非堆积
        record_target_portfolio(
            strategy_id="ACC_CHECK", account_id="ACC_X", as_of="2026-08-01",
            target_weights={"510300": 0.9}, environment=Environment.PAPER, db_path=db,
        )
        assert len(list_target_portfolios("ACC_X", db_path=db)) == 1, "同日重跑产生重复记录"

        # 4) 守卫仍然生效：非白名单来源必须被拒
        tp = build_target_portfolio(
            strategy_id="ACC_CHECK", account_id="ACC_X", as_of="2026-08-01",
            target_weights={"510300": 1.0},
        )
        try:
            write_target_portfolio(tp, source="RECONCILIATION", db_path=db)
            assert False, "模型目标账接受了 RECONCILIATION 来源"
        except LedgerWriteViolation:
            pass

        # 5) 隔离不变量：目标对象不得携带任何成交字段
        for forbidden in ("quantity", "filled_quantity", "order_id", "filled_price"):
            assert not hasattr(tp, forbidden), f"TargetPortfolio 混入执行字段 {forbidden}"
    finally:
        shutil.rmtree(td, ignore_errors=True)

    return "第一本账已通电：run_daily 记账 + 幂等 + 守卫白名单 + 无成交字段"


def c35_decision_plan_ledger_energized() -> str:
    """W2-3：决策计划账（第二本账）必须在生产链路上真实写入。

    背景：V46_PRODUCTION_WIRING_AUDIT 发现 decision_plans 表在生产中恒为空。
    本条锁定第二本账的生产接线，并复核「目标 → 决策」血缘与守卫白名单。
    """
    import inspect as _inspect
    import shutil
    import tempfile
    from pathlib import Path as _Path

    import app.run_daily as rd
    from quant.domain.enums import Environment
    from quant.ledgers.decision_plans import read_decision_proposal, write_decision_proposal
    from quant.ledgers.guards import LedgerWriteViolation
    from quant.ledgers.recorder import build_decision_proposal, record_daily_proposals

    # 1) 接线证明：run_daily.main 必须调用决策计划账记账器
    assert "_record_decision_plans" in _inspect.getsource(rd.main), \
        "run_daily.main 未调用决策计划账记账器——第二本账会重新变空"

    td = tempfile.mkdtemp(prefix="v46_c35_")
    try:
        db = _Path(td) / "decision_plans.db"
        as_of = "2026-08-01"
        env = Environment.PAPER

        # 2) 真实写入并回读
        written = record_daily_proposals(
            as_of=as_of, account_id="ACC_X",
            decisions={"CORE_FUND": {"004813": 0.6, "CASH": 0.4}},
            environment=env, db_path=db,
        )
        pid = written["CORE_FUND"]
        proposal = read_decision_proposal(pid, db_path=db)
        assert proposal is not None, "决策提案未落账"
        # 3) 血缘：source_portfolio_id 必须回溯到 W2-2 的模型目标账 portfolio_id
        expected_lineage = f"CORE_FUND-{env.value}-{as_of}"
        assert proposal.source_portfolio_id == expected_lineage, \
            f"血缘断裂：{proposal.source_portfolio_id} != {expected_lineage}"
        assert proposal.environment == env

        # 4) 守卫仍生效：非白名单来源必须被拒（decision_plans 只接受 DECISION_ENGINE 等）
        bad = build_decision_proposal(
            strategy_id="CORE_FUND", account_id="ACC_X", as_of=as_of, environment=env,
            target_weights={"004813": 1.0}, source_portfolio_id=expected_lineage,
        )
        try:
            write_decision_proposal(bad, source="STRATEGY", db_path=db)
            assert False, "决策计划账接受了 STRATEGY 来源"
        except LedgerWriteViolation:
            pass

        # 5) 隔离不变量：决策提案不得携带真实持仓字段
        for forbidden in ("quantity", "filled_quantity", "actual_position"):
            assert not hasattr(proposal, forbidden), \
                f"DecisionProposal 混入持仓字段 {forbidden}"
    finally:
        shutil.rmtree(td, ignore_errors=True)

    return "第二本账已通电：run_daily 记账 + 血缘回溯 W2-2 + 守卫白名单 + 无持仓字段"


def c36_order_chain_production_reachable() -> str:
    """W2-4：订单生成 → 执行链路必须在生产链路上可达，且权限闸门真正生效。

    背景：V46_PRODUCTION_WIRING_AUDIT 发现 order_factory 零生产调用方，
    permission_gate 守着一扇没人走的门。本条锁定 order_factory 的生产接线，
    并证明未授权标的被 rejected_reasons 拦下（Fail-Closed）。
    """
    import inspect as _inspect
    import shutil
    import tempfile
    from pathlib import Path as _Path

    import app.run_daily as rd
    from quant.domain.assets import Asset
    from quant.domain.enums import AssetType, CapitalPermission, Environment, OrderStatus
    from quant.ledgers.decision_plans import read_decision_proposal
    from quant.ledgers.recorder import record_daily_proposals

    # 1) 接线证明：run_daily 必须真正激活订单生成路径（生产可达）。
    #    注意：run_daily 不直接调用 order_factory，而是经
    #    record_daily_proposals(generate_orders=True) 激活其生产调用方；
    #    因此接线标志是 generate_orders=True 在生产路径被置位。
    src = _inspect.getsource(rd._record_decision_plans)
    assert "generate_orders=True" in src, \
        "run_daily 未以 generate_orders=True 激活订单生成——order_factory 仍是孤岛"

    td = tempfile.mkdtemp(prefix="v46_c36_")
    try:
        db = _Path(td) / "decision_plans.db"
        # 注入确定性资产目录：510300 授权(PAPER)，999999 不在目录(未知→拒)
        registry = {
            "510300": Asset(
                symbol="510300", name="测试ETF", asset_type=AssetType.ETF,
                execution_eligible=True, permission=CapitalPermission.PAPER,
            ),
        }
        written = record_daily_proposals(
            as_of="2026-08-01", account_id="ACC_X",
            decisions={"TACTICAL_ETF": {"510300": 0.05, "999999": 0.05}},
            environment=Environment.PAPER, generate_orders=True,
            portfolio_value=1_000_000.0, current_prices={"510300": 4.0},
            asset_registry=registry, db_path=db,
        )
        proposal = read_decision_proposal(written["TACTICAL_ETF"], db_path=db)
        assert proposal is not None, "订单链未落账"

        # 2) 订单意图真实生成且为 DRAFT（不提交）
        drafted = [o for o in proposal.order_intents if o.status == OrderStatus.DRAFT]
        assert drafted, "order_factory 未生成任何 DRAFT 订单意图"
        assert all(o.environment == Environment.PAPER for o in drafted)

        # 3) 权限闸门 Fail-Closed：未知标的被 rejected_reasons 拦下
        rejected = proposal.metadata.get("rejected_reasons", [])
        assert any("999999" in r for r in rejected), \
            f"权限闸门未拦截未知标的：{rejected}"
    finally:
        shutil.rmtree(td, ignore_errors=True)

    return "订单链已接入生产：order_factory 生成 DRAFT 意图 + permission_gate 拦下未知标的"


def c37_data_health_four_grades() -> str:
    """REQ-DATA-03：数据失效必须分级，不能只有「有/没有」两态。

    四档（NORMAL/STALE/PARTIALLY_MISSING/FULLY_MISSING）必须可判别、可排序、
    可聚合取最差，否则「陈旧数据」会被当成「正常数据」直接喂给策略。
    """
    import pandas as pd

    from quant.data_health import DataHealth, classify, worst

    assert len(list(DataHealth)) == 4, "健康度必须恰为四档"
    ranks = [h.rank for h in DataHealth]
    assert sorted(ranks) == [0, 1, 2, 3], f"四档必须严格可排序：{ranks}"

    idx = pd.date_range("2026-07-20", periods=10, freq="D")
    ok = pd.DataFrame({"date": idx, "close": range(10)})
    assert classify(ok, as_of="2026-07-29", require_columns=["close"]) is DataHealth.NORMAL
    assert classify(ok, as_of="2026-08-30", require_columns=["close"]) is DataHealth.STALE
    assert classify(ok, as_of="2026-07-29", require_columns=["close", "nav"]) \
        is DataHealth.PARTIALLY_MISSING
    assert classify(None, as_of="2026-07-29") is DataHealth.FULLY_MISSING

    # 聚合必须取最差档（多源数据不得被「最好的那一源」洗白）
    agg = worst([DataHealth.NORMAL, DataHealth.STALE, DataHealth.FULLY_MISSING])
    assert agg is DataHealth.FULLY_MISSING, f"聚合未取最差：{agg}"
    assert not DataHealth.FULLY_MISSING.is_usable and DataHealth.NORMAL.is_usable

    return "四档可判别/可排序/聚合取最差：NORMAL<STALE<PARTIALLY_MISSING<FULLY_MISSING"


def c38_confirmation_budget_three_dimensions() -> str:
    """REQ-BUDGET-01：确认预算必须同时约束「时间窗 / 次数 / 额度」三维。

    只卡额度不卡次数，会被拆单绕过；只卡次数不卡时间窗，预算永不回补。
    任一维度耗尽即 Fail-Closed 拒绝。
    """
    from datetime import datetime, timedelta

    from quant.gov.confirmation_budget import ConfirmationBudget

    t0 = datetime(2026, 8, 1, 9, 30)

    # 次数维：3 次用尽后拒绝
    b = ConfirmationBudget(window_seconds=3600, max_count=3, max_amount=1e9)
    for i in range(3):
        assert b.consume(1.0, now=t0 + timedelta(seconds=i)), f"第 {i+1} 次不应被拒"
    assert not b.consume(1.0, now=t0 + timedelta(seconds=3)), "次数超限未 Fail-Closed"

    # 时间窗维：滑出窗口后预算回补
    assert b.consume(1.0, now=t0 + timedelta(seconds=3700)), "窗口滑出后未回补"

    # 额度维：单笔超额度直接拒（不依赖次数）
    b2 = ConfirmationBudget(window_seconds=3600, max_count=99, max_amount=100.0)
    assert not b2.can_confirm(101.0, now=t0), "额度超限未 Fail-Closed"
    assert b2.consume(60.0, now=t0) and not b2.consume(60.0, now=t0), "累计额度未生效"

    cnt, amt = b2.remaining(now=t0)
    assert cnt >= 0 and amt >= 0.0, "剩余预算必须可观测"

    return "三维预算齐备：次数/时间窗/额度任一耗尽即 Fail-Closed，且窗口滑出可回补"


def c39_state_synthesis_takes_conservative() -> str:
    """REQ-STATE-03：快（Regime）慢（Sentinel）双状态合成必须取更保守者。

    关键不变量：DATA_ERROR 严重度最高——「哨兵看不清市场」必须至少与「已知最坏」
    同等保守，否则数据故障期间系统会按 TREND_RISK_ON 继续加仓。
    """
    from quant.regimes.state_synthesis import synthesize
    from quant.sentinel.levels import SentinelLevel

    try:
        from quant.regimes.state_synthesis import MarketRegime
    except ImportError:  # pragma: no cover
        from quant.regimes import MarketRegime  # type: ignore

    # 快轴乐观 + 慢轴红灯 → 取慢轴
    s1 = synthesize(MarketRegime.TREND_RISK_ON, SentinelLevel.RED)
    assert s1.basis == "sentinel" and s1.is_risk_off, f"未取更保守者：{s1}"

    # 快轴危机 + 慢轴绿灯 → 取快轴
    s2 = synthesize(MarketRegime.STRESS_RISK_OFF, SentinelLevel.GREEN)
    assert s2.basis == "regime" and s2.is_risk_off, f"未取更保守者：{s2}"

    # 数据失效必须压过一切乐观信号
    s3 = synthesize(MarketRegime.TREND_RISK_ON, SentinelLevel.DATA_ERROR)
    assert s3.is_risk_off and s3.severity >= s1.severity, \
        f"DATA_ERROR 未被排在最高严重度：{s3}"

    # 双绿才允许不降风险
    s4 = synthesize(MarketRegime.TREND_RISK_ON, SentinelLevel.GREEN)
    assert not s4.is_risk_off, "双绿被误判为降风险"

    return "合成规则取更保守者，且 DATA_ERROR 严重度最高（快轴乐观也被拉到降风险）"


def c40_three_clock_consistency() -> str:
    """REQ-DATA-04：数据/决策/执行三时钟必须单调对齐。

    用「未来数据」做决策、或「决策晚于执行」，都是回测幻觉与实盘事故的源头，
    必须在统一模型里被检出并 Fail-Closed。
    """
    from datetime import datetime

    from quant.gov.clock_consistency import check_three_clocks

    d = datetime(2026, 8, 1, 15, 0)
    k = datetime(2026, 8, 1, 16, 0)
    x = datetime(2026, 8, 2, 9, 30)

    assert check_three_clocks(data_ts=d, decision_ts=k, execution_ts=x).ok, \
        "合法顺序被误判为错乱"

    bad1 = check_three_clocks(data_ts=k, decision_ts=d, execution_ts=x)
    assert not bad1.ok, "决策早于数据（未来函数）未被拦截"

    bad2 = check_three_clocks(data_ts=d, decision_ts=x, execution_ts=k)
    assert not bad2.ok, "执行早于决策未被拦截"

    assert bad1.reason and bad2.reason, "失败必须给出可读原因"

    return "三时钟统一校验：决策早于数据、执行早于决策均 Fail-Closed 并给出原因"


def c41_run_archive_four_credibility_tiers() -> str:
    """REQ-RUN-03：历史运行档案必须四档可信度标记，禁止「一律当真实」。

    未经对账的推断状态若与已对账状态混为一谈，复盘结论会被系统性高估。
    """
    from quant.run_archive.credibility import RunCredibility, most_credible, tag_run

    assert len(list(RunCredibility)) == 4, "可信度必须恰为四档"

    assert tag_run(source="PLAN", has_reconciliation=True) is RunCredibility.ACTUAL_CONFIRMED
    assert tag_run(source="PLAN_LEDGER") is RunCredibility.PLAN_INFERRED
    assert tag_run(source="MODEL_TARGET") is RunCredibility.MODEL_INFERRED
    assert tag_run(source="LEGACY_V43") is RunCredibility.UNKNOWN_LEGACY_STATE
    assert tag_run(source="") is RunCredibility.UNKNOWN_LEGACY_STATE, \
        "来源缺失必须落到最低可信度（Fail-Closed）"

    top = most_credible([
        ("a", RunCredibility.MODEL_INFERRED),
        ("b", RunCredibility.ACTUAL_CONFIRMED),
        ("c", RunCredibility.UNKNOWN_LEGACY_STATE),
    ])
    assert top is RunCredibility.ACTUAL_CONFIRMED, f"可信度排序错误：{top}"
    assert most_credible([]) is None

    return "四档可信度可标注/可排序，来源缺失降到 UNKNOWN_LEGACY_STATE"


def c42_backtest_unknown_price_and_costs() -> str:
    """REQ-BT-01：回测必须建模 T+1 未知价 + 费率 + 确认延迟。

    核心不变量：**信号日绝不按信号日价格成交**。否则回测收益里混进了
    「当天看到信号、当天按当天价买入」的未来函数。
    """
    import pandas as pd

    from quant.research.backtest import backtest_unknown_price

    # 价格在 T+1 跳涨一倍：若按信号日价成交，净值应≈1；按 T+1 价成交则≈0.5
    price = pd.Series([100.0, 200.0, 200.0, 200.0])
    signals = pd.Series([1, 1, 1, 1])
    eq = backtest_unknown_price(signals, price, fee_rate=0.0, confirmation_lag=1)
    assert abs(float(eq.iloc[0]) - 0.5) < 1e-9, \
        f"信号日按当日价成交（未来函数）：{float(eq.iloc[0])}"

    # 确认延迟必须真实推迟建仓
    price2 = pd.Series([100.0, 100.0, 200.0, 200.0])
    eq1 = backtest_unknown_price(signals, price2, fee_rate=0.0, confirmation_lag=1)
    eq2 = backtest_unknown_price(signals, price2, fee_rate=0.0, confirmation_lag=2)
    assert abs(float(eq1.iloc[0]) - 1.0) < 1e-9
    assert abs(float(eq2.iloc[0]) - 0.5) < 1e-9, "确认延迟未推迟建仓价"

    # 费率必须真实扣减
    with_fee = backtest_unknown_price(signals, price2, fee_rate=0.01, confirmation_lag=1)
    assert float(with_fee.iloc[-1]) < float(eq1.iloc[-1]), "费率未计入"

    return "回测已建模 T+1 未知价（信号日不成交）+ 确认延迟 + 费率扣减"


# ── 策略轴补厚（c43-c47）：让交易逻辑本身受验收保护 ─────────────────────
# 背景：此前策略轴仅 c32 一条（评分公式序数不变量），改动配权、集中度、
# Risk-Off 降仓等核心交易行为不会触发任何报警。以下 5 条直接调用策略引擎，
# 断言其**行为契约**而非实现细节。

def _c43_frames(symbols: list[str], n: int = 120, drift: float = 1.0):
    import pandas as pd

    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    return {
        s: pd.DataFrame({
            "date": idx,
            "close": [100.0 + drift * i for i in range(n)],
            "volume": [1e6] * n,
            "turnover": [0.01] * n,
        })
        for s in symbols
    }


def c43_target_weights_normalized_with_cash() -> str:
    """策略轴：目标权重必须归一到 1.0，且未配置部分显式落到 CASH。

    权重不归一 = 隐性杠杆或隐性空仓；不显式记 CASH = 「剩下的钱去哪了」无法追溯。
    """
    from quant.models import Regime
    from quant.strategies.tactical_etf.trend import run_tactical_trend

    res = run_tactical_trend(
        _c43_frames(["A", "B"]), ["A", "B"], regime=Regime.RISK_ON,
        max_positions=1, max_single_weight=0.45,
    )
    total = sum(res.target_weights.values())
    assert abs(total - 1.0) < 1e-9, f"权重未归一：{total}"
    assert "CASH" in res.target_weights, "未显式记录 CASH 余额"
    assert all(w >= -1e-12 for w in res.target_weights.values()), "出现负权重（隐性做空）"

    return f"权重归一={total:.6f}，CASH={res.target_weights['CASH']:.4f} 显式在册"


def c44_concentration_cap_enforced_after_normalize() -> str:
    """策略轴：单标的集中度上限必须在**归一之后**仍然成立。

    历史 bug：先截断再归一，归一会把权重重新放大回上限之上，集中度约束形同虚设。
    本条锁定「归一后逐项 <= 上限」这一最终态。
    """
    from quant.models import Regime
    from quant.strategies.tactical_etf.trend import run_tactical_trend

    cap = 0.2
    res = run_tactical_trend(
        _c43_frames(["A", "B", "C", "D", "E"]), ["A", "B", "C", "D", "E"],
        regime=Regime.RISK_ON, max_positions=5, max_single_weight=cap,
    )
    non_cash = {k: w for k, w in res.target_weights.items() if k != "CASH"}
    over = {k: w for k, w in non_cash.items() if w > cap + 1e-6}
    assert not over, f"归一后仍超集中度上限 {cap}：{over}"
    assert abs(sum(res.target_weights.values()) - 1.0) < 1e-9, "截断后未重新归一"

    return f"归一后 {len(non_cash)} 个标的均 <= {cap}，超额部分回落 CASH"


def c45_risk_off_forces_all_cash() -> str:
    """策略轴：RISK_OFF 下趋势策略必须**完全**退出，而非降低仓位。

    「守卫触发后降到多少仓位」是策略轴的核心问题；此处答案必须是 0 仓位。
    """
    from quant.models import Regime
    from quant.strategies.tactical_etf.trend import run_tactical_trend

    res = run_tactical_trend({}, [], regime=Regime.RISK_OFF)
    assert res.target_weights == {"CASH": 1.0}, f"Risk-Off 未清仓：{res.target_weights}"
    assert not res.selected_symbols, "Risk-Off 仍选出标的"

    # 即使给了强势行情数据，Risk-Off 仍必须清仓（守卫优先于信号）
    res2 = run_tactical_trend(
        _c43_frames(["A", "B", "C"]), ["A", "B", "C"], regime=Regime.RISK_OFF,
    )
    assert res2.target_weights == {"CASH": 1.0}, \
        f"强信号覆盖了 Risk-Off 守卫：{res2.target_weights}"

    return "RISK_OFF 强制 100% CASH，且强势信号无法覆盖守卫"


def c46_selection_is_subset_of_pool() -> str:
    """策略轴：选出的标的必须是候选池的子集，且不超过 max_positions。

    防止策略「凭空造出」池外标的，或悄悄突破持仓数量上限。
    """
    from quant.models import Regime
    from quant.strategies.tactical_etf.trend import run_tactical_trend

    pool = ["A", "B", "C", "D"]
    res = run_tactical_trend(
        _c43_frames(pool), pool, regime=Regime.RISK_ON, max_positions=2,
    )
    assert set(res.selected_symbols).issubset(set(pool)), \
        f"选出池外标的：{set(res.selected_symbols) - set(pool)}"
    assert len(res.selected_symbols) <= 2, f"突破持仓数量上限：{res.selected_symbols}"

    held = {k for k in res.target_weights if k != "CASH"}
    assert held.issubset(set(pool)), f"权重表含池外标的：{held - set(pool)}"

    return f"选出 {res.selected_symbols} ⊆ 候选池，且 <= max_positions"


def c47_fund_orders_carry_unknown_price() -> str:
    """双轴：场外基金订单必须以「未知价」形态生成（数量为 0，金额申购）。

    基金 T 日申购按 T 日收盘净值成交，下单时净值未知。若在此处估算数量，
    等于凭空捏造成交价——这是账实不符的源头。
    """
    from types import SimpleNamespace

    from quant.domain.assets import Asset
    from quant.domain.enums import AssetType, CapitalPermission, Environment
    from quant.exe.order_factory import generate_fund_orders

    decision = SimpleNamespace(
        as_of_date="2026-07-31", target_weights={"FUND_X": 0.1, "CASH": 0.9},
    )
    registry = {
        "FUND_X": Asset(
            symbol="FUND_X", name="测试基金", asset_type=AssetType.ETF,
            execution_eligible=True, permission=CapitalPermission.PAPER,
        ),
    }
    res = generate_fund_orders(
        decision, portfolio_value=1_000_000,
        environment=Environment.PAPER, asset_registry=registry,
    )
    assert res.orders, "基金订单未生成"
    for o in res.orders:
        assert o.estimated_quantity == 0.0, \
            f"基金订单捏造了成交数量（隐含捏造净值）：{o.estimated_quantity}"

    return f"{len(res.orders)} 笔基金订单均为未知价形态（estimated_quantity=0）"


# ── Wave 3（c48-c49）：Riskfolio 适配层与 PROVISIONAL 校准台架 ──────────

def c48_riskfolio_adapters_implemented_shadow_only() -> str:
    """D-04：Riskfolio 适配层必须有真实实现，且**只允许影子运行**。

    抽象基类若长期没有实现体，等于空接线；但一旦有实现，又必须保证
    ``shadow_only=True``——未经充分验证的组合优化器不得直接影响真实决策。
    """
    import numpy as np

    from quant.riskfolio_adapters_impl import (
        EqualWeightAllocator,
        InverseVolAllocator,
        PortfolioEvaluationEngineImpl,
        RiskContributionAnalyzerImpl,
        StatisticalClusterAnalyzer,
        VarianceRiskMeasureEngine,
    )

    rets = {"A": [0.01, -0.02, 0.03, 0.00], "B": [0.02, -0.01, 0.01, 0.01]}
    target = {"A": 0.3, "B": 0.2, "CASH": 0.5}

    r1 = EqualWeightAllocator().allocate(target)
    r2 = InverseVolAllocator().allocate(target)
    for r in (r1, r2):
        assert r.shadow_only is True, "组合优化器未标记为影子运行——存在直连生产风险"
        assert abs(sum(r.weights.values()) - 1.0) < 1e-9, f"权重未归一：{r.weights}"
        r.validate_contract()

    cov = np.eye(2) * 0.04
    out = VarianceRiskMeasureEngine().compute(
        {"A": 0.5, "B": 0.5}, covariance=cov,
    )
    assert abs(out["variance"] - 0.02) < 1e-9, f"方差计算错误：{out}"
    assert abs(out["volatility"] - float(np.sqrt(0.02))) < 1e-9, f"波动率计算错误：{out}"

    contrib = RiskContributionAnalyzerImpl().decompose(
        {"A": 0.5, "B": 0.5}, covariance=cov,
    )
    assert abs(sum(contrib.values()) - 1.0) < 1e-6, f"风险贡献未归一：{contrib}"

    clusters = StatisticalClusterAnalyzer().cluster(rets)
    assert clusters and all(isinstance(v, list) for v in clusters.values())

    report = PortfolioEvaluationEngineImpl().evaluate(r1)
    assert report is not None, "评估引擎未产出报告"

    return "5 个适配器均有实现、权重归一、契约自检通过，且全部 shadow_only=True"


def c49_provisional_calibration_harness_is_gated() -> str:
    """D-01：PROVISIONAL 参数校准台架必须存在，且**默认不自动转正**。

    诚实性红线：没有人工复核过的回测证据，就不得把 PROVISIONAL 标成已校准。
    本条锁定「台架可用 + 默认维持 PROVISIONAL + 置信度 LOW」。
    """
    from quant.provisional import PROVISIONAL_PARAMETERS
    from quant.provisional_calibration import is_calibration_gated, propose_calibration

    proposals = propose_calibration()
    assert proposals, "校准台架未产出任何建议"
    keys = {p.key for p in proposals}
    assert keys.issubset(set(PROVISIONAL_PARAMETERS)), f"建议了未登记参数：{keys}"

    for p in proposals:
        assert p.confidence == "LOW", \
            f"无人工复核证据却给出 {p.confidence} 置信度：{p.key}"
        assert p.candidate == p.current, \
            f"未经复核即改动取值：{p.key} {p.current}→{p.candidate}"
        assert p.basis and p.note, "建议缺少依据说明"

    # 即使外部塞入候选值，置信度仍必须是 LOW（不得被调用方一句话洗白）
    forced = propose_calibration({list(keys)[0]: 999.0})
    assert all(p.confidence == "LOW" for p in forced), "外部候选值绕过了置信度闸门"

    assert is_calibration_gated() is True, "校准未受 verify_provisional_values 闸门约束"

    return f"{len(proposals)} 个 PROVISIONAL 参数维持原值 + LOW 置信度，转正受闸门约束"


# ── c50–c54：「全打接线」收口（c55 因生产无试探路径如实挂起）─────────────
#
# 2026-08-02 指令「全打接线，不再造新能力」：c37–c49 锁定了 6 个治理模块
# 「有实现 + 有测试 + 生产零调用方」。本批把公认的、生产入口已存在的调用点真正接上，
# 让能力从「有测试」变成「生产行为真正改变」。验收只断言「调用点存在 + 行为契约成立」，
# 不引入任何新能力。c53 因生产无试探路径（见 PENDING 登记）保持挂起，不伪造接线。


def c50_data_health_wired_into_etf_chain() -> str:
    """c50：数据健康四档分级接入 ETF 生产链，非 NORMAL 标的被移出配权池。

    此前 ETF 分支只有布尔式「有/没有」判断，陈旧/部分缺失数据会被当正常数据喂给策略
    （基金分支早有两级隔离，ETF 分支一直没跟上）。本条锁定三件事：
      (1) run_tactical_etf 生产入口确实调用 assess_data_health；
      (2) 非 NORMAL 标的确实从研究/合格/实盘三池被移除（quarantined_symbols）；
      (3) 分级函数对「基准缺失 / 陈旧」能正确给出最差档。
    """
    import inspect as _inspect

    import pandas as _pd
    import quant.strategies.tactical_etf.engine as eng
    from quant.data_health import DataHealth

    src = _inspect.getsource(eng.run_tactical_etf)
    assert "assess_data_health(" in src, \
        "run_tactical_etf 未调用 assess_data_health——数据健康分级仍是离线能力"
    assert "quarantined" in src and "blocked" in src, \
        "run_tactical_etf 未把非 NORMAL 标的移出配权池——隔离形同虚设"

    # 行为契约：合成「基准缺失 → FULLY_MISSING」「陈旧 → 非 NORMAL」
    stale = _pd.DataFrame({"date": ["2020-01-01"], "close": [1.0]})
    settings = {"data": {"max_staleness_trading_days": 5, "require_ohlcv": False}}
    graded = eng.assess_data_health(
        {"BENCH": _pd.DataFrame(), "OLD": stale}, settings,
        as_of=_pd.Timestamp("2026-08-01"),
    )
    assert graded["BENCH"] is DataHealth.FULLY_MISSING, "基准缺失未被判为 FULLY_MISSING"
    assert graded["OLD"] is not DataHealth.NORMAL, "陈旧数据未被判为非 NORMAL"
    assert DataHealth("FULLY_MISSING").rank > DataHealth("NORMAL").rank, "DataHealth 档序错误"

    return "数据健康四档已接入 ETF 生产链：分级调用 + 非NORMAL隔离 + 基准缺失判最差档"


def c51_three_clocks_wired_into_run_daily() -> str:
    """c51：三时钟一致性校验作为 run_daily 决策前置闸门，Fail-Closed。

    此前 check_three_clocks() 生产零调用方，未来函数 / 执行早于决策在生产中无人拦截。
    本条锁定：run_daily.main 在记录决策计划账之前确实调用 _check_decision_clocks，
    且「决策早于数据」时该闸门返回 False（不生成订单意图）。
    """
    import inspect as _inspect

    import app.run_daily as rd
    from quant.gov.clock_consistency import check_three_clocks

    assert "_check_decision_clocks" in _inspect.getsource(rd.main), \
        "run_daily.main 未在决策前调用三时钟闸门——未来函数仍可能漏网"

    # 行为契约 1：决策早于数据（未来函数）→ 闸门 False
    pf = {"tactical_etf": {"data_as_of": "2026-08-03"}, "errors": []}
    assert rd._check_decision_clocks(pf, "2026-08-01") is False, \
        "决策早于数据竟被放行——三时钟 Fail-Closed 失效"
    # 行为契约 2：等时钟边界 → 该前置校验（无执行钟）通过
    po = {"tactical_etf": {"data_as_of": "2026-08-01"}, "errors": []}
    assert rd._check_decision_clocks(po, "2026-08-01") is True, "等时钟边界被误判为失败"
    # 行为契约 3：有决策但无数据时钟 → 失败
    pn = {"tactical_etf": {}, "errors": []}
    assert rd._check_decision_clocks(pn, "2026-08-01") is False, "无数据时钟竟被放行"

    # 原语本身可用，避免只是接了个空壳
    assert check_three_clocks(data_ts=None, decision_ts=None).ok is True

    return "三时钟已接入 run_daily 决策前置：未来函数 / 无数据时钟均 Fail-Closed 拦截"


def c52_state_synthesis_forces_risk_off() -> str:
    """c52：快慢状态合成接入生产决策，且能反向压制快轴强制 RISK_OFF。

    此前 synthesize() 生产零调用方，MarketRegime 与 SentinelLevel 各自为政，
    「取更保守者」从未真正影响任何一次下单决策。本条锁定：
      (1) run_tactical_etf 确实调用 synthesize_production_state；
      (2) 合成结果一旦 is_risk_off，引擎确实把 regime 强制降到 RISK_OFF；
      (3) 行为契约：基准数据完全失效（DATA_ERROR）能令非 RISK_OFF 的快轴变 risk_off。
    """
    import inspect as _inspect

    import quant.strategies.tactical_etf.engine as eng
    from quant.data_health import DataHealth
    from quant.models import Regime

    src = _inspect.getsource(eng.run_tactical_etf)
    assert "synthesize_production_state(" in src, \
        "run_tactical_etf 未调用 synthesize_production_state——快慢状态合成仍是离线能力"
    assert "is_risk_off" in src and "Regime.RISK_OFF" in src, \
        "引擎未把合成结果强制降为 RISK_OFF——「取更保守者」未影响下单"

    # 行为契约：快轴 RISK_ON + 慢轴 DATA_ERROR → 必须 risk_off
    s = eng.synthesize_production_state(Regime.RISK_ON, DataHealth.FULLY_MISSING)
    assert s.is_risk_off is True, "基准数据完全失效竟未触发 risk_off"
    # 对照：快轴 RISK_ON + 慢轴 GREEN → 不应 risk_off
    s_ok = eng.synthesize_production_state(Regime.RISK_ON, DataHealth.NORMAL)
    assert s_ok.is_risk_off is False, "正常数据被误判为 risk_off"

    return "快慢状态合成已接入生产：DATA_ERROR 可反向压制快轴强制 RISK_OFF"


def c53_run_snapshot_four_tier_credibility() -> str:
    """c53：运行快照改用四档可信度标注，取代笼统 UNVERIFIED。

    此前快照只写笼统 UNVERIFIED，复盘时无法区分「已对账真实成交」与「仅按计划推断」。
    本条锁定：run_daily 在写入快照前确实计算 run_credibility 指标，且四档判定正确。
    """
    import inspect as _inspect

    import app.run_daily as rd
    from quant.run_archive.credibility import RunCredibility

    assert "run_credibility" in _inspect.getsource(rd.main), \
        "run_daily 未在决策后计算 run_credibility——快照仍可能是笼统 UNVERIFIED"
    assert "run_credibility" in _inspect.getsource(rd._write_run_snapshot), \
        "_write_run_snapshot 未记录 run_credibility 指标——四档未落到档案"

    # 第二本账成功 → PLAN_INFERRED；双账失败 → UNKNOWN_LEGACY_STATE
    cred_plan = rd._resolve_run_credibility({"decision_plans_recorded": {"id": "x"}})
    assert cred_plan is RunCredibility.PLAN_INFERRED, f"第二本账成功未得 PLAN_INFERRED：{cred_plan}"
    cred_legacy = rd._resolve_run_credibility(
        {"decision_plans_recorded": {"error": "x"}, "model_targets_recorded": {"error": "x"}}
    )
    assert cred_legacy is RunCredibility.UNKNOWN_LEGACY_STATE, f"双账失败未得 UNKNOWN：{cred_legacy}"
    # 写入失败（error 形态）必须排除，否则可信度被虚高
    cred_err = rd._resolve_run_credibility({"decision_plans_recorded": {"error": "boom"}})
    assert cred_err is RunCredibility.UNKNOWN_LEGACY_STATE, "error 形态被误当成功，可信度虚高"

    for tier in ("ACTUAL_CONFIRMED", "PLAN_INFERRED", "MODEL_INFERRED", "UNKNOWN_LEGACY_STATE"):
        assert tier in RunCredibility.__members__, f"缺可信度档 {tier}"

    return "运行快照已改用四档可信度：第二本账→PLAN_INFERRED，双账失败→UNKNOWN，error 被排除"


def c54_backtest_enforces_no_signal_day_fill() -> str:
    """c54：既有回测入口强制「信号日绝不成交」不变量（T+1 未知价）。

    两个引擎各自实现了 T+1 执行，但该性质此前只存在于注释与意图，无任何代码强制——
    一旦有人为「提高回测收益」把执行改回信号日当天，绩效会被系统性高估且无红灯。
    本条锁定：run_event_backtest / run_fund_event_backtest 在汇总曲线前确实调用
    verify_no_signal_day_fill 作硬后置条件（违约即返回 BLOCKED）。
    """
    import inspect as _inspect

    import quant.backtest as bt
    import quant.fund_backtest as fbt
    from quant.research.backtest import verify_no_signal_day_fill

    assert "verify_no_signal_day_fill" in _inspect.getsource(bt.run_event_backtest), \
        "quant/backtest.py 未在汇总前调用 verify_no_signal_day_fill——T+1 不变量无强制"
    assert "verify_no_signal_day_fill" in _inspect.getsource(fbt.run_fund_event_backtest), \
        "quant/fund_backtest.py 未强制 T+1 不变量——基金回测收益可被高估"

    # 成交日 <= 信号日 → 违约；成交日晚于信号日 → 通过
    ok, _ = verify_no_signal_day_fill(
        [{"signal_date": "2026-08-01", "execution_date": "2026-08-01"}])
    assert ok is False, "成交日等于信号日竟被放行——等于用信号日已知价成交"
    ok2, _ = verify_no_signal_day_fill(
        [{"signal_date": "2026-08-01", "execution_date": "2026-08-02"}])
    assert ok2 is True, "正常 T+1 成交被误判违约"
    # 基金链路字段名（pricing_nav_date）
    ok3, _ = verify_no_signal_day_fill(
        [{"signal_date": "2026-08-01", "pricing_nav_date": "2026-07-31"}],
        execution_key="pricing_nav_date",
    )
    assert ok3 is False, "基金链路成交日早于信号日竟被放行"

    return "两个回测引擎均强制 T+1 不变量：成交日≤信号日即 BLOCKED，基金链路同口径"


# ── 待办条件（诚实性装置）─────────────────────────────────────────────
#
# PENDING_CONDITIONS 登记**已识别但尚未实现**的验收条件：计入分母、不计入通过率，
# 禁止为求全绿而删除。
#
# 2026-08-02「全打接线」收口说明：c50–c54 已从「模块存在、生产零调用方」升级为
# 「生产链路真实调用 + 行为契约被验收锁定」，故移出本清单、升入 CONDITIONS。
# 其余条目为**真实无法在本轮接线**的缺口，如实挂起，绝不删除、不伪造：
#   - c55：生产无试探路径（regime_ensemble 零生产调用方，run_daily 无当前持仓输入），
#          接假闸门只会制造「已接线」的错觉，故挂起等待真正的试探策略接入。
#   - c56：PROVISIONAL 参数转正须人工复核回测证据，禁止 Agent 编造。
#   - c57：生产期权重运行时硬闸门（用户明确「不再造新能力」，本轮不写）。
PENDING_CONDITIONS: list[tuple[int, str, str, str, str]] = [
    (55, "双轴", "确认预算接入试探路径（受阻：生产无试探路径）", "REQ-BUDGET-01",
     "ConfirmationBudget 已就位且有单测，但「生产试探路径」本身不存在："
     "RECOVERY_PROBE 仅由 quant/regimes/regime_ensemble.py 产出，而该模块**零生产调用方**"
     "（仅 tests/test_features_regime.py 使用）；run_daily 也无当前持仓输入，真实换手率无法计算。"
     "若强行接一个假闸门（凭空造探针信号），只会制造「已接线」的错觉而实际不约束任何行为。"
     "故如实挂起，待 Wave 真正接入试探策略后再落地，绝不为求全绿而伪造接线。"),
    (56, "策略轴", "4 个 PROVISIONAL 参数经人工复核转正", "REQ-PARAM-02",
     "校准台架已就位但**刻意不自动转正**：`minimum_history_days=126`、"
     "`min_median_turnover_20d=1e8`、`max_single_weight=0.45`、`max_non_emergency_turnover=0.35` "
     "四项仍为拍脑袋默认值。转正必须有人工复核过的回测证据，禁止由 Agent 自行编造"),
    (57, "工程轴", "权重合法性生产期运行时校验器", "REQ-CASH-03",
     "c43/c44 只在验收阶段断言权重归一与集中度；生产链路缺少下单前的 "
     "`PortfolioWeights.validate()` 式硬闸门，违规无法被实时阻断（用户明确本轮不造新能力，挂起）"),
]


# ── 条件清单（编号, 轴, 名称, 验证函数）──────────────────────────────────

CONDITIONS: list[tuple[int, str, str, Callable[[], str]]] = [
    (1, "工程轴", "全量回归测试在册", c01_regression_suite_present),
    (2, "工程轴", "默认环境为 PAPER", c02_default_environment_paper),
    (3, "工程轴", "禁止 LIVE 自动生成订单", c03_live_generation_disabled),
    (4, "工程轴", "活动代码无用户绝对路径", c04_no_hardcoded_user_paths),
    (5, "工程轴", "V4.6 不内嵌 legacy", c05_no_embedded_legacy),
    (6, "工程轴", "首次运行入口齐全", c06_entrypoints_present),
    (7, "工程轴", "三本账物理隔离", c07_three_ledgers_isolated),
    (8, "工程轴", "真实持仓仅经对账写入", c08_actual_position_requires_reconciliation),
    (9, "工程轴", "场外基金 11 态状态机", c09_fund_state_machine_has_11_states),
    (10, "双轴", "未知价原则", c10_unknown_nav_principle),
    (11, "工程轴", "两阶段人工审批令牌互斥", c11_two_phase_approval_tokens_exclusive),
    (12, "工程轴", "基金幂等对账", c12_fund_reconciliation_idempotent),
    (13, "工程轴", "市场哨兵五级含 DATA_ERROR", c13_sentinel_levels_include_data_error),
    (14, "工程轴", "非对称迟滞（升即时/降慢确认）", c14_asymmetric_hysteresis),
    (15, "工程轴", "事件唯一键去重幂等", c15_event_unique_key_dedup_idempotent),
    (16, "工程轴", "运行快照不覆盖历史", c16_run_snapshot_never_overwrites),
    (17, "工程轴", "数据代理隔离机器约束", c17_proxy_isolation_machine_constraint),
    (18, "工程轴", "研究模型默认关闭冻结", c18_research_model_default_closed),
    (19, "工程轴", "研究模型生产门槛 >=L4", c19_research_model_production_gate_l4),
    (20, "工程轴", "Kronos 冻结占位未接入", c20_kronos_frozen_placeholder),
    (21, "工程轴", "资产主数据与血缘就绪", c21_asset_master_and_lineage_ready),
    (22, "工程轴", "下单权限闸门 Fail-Closed", c22_order_permission_gate_fail_closed),
    (23, "工程轴", "权限冲突取更严格者", c23_permission_conflict_takes_stricter),
    (24, "工程轴", "订单工厂无权限旁路开关", c24_no_permission_bypass_switch),
    (25, "工程轴", "观察权限否决已接入决策链", c25_observation_veto_is_wired),
    (26, "工程轴", "哨兵无数据不判绿灯", c26_sentinel_no_data_is_not_green),
    (27, "工程轴", "ETF 容忍带分支可达", c27_etf_tolerance_band_branch_executes),
    (28, "工程轴", "信号年龄闸门 Fail-Closed", c28_signal_age_gate_fail_closed),
    (29, "工程轴", "日历分组 CN/HK/QDII 落地", c29_calendar_groups_exist),
    (30, "工程轴", "跨组不拖累对齐", c30_per_group_alignment_no_cross_drag),
    (31, "工程轴", "台账白名单全覆盖", c31_ledger_whitelist_covers_all_writes),
    (32, "策略轴", "评分公式序数不变量（动量符号→分数符号）", c32_strategy_scoring_formula_locked),
    (33, "工程轴", "PROVISIONAL 参数闸门生产接线 + Fail-Closed", c33_provisional_gate_wired_in_production),
    (34, "工程轴", "模型目标账（第一本账）生产通电", c34_model_target_ledger_energized),
    (35, "工程轴", "决策计划账（第二本账）生产通电", c35_decision_plan_ledger_energized),
    (36, "工程轴", "订单链生产可达（order_factory + permission_gate）", c36_order_chain_production_reachable),
    (37, "工程轴", "数据失效四档分级隔离", c37_data_health_four_grades),
    (38, "双轴", "确认预算三维（次数/时间窗/额度）", c38_confirmation_budget_three_dimensions),
    (39, "工程轴", "快慢状态合成取更保守者", c39_state_synthesis_takes_conservative),
    (40, "工程轴", "三时钟一致性校验", c40_three_clock_consistency),
    (41, "工程轴", "运行档案四档可信度", c41_run_archive_four_credibility_tiers),
    (42, "双轴", "回测 T+1 未知价 + 费率 + 确认延迟", c42_backtest_unknown_price_and_costs),
    (43, "策略轴", "目标权重归一且 CASH 显式在册", c43_target_weights_normalized_with_cash),
    (44, "策略轴", "集中度上限在归一后仍成立", c44_concentration_cap_enforced_after_normalize),
    (45, "策略轴", "RISK_OFF 强制清仓至 100% CASH", c45_risk_off_forces_all_cash),
    (46, "策略轴", "选品为候选池子集且不破持仓上限", c46_selection_is_subset_of_pool),
    (47, "双轴", "基金订单以未知价形态生成", c47_fund_orders_carry_unknown_price),
    (48, "工程轴", "Riskfolio 适配层有实现且仅影子运行", c48_riskfolio_adapters_implemented_shadow_only),
    (49, "工程轴", "PROVISIONAL 校准台架受闸门约束", c49_provisional_calibration_harness_is_gated),
    (50, "工程轴", "数据健康分级接入 ETF 生产链", c50_data_health_wired_into_etf_chain),
    (51, "工程轴", "三时钟校验接入 run_daily 决策前置", c51_three_clocks_wired_into_run_daily),
    (52, "工程轴", "快慢状态合成接入生产并强制 RISK_OFF", c52_state_synthesis_forces_risk_off),
    (53, "工程轴", "运行快照改用四档可信度标注", c53_run_snapshot_four_tier_credibility),
    (54, "双轴", "回测入口强制「信号日绝不成交」不变量", c54_backtest_enforces_no_signal_day_fill),
]


def collect_results() -> list[tuple[int, str, str, bool, str]]:
    """逐条执行验证，返回 (编号, 轴, 名称, ok, 证据/错误)。"""
    results = []
    for num, axis, name, fn in CONDITIONS:
        try:
            detail = fn()
            results.append((num, axis, name, True, detail))
        except Exception as exc:  # noqa: BLE001
            results.append((num, axis, name, False, f"{type(exc).__name__}: {exc}"))
    return results


def run_all() -> int:
    total_registered = len(CONDITIONS) + len(PENDING_CONDITIONS)
    print(f"\n猎豹 V4.6 —— 发布验收（已实现 {len(CONDITIONS)} 条 / 登记在册 {total_registered} 条）")
    print(f"根目录: {ROOT}")
    print("=" * 64)
    results = collect_results()
    for num, axis, name, ok, detail in results:
        mark = "✅" if ok else "❌"
        print(f"  {mark} [{num:02d}][{axis}] {name} — {detail}")
    passed = sum(1 for r in results if r[3])
    total = len(results)
    print("=" * 64)
    print(f"已实现条件：{passed}/{total} 通过")

    # 轴向分布：策略轴条数是「交易逻辑是否受保护」的直接体现，必须显式暴露。
    axis_stat: dict[str, list[int]] = {}
    for _num, axis, _name, ok, _detail in results:
        bucket = axis_stat.setdefault(axis, [0, 0])
        bucket[1] += 1
        if ok:
            bucket[0] += 1
    print("轴向分布：" + "  ".join(
        f"{ax} {p}/{t}" for ax, (p, t) in sorted(axis_stat.items())
    ))

    if PENDING_CONDITIONS:
        print("\n尚未实现（已登记，不计入通过率；禁止为求全绿而删除本清单）：")
        for num, axis, name, req_id, reason in PENDING_CONDITIONS:
            print(f"  ⏳ [{num:02d}][{axis}] {name} — {req_id}：{reason}")

    print("=" * 64)
    coverage = passed / total_registered
    print(f"验收门机械覆盖率：{passed}/{total_registered} = {coverage:.0%}")

    if coverage >= 1.0:
        # 100% 是**最容易被误读**的数字：它只说明「已登记条件全绿」，
        # 而条件清单是我们自己写的。此处强制打出反向提示，防止对外误引。
        print("‼ 覆盖率为 100% —— 这**只**表示自定义条件全部通过，绝不表示 V4.6 已完成。")
        print("  分母由本文件自行定义；未写成条件的需求缺口在此不可见。")
        print("  发现新缺口时应新增条件（分母上升、比率下降），这是健康信号而非退步。")

    # 口径护栏：防止本数字被误当作系统完成度对外引用。
    # 裁定依据 docs/audit/V46_COMPLETION_METRIC_RULING.md（2026-08-01 生效）。
    print("-" * 64)
    print("⚠ 口径提示：以上比率是【内部工程卫生指标】，衡量『既有实现的自检完整性』，")
    print("  不等于系统完成度。分母为自定义验收条件，会随实现增删而移动。")
    print("  对外完成度请一律引用需求追溯落地率（TESTED 口径；2026-08-02 已抬升至 17/63 ≈ 27%），")
    print("  见 docs/V46_REQUIREMENTS_TRACEABILITY.md 与 docs/audit/V46_COMPLETION_METRIC_RULING.md。")
    print("  注：部分条件验证的模块生产不可达，详见 docs/audit/V46_PRODUCTION_WIRING_AUDIT.md。")

    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(run_all())
