from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import project_path


def _action_line(status: str, trade_plan_path: str | None, execution_readiness: str) -> str:
    if execution_readiness == "ACCOUNT_MAPPING_REQUIRED":
        return "**今天不依据本报告申赎。** 当前仍是旧示例代码，先完成一次真实 A/C/LOF 代码与持仓映射；以下策略目标仅作研究参考。"
    if status == "DATA_BLOCKED":
        return "**今天不申购、不赎回、不调仓。** 只有基准基金或明确全局阻断项的数据异常才会出现该状态。"
    if status == "MIGRATION_REQUIRED":
        return "**今天不操作。** 先在总控台菜单中更新真实持仓状态。"
    if status == "HOLD":
        return "**今天保持原仓。** 未到建议复核节点，或偏离不足以覆盖申赎成本。"
    if status == "HOLD_RECHECK":
        return "**今天不调仓。** 信号、时点或数据尚不足以支持换仓。"
    if status in {"REBALANCE_CANDIDATE", "RISK_OVERRIDE"}:
        if execution_readiness == "PRE_TRADE_CONFIRMATION_REQUIRED":
            return "**策略已形成调整候选。** 已生成待确认策略计划；提交前只需在销售平台核对申赎开放、实际份额费率与截止时间，系统没有发送订单。"
        if trade_plan_path:
            return "**仅生成待人工审核的申赎计划。** 未向任何基金销售平台发送订单。"
        return "**出现可复核候选。** 因账户映射或执行条件未完成，尚未生成真实申赎计划。"
    return "**今天不操作。** 请查看决策原因。"


def _weight_lines(weights: dict[str, Any]) -> list[str]:
    return [f"- {key}: {float(value):.2%}" for key, value in weights.items()]


