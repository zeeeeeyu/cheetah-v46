"""V4.6 daily orchestration.

First-run scope: PAPER analysis only. It runs core-fund and tactical-ETF engines,
writes a combined report, and never creates or submits LIVE orders.
"""
from __future__ import annotations
import argparse, json, traceback
import pandas as pd
from quant.config import project_path, load_json
from quant.fund_pipeline import run_fund_daily
from quant.gov.clock_consistency import check_three_clocks
from quant.run_archive.credibility import RunCredibility, tag_run
from quant.strategies.tactical_etf.engine import run_tactical_etf
from quant.accounts import DEFAULT_ACCOUNT_ID
from quant.domain.enums import Environment
from quant.ledgers.recorder import record_daily_decisions, record_daily_proposals
from app.inspect_system import inspect_system
from app.common import save_json_report, run_id, open_run_snapshot


def _resolve_run_credibility(payload: dict) -> RunCredibility:
    """按四档标注本次运行档案的可信度（c53 / REQ-RUN-03）。

    此前快照只写一个笼统的 ``UNVERIFIED``，复盘时无法区分「已对账的真实成交」
    与「仅按计划推断的状态」。四档判定优先级（高 → 低）：

    - ``ACTUAL_CONFIRMED``     经对账确认（run_daily 是纯分析流程，不做对账，
                               故此处恒不触发；保留分支是为了让接线点显式可见）
    - ``PLAN_INFERRED``        第二本账（决策计划）已成功写入
    - ``MODEL_INFERRED``       仅第一本账（模型目标）写入成功
    - ``UNKNOWN_LEGACY_STATE`` 两本账都没写成 —— 来源不明，降到最低档（Fail-Closed）

    注意「写入成功」的判定必须排除 ``{"error": ...}`` 形态：记账失败是 best-effort
    吞掉的，若把它当成功，可信度会被虚高一档。
    """
    def _ok(entry: object) -> bool:
        return bool(entry) and not (isinstance(entry, dict) and "error" in entry)

    if _ok(payload.get("decision_plans_recorded")):
        source = "PLAN"
    elif _ok(payload.get("model_targets_recorded")):
        source = "MODEL"
    else:
        source = "LEGACY"
    # run_daily 从不写第三本账（真实持仓只经对账写入），故 has_reconciliation 恒为 False。
    return tag_run(source=source, has_reconciliation=False)


def _check_decision_clocks(payload: dict, as_of: str) -> bool:
    """决策前三时钟一致性校验（c51 / REQ-DATA-04）。Fail-Closed。

    ``check_three_clocks()`` 此前生产零调用方——未来函数与执行早于决策在生产中
    无人拦截。此处是它的第一个真实调用点，且**校验不通过就不生成订单意图**。

    数据时钟取各引擎实际使用的最新 bar 日期；决策时钟取本次运行的 ``as_of``。
    执行时钟在提案生成后才有（``OrderIntent.earliest_execution_date``），
    故本前置闸门只传两钟，执行钟在 :func:`_audit_execution_clock` 中事后审计。
    """
    tactical = payload.get("tactical_etf") or {}
    data_as_of = tactical.get("data_as_of")
    try:
        data_ts = pd.Timestamp(data_as_of).to_pydatetime() if data_as_of else None
        decision_ts = pd.Timestamp(as_of).to_pydatetime()
    except Exception as exc:  # noqa: BLE001
        payload["clock_check"] = {"ok": False, "reason": f"时钟解析失败：{exc}"}
        payload["errors"].append(f"CLOCK: 时钟解析失败：{exc}")
        return False

    check = check_three_clocks(data_ts=data_ts, decision_ts=decision_ts)
    payload["clock_check"] = {
        "ok": check.ok,
        "reason": check.reason,
        "data_ts": str(check.data_ts) if check.data_ts else None,
        "decision_ts": str(check.decision_ts) if check.decision_ts else None,
        "execution_ts": None,
    }
    if not check.ok:
        payload["errors"].append(f"CLOCK: {check.reason} → Fail-Closed，本次不生成订单意图")
        print(f"[gate] 三时钟校验未通过：{check.reason} → 跳过决策计划账写入")
    return check.ok


def _write_run_snapshot(payload: dict, as_of: str) -> None:
    """尽力写一份不可覆盖的运行快照；失败绝不影响实际运行（best-effort）。"""
    try:
        with open_run_snapshot("daily", environment="PAPER") as snap:
            snap.register_input("as_of", "parameter", "as_of", str(as_of), as_of=str(as_of))
            snap.write_json("daily_paper", payload)
            snap.record_metric("has_errors", bool(payload.get("errors")))
            snap.record_metric("live_orders_created", payload.get("live_orders_created", False))
            # c54：四档可信度取代笼统 UNVERIFIED
            snap.record_metric("run_credibility", payload.get("run_credibility"))
            snap.record_metric("clock_check_ok", (payload.get("clock_check") or {}).get("ok"))
            snap.record_metric("worst_data_health", (payload.get("tactical_etf") or {}).get("worst_data_health"))
    except Exception as exc:  # noqa: BLE001 - 可观测性绝不能拖垮主流程
        print(f"[warn] 运行快照写入失败（不影响主流程）: {exc}")


