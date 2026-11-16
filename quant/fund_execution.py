from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from .config import project_path
from .guard import weights_turnover
from .ledgers.guards import assert_write_allowed


@dataclass
class FundExecutionCheck:
    allowed: bool
    reasons: list[str]
    estimated_cost_weight: float | None


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_fund_state() -> dict[str, Any]:
    return _load_json(project_path("state", "fund_portfolio_state.json"))


def save_fund_state(state: dict[str, Any], *, source: str = "EXECUTION_SERVICE") -> Path:
    """落盘基金组合状态。

    GAP-4 修复：此前直接 write_text 无来源校验，任何代码路径都能改写基金状态。
    现在通过台账白名单守卫校验 source，只允许 EXECUTION_SERVICE 和 MANUAL 写入。
    不接受任何绕过参数（bypass/skip/enforce/force）。
    """
    assert_write_allowed("fund_execution_state", source)
    path = project_path("state", "fund_portfolio_state.json")
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_tradeability_snapshot() -> dict[str, Any]:
    path = project_path("state", "fund_tradeability_snapshot.json")
    if not path.exists():
        return {"mode": "FUND_NAV_ONLY", "as_of_date": None, "funds": {}}
    return _load_json(path)


def validate_fund_state(state: dict[str, Any], valid_symbols: set[str], cash_symbol: str) -> list[str]:
    issues: list[str] = []
    if state.get("mode") != "FUND_NAV_ONLY":
        issues.append("fund_portfolio_state.json 的 mode 必须为 FUND_NAV_ONLY")
    weights = state.get("current_weights", {})
    if not weights:
        issues.append("当前场外基金持仓为空")
        return issues
    total = sum(float(value) for value in weights.values())
    if abs(total - 1.0) > 1e-6:
        issues.append(f"当前场外基金权重总和为 {total:.6f}，必须等于 1.0")
    unknown = [symbol for symbol in weights if symbol not in valid_symbols and symbol != cash_symbol]
    if unknown:
        issues.append(f"场外基金状态含未知代码 {unknown}；先在 config/fund/instruments.json 登记并导入该基金直接净值")
    return issues


def _status_open(status: str, allow_limited: bool) -> bool:
    normalized = str(status or "UNKNOWN").upper()
    return normalized == "OPEN" or (allow_limited and normalized == "LIMITED")


