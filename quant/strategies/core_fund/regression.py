"""V4.5.1 → V4.6 核心基金引擎回归对比。

在同一日期、同一数据和同一策略参数下，逐项对比新旧引擎的输出。
若不一致，必须输出差异，不能静默接受。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from quant.config import project_path
from quant.fund_pipeline import run_fund_daily
from quant.domain.signals import StrategyDecision as DomainStrategyDecision
from quant.domain.enums import Environment as DomainEnv
from quant.strategies.core_fund.engine import (
    FundStrategicDecision,
    convert_legacy_decision_to_strategic,
    StrategicAction,
)


@dataclass
class RegressionRow:
    """回归对比的每一行。"""
    symbol: str
    date: str
    v451_eligible: bool | None
    v46_eligible: bool | None
    v451_score: float | None
    v46_score: float | None
    v451_weight: float | None
    v46_weight: float | None
    v451_regime: str | None
    v46_regime: str | None
    v451_decision_status: str | None
    v46_strategic_action: str | None
    match: bool
    diff_description: str = ""


@dataclass
class RegressionReport:
    rows: list[RegressionRow] = field(default_factory=list)
    total_mismatches: int = 0
    total_rows: int = 0
    summary: str = ""


def run_regression(
    as_of_date: str | None = None,
) -> RegressionReport:
    """在指定日期运行新旧引擎，输出逐项对比。"""
    from quant.config import load_json
    from quant.fund_universe import FundInstrument, build_fund_signals, validate_fund_catalog
    from quant.fund_datahub import FundNAVRepository, fund_nav_quality_gate, auto_refresh_direct_fund_navs
    from quant.fund_execution import load_fund_state, load_tradeability_snapshot
    from quant.fund_features import nav_frames_as_close
    from quant.fund_regime import fund_nav_regime
    from quant.fund_schedule import review_interval_nav_observations
    from quant.guard import signal_confidence
    from quant.models import DataStatus

    settings = load_json("config/fund/settings.json")
    instruments = [FundInstrument.from_dict(item) for item in load_json("config/fund/instruments.json")]
    validate_fund_catalog(instruments)
    as_of = pd.Timestamp(as_of_date).normalize() if as_of_date else pd.Timestamp.today().normalize()

    # Run V4.5.1 original pipeline (unmodified)
    v451_decision = run_fund_daily(as_of_date=as_of_date)

    # Compute V4.6 strategic equivalent
    state = load_fund_state()
    current_weights = {k: float(v) for k, v in state.get("current_weights", {}).items()}
    v46_strategic = convert_legacy_decision_to_strategic(
        v451_decision, current_weights,
        observation_symbols={"004813"},
    )

    # Build per-symbol comparison
    report = RegressionReport()
    all_symbols = set()
    for sig in v451_decision.signals:
        all_symbols.add(sig.symbol)
    for sym in v46_strategic.target_weights:
        all_symbols.add(sym)

    v451_signal_map = {s.symbol: s for s in v451_decision.signals}

    for symbol in sorted(all_symbols):
        if symbol == "CASH":
            continue

        v451_sig = v451_signal_map.get(symbol)
        v451_eligible = v451_sig.eligible if v451_sig else None
        v451_score = v451_sig.score if v451_sig else None
        v451_weight = v451_decision.target_weights.get(symbol)
        v46_weight = v46_strategic.target_weights.get(symbol)
        v46_action = v46_strategic.strategic_actions.get(symbol)

        # V4.6 eligibility: if strategic action is not HOLD or REVIEW_REQUIRED
        v46_eligible = v46_action in (StrategicAction.STRATEGIC_INCREASE, StrategicAction.STRATEGIC_REDUCE, StrategicAction.EXIT_CANDIDATE)

        diffs: list[str] = []
        if v451_eligible is not None and v46_eligible is not None and v451_eligible != v46_eligible:
            diffs.append(f"eligible: {v451_eligible} → {v46_eligible}")
        if v451_score is not None and v46_weight is not None:
            # Score vs action is not directly comparable — note as informational
            pass
        if v451_weight is not None and v46_weight is not None and abs(v451_weight - v46_weight) > 0.001:
            diffs.append(f"weight: {v451_weight:.4f} → {v46_weight:.4f}")

        match = len(diffs) == 0
        if not match:
            report.total_mismatches += 1

        report.rows.append(RegressionRow(
            symbol=symbol,
            date=v451_decision.as_of_date,
            v451_eligible=v451_eligible,
            v46_eligible=v46_eligible,
            v451_score=v451_score,
            v46_score=None,  # V4.6 doesn't expose raw scores through strategic layer
            v451_weight=v451_weight,
            v46_weight=v46_weight,
            v451_regime=v451_decision.regime.regime.value if hasattr(v451_decision.regime, 'regime') else str(v451_decision.regime),
            v46_regime=v46_strategic.regime_label,
            v451_decision_status=v451_decision.status.value if hasattr(v451_decision.status, 'value') else str(v451_decision.status),
            v46_strategic_action=v46_action.value if v46_action else None,
            match=match,
            diff_description="; ".join(diffs),
        ))

    report.total_rows = len(report.rows)

    # Summary
    if report.total_mismatches == 0:
        report.summary = (
            f"✅ V4.5.1 与 V4.6 在 {as_of.date()} 的决策完全一致。"
            f"共 {report.total_rows} 个资产，0 项差异。"
        )
    else:
        report.summary = (
            f"⚠️ 发现 {report.total_mismatches}/{report.total_rows} 项差异。"
            f"请检查下方逐项差异。"
        )

    return report


def write_regression_csv(
    report: RegressionReport,
    path: Path | None = None,
) -> Path:
    """将回归对比结果写入 CSV。"""
    if path is None:
        path = project_path("output", "regression", "core_fund_v451_vs_v46.csv")
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "symbol", "date", "v451_eligible", "v46_eligible",
            "v451_score", "v46_score", "v451_weight", "v46_weight",
            "v451_regime", "v46_regime", "v451_decision_status",
            "v46_strategic_action", "match", "diff_description",
        ])
        for row in report.rows:
            writer.writerow([
                row.symbol, row.date,
                row.v451_eligible, row.v46_eligible,
                row.v451_score, row.v46_score,
                row.v451_weight, row.v46_weight,
                row.v451_regime, row.v46_regime,
                row.v451_decision_status, row.v46_strategic_action,
                row.match, row.diff_description,
            ])

    return path