def _record_model_targets(payload: dict, as_of: str) -> None:
    """W2-2：把当日各策略的目标权重记入模型目标账（第一本账）。

    此前该账本在生产中恒为空——三本账只有第三本通电。此处补上第一本。

    严格边界：只记录「模型想要什么」，不产生订单、不改变持仓、不回流影响决策。
    采用 best-effort（与运行快照一致）：记账失败不应中断分析主流程，但必须显式告警，
    并把结果写入 payload 以便事后审计。
    """
    weights: dict[str, dict | None] = {}
    core = payload.get("core_fund") or {}
    tactical = payload.get("tactical_etf") or {}
    if core.get("target_weights"):
        weights["CORE_FUND"] = core["target_weights"]
    if tactical.get("target_weights"):
        weights["TACTICAL_ETF"] = tactical["target_weights"]

    if not weights:
        payload["model_targets_recorded"] = {}
        return
    try:
        written = record_daily_decisions(
            as_of=as_of,
            account_id=DEFAULT_ACCOUNT_ID,
            decisions=weights,
            environment=Environment.PAPER,
        )
        payload["model_targets_recorded"] = written
        print(f"模型目标账已记录 {len(written)} 条：{', '.join(written.values())}")
    except Exception as exc:  # noqa: BLE001
        payload["model_targets_recorded"] = {"error": str(exc)}
        print(f"[warn] 模型目标账写入失败（不影响主流程，但已丢失审计记录）: {exc}")


def _record_decision_plans(payload: dict, as_of: str) -> None:
    """W2-3 + W2-4：把当日各策略的决策记入决策计划账（第二本账）。

    此前该账本在生产中恒为空——三本账只有第三本通电。此处补上第二本，
    并打通「模型目标账 → 决策计划账」血缘（source_portfolio_id 指回 W2-2）。
    W2-4 同时经 order_factory + permission_gate 生成订单意图（DRAFT，不提交）。

    严格边界：只记录「系统决定做什么」，不提交订单、不改变持仓、不回流影响决策。
    采用 best-effort（与运行快照一致）：记账失败不应中断分析主流程，但必须显式告警，
    并把结果写入 payload 以便事后审计。
    """
    try:
        settings = load_json("config/settings.json")
        portfolio_value = float(settings.get("portfolio", {}).get("total_value", 1_000_000.0))
    except Exception:
        portfolio_value = 1_000_000.0

    weights: dict[str, dict | None] = {}
    core = payload.get("core_fund") or {}
    tactical = payload.get("tactical_etf") or {}
    if core.get("target_weights"):
        weights["CORE_FUND"] = core["target_weights"]
    if tactical.get("target_weights"):
        weights["TACTICAL_ETF"] = tactical["target_weights"]

    if not weights:
        payload["decision_plans_recorded"] = {}
        return
    try:
        written = record_daily_proposals(
            as_of=as_of,
            account_id=DEFAULT_ACCOUNT_ID,
            decisions=weights,
            environment=Environment.PAPER,
            generate_orders=True,
            portfolio_value=portfolio_value,
            db_path=None,
        )
        payload["decision_plans_recorded"] = written
        print(f"决策计划账已记录 {len(written)} 条提案：{', '.join(written.values())}")
    except Exception as exc:  # noqa: BLE001
        payload["decision_plans_recorded"] = {"error": str(exc)}
        print(f"[warn] 决策计划账写入失败（不影响主流程，但已丢失审计记录）: {exc}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--environment", choices=["PAPER"], default="PAPER")
    parser.add_argument("--as-of", default=None)
    args = parser.parse_args()
    diagnostic = inspect_system()
    if diagnostic["status"] != "READY_FOR_PAPER":
        print(json.dumps(diagnostic, ensure_ascii=False, indent=2))
        raise SystemExit("系统诊断未通过，停止运行。")
    as_of = args.as_of or str(pd.Timestamp.today().date())
    payload = {
        "run_id": run_id("PAPER"),
        "environment": "PAPER",
        "as_of_date": as_of,
        "diagnostic": diagnostic,
        "core_fund": None,
        "tactical_etf": None,
        "errors": [],
        "live_orders_created": False,
    }
    try:
        core = run_fund_daily(as_of)
        payload["core_fund"] = core.to_dict()
    except Exception as exc:
        payload["errors"].append(f"CORE_FUND: {exc}")
    try:
        tactical = run_tactical_etf(as_of)
        payload["tactical_etf"] = tactical.to_dict()
    except Exception as exc:
        payload["errors"].append(f"TACTICAL_ETF: {exc}")
    _record_model_targets(payload, as_of)
    # c51：三时钟前置闸门。校验不通过则不生成订单意图（Fail-Closed）。
    # 模型目标账（第一本账，只记「想要什么」）不受此闸门约束——它不产生订单。
    if _check_decision_clocks(payload, as_of):
        _record_decision_plans(payload, as_of)
    else:
        payload["decision_plans_recorded"] = {"error": "三时钟校验未通过，已 Fail-Closed 跳过"}
    # c54：四档可信度必须在两本账写完之后判定
    payload["run_credibility"] = _resolve_run_credibility(payload).value
    path = save_json_report("decisions", f"daily_paper_{as_of}.json", payload)
    _write_run_snapshot(payload, as_of)
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    print(f"每日PAPER汇总: {path}")
    print("本次未生成、未批准、未提交任何真实订单。")
    if payload["errors"]:
        raise SystemExit(2)

if __name__ == "__main__":
    main()