def write_fund_decision_report(payload: dict[str, Any], as_of_date: str) -> Path:
    output = project_path("output", "fund_decisions")
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / f"fund_decision_{as_of_date}.json"
    md_path = output / f"fund_decision_{as_of_date}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    status = str(payload["status"])
    trade_plan_path = payload.get("trade_plan_path")
    execution_readiness = str(payload.get("execution_readiness", "NOT_CHECKED"))
    strategy_confidence = str(payload.get("strategy_confidence", payload.get("confidence", "N/A")))
    next_review = payload.get("next_review") or {}
    mapping_confirmed = bool((payload.get("account_profile") or {}).get("mapping_confirmed", True))

    target_title = "## 理论策略目标（非交易指令）"
    if status in {"REBALANCE_CANDIDATE", "RISK_OVERRIDE"} and trade_plan_path:
        target_title = "## 本轮策略目标（计划仍需人工确认）"

    lines = [
        f"# 场外基金专用决策报告｜{as_of_date}",
        "",
        "- 模式：**FUND_NAV_ONLY**（同基金直接累计净值信号、回测与交易）",
        f"- 决策状态：**{status}**",
        f"- 市场状态：**{payload['regime']['regime']}**",
        f"- 策略信号置信度：**{strategy_confidence}**",
        f"- 执行准备状态：**{execution_readiness}**",
        f"- 理论目标换手率：**{payload['estimated_turnover']:.2%}**" + ("（当前可生成计划）" if trade_plan_path else "（当前不可直接执行）"),
        f"- 预计已核实费率成本（权重口径）：**{payload['estimated_cost']:.4%}**" if payload.get("estimated_cost_verified", False) else "- 预计成本：**待销售平台确认，不作为策略方向的阻断条件**",
        "",
        "## 今天可执行动作",
        f"- {_action_line(status, trade_plan_path, execution_readiness)}",
        "",
        "## 下一次建议运行",
    ]
    if next_review:
        lines.extend([
            f"- 建议时间：**{next_review.get('suggested_run_at', 'N/A')}**",
            f"- 复核节奏：等待约 **{next_review.get('interval_nav_observations', 'N/A')}** 个新的基金净值观测",
            f"- 原因：{next_review.get('reason', 'N/A')}",
            f"- 说明：{next_review.get('calendar_disclaimer', '')}",
        ])
    else:
        lines.append("- 当前无法计算下一次建议运行时间。")

    if not mapping_confirmed:
        lines.extend([
            "",
            "## 真实账户映射提示（一次性设置）",
            "- 当前候选池仍保存旧示例代码；你的真实 A/C/LOF 代码、份额类别、当前权重和持有天数尚未写入策略配置。",
            "- 在完成映射前，本报告的方向只能作为研究参考，不能对应真实账户执行。",
            "- 请运行 `main_run.py --mode menu`，选择“6. 一次性配置真实混合份额基金账户”。",
        ])

    lines.extend(["", "## 决策原因"])
    lines.extend([f"- {item}" for item in payload.get("reasons", [])])

    isolation = payload.get("quality_isolation", {}) or {}
    quarantined = isolation.get("quarantined_symbols", [])
    if quarantined:
        lines.extend([
            "",
            "## 已隔离的数据异常（不作为本轮新增或赎回依据）",
            f"- 隔离基金：{', '.join(quarantined)}",
            "- 处理规则：该基金不参与本轮排名、不新增申购；若你实际持有，则其当前权重被冻结，不会依据异常数据生成赎回指令。",
            f"- 最新日历组信号日期：{isolation.get('latest_signal_date') or 'N/A'}",
        ])
        frozen = isolation.get("frozen_holdings", {}) or {}
        if frozen:
            lines.extend([f"- 已冻结持仓：{symbol} {float(weight):.2%}" for symbol, weight in frozen.items()])

    alignment = payload.get("calendar_group_alignment") or {}
    if alignment.get("signal_dates"):
        # Each calendar group keeps its own signal date; there is deliberately no single
        # "common signal date" that would hide a lagging group behind a fresh one.
        lines.extend([
            "",
            "## 日历分组信号时效",
            f"- 对齐口径：{alignment.get('convention', 'N/A')}（组内取共同信号日，跨组互不截断）",
        ])
        ages = alignment.get("signal_age_days", {}) or {}
        stale = set(alignment.get("stale_groups", []) or [])
        for group in sorted(alignment["signal_dates"]):
            members = ", ".join(alignment.get("symbols", {}).get(group, []))
            flag = "（超过跨组滞后上限，已降级隔离）" if group in stale else ""
            lines.append(
                f"- {group} 组：信号日 {alignment['signal_dates'][group]}｜"
                f"较最新组滞后 {ages.get(group, 0)} 个自然日{flag}｜成员 {members}"
            )
        lines.append(
            f"- 跨组最大滞后 {alignment.get('max_cross_group_lag_calendar_days', 0)} 个自然日，"
            f"上限 {alignment.get('lag_threshold_calendar_days', 'N/A')} 个自然日"
        )

    refresh = payload.get("data_refresh", [])
    if refresh:
        lines.extend(["", "## 数据更新"] + [
            f"- {item.get('symbol')}｜{item.get('status')}｜{item.get('message')}"
            for item in refresh
        ])

    lines.extend([
        "",
        target_title,
        *_weight_lines(payload.get("champion_weights", {})),
        "",
        "## Challenger（影子 HRP）权重",
        *_weight_lines(payload.get("challenger_weights", {})),
        "",
        "## 同质化雷达（净值收益相关性）",
        f"- 相关性上限：{payload.get('correlation_limit', 'N/A')}",
        "- 说明：只有相关系数未超过上限的候选，才可与已选基金同时进入组合。",
    ])
    corr = payload.get("correlation_matrix", {}) or {}
    selected = payload.get("selected_symbols", []) or []
    pair_lines = []
    for idx, left in enumerate(selected):
        for right in selected[idx + 1:]:
            value = (corr.get(left, {}) or {}).get(right)
            if value is not None:
                pair_lines.append(f"- {left} 与 {right}：{float(value):.4f}")
    lines.extend(pair_lines or ["- 本轮仅一只入选基金，或暂无可计算的成对相关性。"])
    lines.extend([
        "",
        "## 数据来源与降级检查",
        "- 本轮信号只使用同代码直接累计净值；不会以 ETF、同类基金或缺失字段作代理。",
        "- 网络刷新失败时，只有本地同代码缓存通过时效与质量校验才允许继续分析；否则系统阻断或隔离。",
        "",
        "## 执行前核对清单",
        *[f"- {item}" for item in payload.get("fund_execution_constraints", [])],
        "",
        "## 信号明细",
    ])
    for item in payload.get("signals", []):
        score = item.get("score")
        score_text = "N/A" if score is None else f"{score:.4f}"
        lines.append(f"- {item['symbol']} {item['name']}｜eligible={item['eligible']}｜score={score_text}｜原因：{'；'.join(item['reasons']) or '通过'}")
    lines.extend([
        "",
        "## 安全边界",
        "- 场外基金分支不使用 ETF 代理、成交量、OHLCV、ATR 或日内价格。",
        "- T 日净值披露后形成信号；申赎需在下一基金可申赎日人工提交，并按销售平台规则以未知净值定价。",
        "- C 类、A 类及 LOF/QDII 的费率均以销售平台当日规则为准；研究假设不能替代真实费用核对。",
        "- 申购/赎回状态和费率缺失时，系统仍可保留策略方向，但只生成待确认计划，绝不自动下单。",
        "- 本系统只生成计划与纸面模拟，绝不向券商或基金销售平台下单。",
    ])
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path
