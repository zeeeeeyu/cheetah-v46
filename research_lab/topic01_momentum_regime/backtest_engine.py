"""Topic-01 回测引擎：T+1 / Point-in-Time / 成本模型。

严格复用 V4.6 时间语义：
- 信号在 T 日收盘后生成（仅用 <= T 的数据）；成交在 T+1 开盘。
- 复用 quant.research.backtest.verify_no_signal_day_fill 作为硬后置条件（c54 不变量）。
- 复用 quant.research.backtest.compute_performance_metrics 计算基础绩效。

成本模型（Topic-01 §七）：
- zero：无成本（diagnostic）
- conservative：ETF 佣金 0.025% + 滑点 0.2%（PROVISIONAL）
- fund-realism：基金层专用，第一阶段不使用

禁止：信号日用信号日价格成交；未来数据泄漏；为提升收益改回信号日成交。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

# 只读复用生产回测基建（不修改生产）
from quant.research.backtest import verify_no_signal_day_fill, compute_performance_metrics

from .reference_strategy import ReferenceConfig, generate_target_weights

COST_PRESETS = {
    "zero": 0.0,
    "conservative": 0.00225,  # 0.025% 佣金 + 0.2% 滑点
    "fund-realism": 0.005,
}


@dataclass
class BacktestOutput:
    strategy_id: str
    config_hash: str
    code_version: str
    dataset_version: str
    period_start: str
    period_end: str
    initial_capital: float
    final_value: float
    total_return: float
    annual_return: float
    max_drawdown: float
    sharpe: float | None
    sortino: float | None
    calmar: float | None
    cvar_95: float | None
    total_turnover: float
    trade_count: int
    avg_exposure: float
    avg_cash: float
    cost_model: str
    status: str
    warnings: list[str] = field(default_factory=list)
    rebalances: list[dict[str, Any]] = field(default_factory=list)
    curve: list[dict[str, float]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def _cvar_95(returns: pd.Series) -> float | None:
    if len(returns) < 10:
        return None
    a = np.sort(returns.to_numpy(dtype=float))
    k = max(1, int(0.05 * len(a)))
    return float(a[:k].mean())


def run_strategy_backtest(
    frames: dict[str, pd.DataFrame],
    pool: list[str],
    config: ReferenceConfig,
    *,
    strategy_id: str = "ref-mtr",
    code_version: str = "ref-mtr-v1",
    dataset_version: str = "",
    cost_model: str = "conservative",
    rebalance_freq: int = 5,
    config_hash: str = "",
) -> BacktestOutput:
    cost_rate = COST_PRESETS.get(cost_model, COST_PRESETS["conservative"])

    base = next(iter(frames.values()))
    dates = list(base["date"])
    warmup = max(config.horizons) + 1
    if len(dates) <= warmup + 1:
        return BacktestOutput(
            strategy_id=strategy_id, config_hash=config_hash, code_version=code_version,
            dataset_version=dataset_version, period_start=dates[0] if dates else "",
            period_end=dates[-1] if dates else "", initial_capital=1.0, final_value=1.0,
            total_return=0.0, annual_return=0.0, max_drawdown=0.0, sharpe=None,
            sortino=None, calmar=None, cvar_95=None, total_turnover=0.0, trade_count=0,
            avg_exposure=0.0, avg_cash=1.0, cost_model=cost_model, status="INSUFFICIENT_DATA",
            warnings=["历史长度不足以覆盖最长周期 + 至少一个成交日"],
        )

    signal_idx = list(range(warmup, len(dates) - 1, rebalance_freq))
    signal_dates = {dates[i]: i for i in signal_idx}
    px = {s: f.set_index("date") for s, f in frames.items()}

    cash = 1.0
    holdings = {s: 0.0 for s in pool}
    rebalances: list[dict[str, Any]] = []
    pending_exec: dict[str, Any] | None = None
    curve: list[dict[str, float]] = []
    total_turnover = 0.0
    trade_count = 0
    exposures: list[float] = []

    for idx, d in enumerate(dates):
        # ── T+1 开盘执行待定调仓 ──
        if pending_exec is not None and pending_exec["execution_date"] == d:
            target = pending_exec["target"]
            port_value = cash + sum(
                holdings[s] * float(px[s].loc[d, "close"]) for s in pool if d in px[s].index
            )
            for s in pool:
                if d not in px[s].index:
                    continue
                open_px = float(px[s].loc[d, "open"])
                if open_px <= 0:
                    continue
                tgt_val = port_value * target.get(s, 0.0)
                cur_val = holdings[s] * open_px
                delta_val = tgt_val - cur_val
                if abs(delta_val) < 1e-9:
                    continue
                qty = delta_val / open_px
                fee = abs(delta_val) * cost_rate
                cash -= delta_val + (fee if delta_val > 0 else -fee)
                holdings[s] += qty
                total_turnover += abs(delta_val)
                trade_count += 1
            pending_exec = None

        # ── T 日收盘后生成信号 ──
        if d in signal_dates:
            as_of = d
            target = generate_target_weights(frames, pool, as_of, config)
            exec_d = dates[idx + 1]  # T+1
            pending_exec = {"signal_date": d, "execution_date": exec_d, "target": target}
            rebalances.append({
                "signal_date": d, "execution_date": exec_d,
                "target": dict(target),
                "longs": [s for s, w in target.items() if w > 0 and s != config.cash_symbol],
            })

        # ── 收盘市值 ──
        val = cash + sum(
            holdings[s] * float(px[s].loc[d, "close"]) for s in pool if d in px[s].index
        )
        invested = sum(
            holdings[s] * float(px[s].loc[d, "close"]) for s in pool if d in px[s].index
        )
        exp = invested / val if val > 0 else 0.0
        exposures.append(exp)
        curve.append({"date": d, "value": float(val), "exposure": float(exp)})

    # ── 硬后置条件：c54「信号日绝不成交」──
    ok, reason = verify_no_signal_day_fill(rebalances)
    if not ok:
        return BacktestOutput(
            strategy_id=strategy_id, config_hash=config_hash, code_version=code_version,
            dataset_version=dataset_version, period_start=dates[0], period_end=dates[-1],
            initial_capital=1.0, final_value=1.0, total_return=0.0, annual_return=0.0,
            max_drawdown=0.0, sharpe=None, sortino=None, calmar=None, cvar_95=None,
            total_turnover=0.0, trade_count=0, avg_exposure=0.0, avg_cash=1.0,
            cost_model=cost_model, status="BLOCKED", warnings=[reason],
            rebalances=rebalances, curve=curve,
        )

    nav = pd.Series([c["value"] for c in curve], index=[c["date"] for c in curve])
    m = compute_performance_metrics(nav)
    rets = nav.pct_change().dropna()
    cvar = _cvar_95(rets)
    avg_exp = float(np.mean(exposures)) if exposures else 0.0
    avg_cash = 1.0 - avg_exp

    return BacktestOutput(
        strategy_id=strategy_id, config_hash=config_hash, code_version=code_version,
        dataset_version=dataset_version, period_start=curve[0]["date"], period_end=curve[-1]["date"],
        initial_capital=1.0, final_value=float(nav.iloc[-1]),
        total_return=m.get("total_return", 0.0), annual_return=m.get("annual_return", 0.0),
        max_drawdown=m.get("max_drawdown", 0.0), sharpe=m.get("sharpe"),
        sortino=m.get("sortino"), calmar=m.get("calmar"), cvar_95=cvar,
        total_turnover=float(total_turnover), trade_count=trade_count,
        avg_exposure=avg_exp, avg_cash=avg_cash, cost_model=cost_model, status="COMPLETED",
        warnings=[], rebalances=rebalances, curve=curve,
        metadata={"rebalance_count": len(rebalances), "signal_dates": len(signal_dates)},
    )