def _rate(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def validate_fund_execution_constraints(
    current_weights: dict[str, float],
    target_weights: dict[str, float],
    instruments: dict[str, dict[str, Any]],
    tradeability: dict[str, Any],
    as_of_date: str,
    execution_config: dict[str, Any],
    cash_symbol: str = "CASH",
) -> FundExecutionCheck:
    """Validate tradability and actual fee information before producing any fund order plan.

    A fund NAV signal is not automatically executable: the user must verify subscription/redemption
    status and channel-specific fees for the decision date.  Unknown is intentionally treated as
    not executable, not as open/zero-fee.
    """
    reasons: list[str] = []
    funds = tradeability.get("funds", {})
    snapshot_date = tradeability.get("as_of_date")
    if execution_config.get("require_tradeability_snapshot_same_day", True) and snapshot_date != as_of_date:
        reasons.append(f"申赎/费率快照日期不匹配：快照 {snapshot_date!r}，决策日 {as_of_date}；需在销售平台重新核实")

    total_cost = 0.0
    any_trade = False
    for symbol in sorted(set(current_weights) | set(target_weights)):
        if symbol == cash_symbol:
            continue
        current = float(current_weights.get(symbol, 0.0))
        target = float(target_weights.get(symbol, 0.0))
        delta = target - current
        if abs(delta) <= 1e-12:
            continue
        any_trade = True
        snapshot = funds.get(symbol, {})
        if not snapshot:
            reasons.append(f"{symbol} 缺少当日申赎状态与费率快照")
            continue
        fee_verified = bool(snapshot.get("fee_verified", False))
        if delta > 0:
            if not _status_open(snapshot.get("purchase_status", "UNKNOWN"), bool(execution_config.get("allow_limited_purchase", False))):
                reasons.append(f"{symbol} 申购状态为 {snapshot.get('purchase_status', 'UNKNOWN')}，不允许生成新增申购计划")
            rate = _rate(snapshot.get("subscription_fee_rate"))
            if execution_config.get("require_fee_verification", True) and (not fee_verified or rate is None):
                reasons.append(f"{symbol} 申购费率未按当日销售平台核实")
            elif rate is not None:
                total_cost += abs(delta) * rate
        else:
            if not _status_open(snapshot.get("redemption_status", "UNKNOWN"), allow_limited=False):
                reasons.append(f"{symbol} 赎回状态为 {snapshot.get('redemption_status', 'UNKNOWN')}，不允许生成赎回计划")
            rate = _rate(snapshot.get("redemption_fee_rate"))
            if execution_config.get("require_fee_verification", True) and (not fee_verified or rate is None):
                reasons.append(f"{symbol} 赎回费率未按持有期限和销售平台核实")
            elif rate is not None:
                total_cost += abs(delta) * rate

    if not any_trade:
        return FundExecutionCheck(True, ["目标与当前持仓一致，无申赎动作"], 0.0)
    return FundExecutionCheck(not reasons, reasons, total_cost if not reasons else None)


def build_fund_trade_plan(
    current_weights: dict[str, float],
    target_weights: dict[str, float],
    as_of_date: str,
    instruments: dict[str, dict[str, Any]],
    tradeability: dict[str, Any],
    execution_config: dict[str, Any],
    portfolio_value_cny: float | None,
    cash_symbol: str = "CASH",
    execution_readiness: str = "READY_FOR_MANUAL_APPROVAL",
    pre_trade_requirements: list[str] | None = None,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    funds = tradeability.get("funds", {})
    pre_trade_text = "；".join(pre_trade_requirements or [])
    plan_type = "PROVISIONAL_STRATEGY_PLAN" if execution_readiness != "READY_FOR_MANUAL_APPROVAL" else "MANUAL_APPROVAL_PLAN"
    subscription_lag = int(execution_config["purchase_confirmation_lag_trading_days"])
    redemption_lag = int(execution_config["redemption_confirmation_lag_trading_days"])
    submission_lag = int(execution_config["order_submission_lag_trading_days"])
    for symbol in sorted(set(current_weights) | set(target_weights)):
        current = float(current_weights.get(symbol, 0.0))
        target = float(target_weights.get(symbol, 0.0))
        delta = target - current
        if abs(delta) < 1e-12:
            action = "HOLD"
        elif symbol == cash_symbol:
            action = "CASH_RESIDUAL"
        elif delta > 0:
            action = "SUBSCRIBE"
        else:
            action = "REDEEM"
        snapshot = funds.get(symbol, {}) if symbol != cash_symbol else {}
        rate = None
        confirmation_lag = None
        if action == "SUBSCRIBE":
            rate = _rate(snapshot.get("subscription_fee_rate"))
            confirmation_lag = subscription_lag
        elif action == "REDEEM":
            rate = _rate(snapshot.get("redemption_fee_rate"))
            confirmation_lag = redemption_lag
        amount = None if portfolio_value_cny is None else abs(delta) * float(portfolio_value_cny)
        rows.append(
            {
                "signal_date": as_of_date,
                "symbol": symbol,
                "name": instruments.get(symbol, {}).get("name", "现金") if symbol != cash_symbol else "现金",
                "current_weight": current,
                "target_weight": target,
                "delta_weight": delta,
                "action": action,
                "estimated_amount_cny": amount,
                "order_submission": f"信号日后第 {submission_lag} 个基金可申赎日（下单前再次核对截止时间）" if action in {"SUBSCRIBE", "REDEEM"} else "不适用",
                "pricing_rule": "按销售平台受理规则对应的未知基金净值定价；不得假设可按信号日净值成交" if action in {"SUBSCRIBE", "REDEEM"} else "不适用",
                "estimated_confirmation_lag_trading_days": confirmation_lag,
                "purchase_status_verified": snapshot.get("purchase_status") if action == "SUBSCRIBE" else None,
                "redemption_status_verified": snapshot.get("redemption_status") if action == "REDEEM" else None,
                "fee_rate_verified": rate,
                "plan_type": plan_type,
                "execution_readiness": execution_readiness,
                "pre_trade_confirmation_required": bool(pre_trade_text),
                "pre_trade_requirements": pre_trade_text or "提交前仍需核对销售平台申赎状态、费用和截止时间",
                "manual_approval_required": True,
                "order_sent": False,
            }
        )
    return pd.DataFrame(rows)


def write_fund_trade_plan(plan: pd.DataFrame, as_of_date: str) -> Path:
    output = project_path("output", "fund_trade_plans")
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"fund_trade_plan_{as_of_date}.csv"
    plan.to_csv(path, index=False, encoding="utf-8-sig")
    return path
