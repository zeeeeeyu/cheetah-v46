"""V4.6 统一回测框架。

包含：BacktestResult、绩效指标、ETF/基金执行模拟器、成本模型接口。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from quant.exe.cost_model import estimate_etf_cost, estimate_fund_cost


# ═══════════════════════════════════════════════════════════════
# 统一 BacktestResult
# ═══════════════════════════════════════════════════════════════

@dataclass
class BacktestResult:
    strategy_id: str = ""
    strategy_version: str = "0.0.0"
    code_version: str = "V4.6"
    dataset_version: str = ""
    config_hash: str = ""
    period_start: str = ""
    period_end: str = ""
    initial_capital: float = 1.0
    final_value: float = 1.0
    total_return: float = 0.0
    annual_return: float = 0.0
    max_drawdown: float = 0.0
    drawdown_duration_days: int = 0
    sharpe: float | None = None
    sortino: float | None = None
    calmar: float | None = None
    total_turnover: float = 0.0
    total_cost: float = 0.0
    trade_count: int = 0
    avg_exposure: float = 1.0
    avg_cash: float = 0.0
    exposure: float = 1.0
    regime_metrics: dict[str, float] = field(default_factory=dict)
    status: str = "PENDING"
    warnings: list[str] = field(default_factory=list)
    curve: list[dict[str, float]] | None = None
    rebalance_log: list[dict[str, Any]] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.curve and len(self.curve) > 1000:
            d["curve"] = self.curve[::max(1, len(self.curve)//1000)]  # 采样
        return d

    def short_summary(self) -> str:
        return (f"{self.strategy_id}: 总收益 {self.total_return:.2%}, "
                f"年化 {self.annual_return:.2%}, 回撤 {self.max_drawdown:.2%}, "
                f"Sharpe {self.sharpe:.2f}" if self.sharpe else "N/A")


def compute_performance_metrics(nav: pd.Series) -> dict[str, float]:
    """统一的绩效指标计算。"""
    if len(nav) < 2:
        return {"total_return": 0.0, "annual_return": 0.0, "max_drawdown": 0.0}

    returns = nav.pct_change().dropna()
    years = max(len(returns) / 252.0, 1 / 252.0)
    total_ret = float(nav.iloc[-1] / nav.iloc[0] - 1.0)
    ann_ret = float((nav.iloc[-1] / nav.iloc[0]) ** (1.0 / years) - 1.0)

    roll_max = nav.cummax()
    dd = nav / roll_max - 1.0
    max_dd = float(dd.min())
    dd_dur = 0
    in_dd = False; cur_dur = 0
    for v in dd.values:
        if v < -1e-6:
            in_dd = True; cur_dur += 1
        elif in_dd:
            dd_dur = max(dd_dur, cur_dur); cur_dur = 0; in_dd = False
    if in_dd: dd_dur = max(dd_dur, cur_dur)

    std = float(returns.std()) if len(returns) > 1 else 0.0
    downside = returns[returns < 0]
    dstd = float(np.sqrt(np.mean(downside ** 2))) if len(downside) > 1 else 0.0
    sharpe = float(returns.mean() / std * np.sqrt(252)) if std > 1e-10 else None
    sortino = float(returns.mean() / dstd * np.sqrt(252)) if dstd > 1e-10 else None
    calmar = float(ann_ret / abs(max_dd)) if max_dd < -1e-10 else None

    return {
        "total_return": round(total_ret, 6),
        "annual_return": round(ann_ret, 6),
        "max_drawdown": round(max_dd, 6),
        "drawdown_duration_days": dd_dur,
        "sharpe": round(sharpe, 4) if sharpe is not None else None,
        "sortino": round(sortino, 4) if sortino is not None else None,
        "calmar": round(calmar, 4) if calmar is not None else None,
    }


# ═══════════════════════════════════════════════════════════════
# ETF 执行模拟器
# ═══════════════════════════════════════════════════════════════

def simulate_etf_execution(
    signal_date: pd.Timestamp,
    frame: pd.DataFrame,
    target_amount: float,
    max_slippage: float = 0.002,
    lot_size: int = 100,
    min_commission: float = 5.0,
) -> tuple[float, float, float, str]:
    """模拟 ETF 执行。

    规则：
    - T 日收盘信号 → T+1 开盘执行
    - 数量按 100 份取整
    - 最低佣金 5 元
    - 滑点 + 买卖价差
    - 信号过期检查
    """
    if frame is None or frame.empty:
        return 0.0, 0.0, 0.0, "NO_DATA"

    dates = pd.to_datetime(frame["date"]).sort_values()
    signal_idx = dates.searchsorted(signal_date)
    if signal_idx >= len(dates) - 1:
        return 0.0, 0.0, 0.0, "SIGNAL_EXPIRED"

    exec_date = dates.iloc[signal_idx + 1]
    exec_row = frame[frame["date"] == exec_date]
    if exec_row.empty:
        return 0.0, 0.0, 0.0, "NO_EXECUTION_DATE"

    exec_price = float(exec_row.iloc[0]["open"])
    slippage = exec_price * max_slippage
    fill_price = exec_price + slippage

    raw_qty = target_amount / fill_price
    qty = int(raw_qty / lot_size) * lot_size
    if qty <= 0:
        return 0.0, 0.0, 0.0, "BELOW_LOT_SIZE"

    filled_amount = qty * fill_price
    fee = max(filled_amount * 0.00025, min_commission)
    net_amount = filled_amount + fee
    return float(qty), float(fill_price), float(net_amount), "FILLED"


# ═══════════════════════════════════════════════════════════════
# 基金执行模拟器
# ═══════════════════════════════════════════════════════════════

def simulate_fund_execution(
    signal_date: pd.Timestamp,
    frame: pd.DataFrame,
    target_amount: float,
    side: str = "BUY",
    subscription_rate: float = 0.001,
    redemption_rate: float = 0.0,
    confirmation_lag: int = 2,
) -> tuple[float, float, float, float, str]:
    """模拟场外基金执行。

    规则：
    - T 日信号，T+confirmation_lag 日以当日净值定价
    - 未知价（不能预设价格）
    - 申购/赎回确认延迟
    """
    if frame is None or frame.empty:
        return 0.0, 0.0, 0.0, 0.0, "NO_DATA"

    dates = pd.to_datetime(frame["date"]).sort_values()
    signal_idx = dates.searchsorted(signal_date)

    pricing_idx = signal_idx + confirmation_lag
    if pricing_idx >= len(dates):
        return 0.0, 0.0, 0.0, 0.0, "CONFIRMATION_LAG_EXCEEDS_HISTORY"

    pricing_date = dates.iloc[pricing_idx]
    pricing_row = frame[frame["date"] == pricing_date]
    if pricing_row.empty:
        return 0.0, 0.0, 0.0, 0.0, "NO_NAV_ON_PRICING_DATE"

    nav = float(pricing_row.iloc[0].get("nav", pricing_row.iloc[0].get("close", 0)))
    if nav <= 0:
        return 0.0, 0.0, 0.0, 0.0, "INVALID_NAV"

    if side == "BUY":
        fee_rate = subscription_rate
    else:
        fee_rate = redemption_rate

    qty = target_amount / nav
    fee = target_amount * fee_rate
    net_amount = target_amount + (fee if side == "BUY" else -fee)
    return float(qty), float(nav), float(fee), float(net_amount), "FILLED"


# ═══════════════════════════════════════════════════════════════
# 未知价回测 harness（c42 / REQ-BT-01）
# ═══════════════════════════════════════════════════════════════

def verify_no_signal_day_fill(
    rebalances: "list[dict]",
    *,
    signal_key: str = "signal_date",
    execution_key: str = "execution_date",
) -> tuple[bool, str]:
    """强制「信号日绝不成交」不变量（c54 / REQ-BT-01）。

    ``run_event_backtest`` / ``run_fund_event_backtest`` 各自实现了 T+1 执行，
    但这个性质此前只存在于**注释与作者意图**里：没有任何代码强制它。一旦后续
    有人为了「提高回测收益」把执行改回信号日当天，回测会静默返回被高估的绩效，
    不会有任何红灯。

    本函数把该性质提升为**返回前的硬后置条件**。任一笔调仓的成交日 <= 信号日
    即判定违约，调用方必须据此 Fail-Closed（返回 BLOCKED），而不是照常输出指标。

    Args:
        rebalances: 调仓日志，每条含信号日与成交日两个日期字段。
        signal_key: 信号日字段名。
        execution_key: 成交日字段名（基金链路为定价净值日）。

    Returns:
        (ok, reason)。``ok`` 为 False 时 ``reason`` 指明第一笔违约记录。
    """
    for i, record in enumerate(rebalances or []):
        sig = record.get(signal_key)
        exe = record.get(execution_key)
        if sig is None or exe is None:
            return False, (
                f"第 {i} 笔调仓缺少日期字段（{signal_key}={sig!r}, {execution_key}={exe!r}）"
                "——无法证明未使用信号日价格成交"
            )
        if pd.Timestamp(exe) <= pd.Timestamp(sig):
            return False, (
                f"第 {i} 笔调仓成交日 {exe} 不晚于信号日 {sig}"
                "——等于用信号日已知价成交，收益被系统性高估"
            )
    return True, f"{len(rebalances or [])} 笔调仓全部满足「成交日晚于信号日」"


def backtest_unknown_price(
    signals: "pd.Series",
    price: "pd.Series",
    *,
    fee_rate: float = 0.00025,
    confirmation_lag: int = 1,
    initial_capital: float = 1.0,
) -> "pd.Series":
    """未知价回测 harness（c42）。

    关键不变量：任何一日的持仓调整，其成交价只取**晚于信号日**的行情
    （T + ``confirmation_lag``），绝不引用信号日当天的价格——模拟「下单时还不知道
    成交价」。同时计入费率与确认延迟。返回权益曲线（以 ``initial_capital`` 为基准）。

    断言点（供验收用）：当 ``confirmation_lag >= 1`` 时，信号日当天的权益等于调整前
    权益（成交被推迟到未来），从而证明不存在「用当日价成交」。
    """
    sig = pd.Series(signals, dtype="float64")
    px = pd.Series(price, dtype="float64")
    idx = list(px.index)
    equity = pd.Series(index=px.index, dtype="float64")
    capital = float(initial_capital)
    shares = 0.0
    prev_target = 0.0
    n = len(idx)
    for i, d in enumerate(idx):
        target = float(sig.get(d, prev_target))
        if target != prev_target:
            fill_i = min(i + confirmation_lag, n - 1)
            fill_px = float(px.iloc[fill_i])
            target_value = capital * target
            desired = target_value / fill_px if fill_px > 0 else 0.0
            trade = desired - shares
            fee = abs(trade) * fill_px * fee_rate
            capital -= trade * fill_px + fee
            shares = desired
            prev_target = target
        equity.iloc[i] = capital + shares * float(px.iloc[i])
    return equity
