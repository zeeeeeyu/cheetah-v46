"""V4.6 战术 ETF 资产池管理。

三级池：RESEARCH → ELIGIBLE → LIVE。
ETF 进入实盘候选池必须满足流动性、规模、数据完整性等条件。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from quant.domain.enums import AssetType


# ── 初始 ETF 研究池 ────────────────────────────────────────

INITIAL_RESEARCH_UNIVERSE = {
    # 大盘
    "510300": "沪深300ETF",
    "510050": "上证50ETF",
    "159919": "沪深300ETF(嘉实)",
    "510310": "沪深300ETF(易方达)",
    # 中盘
    "510500": "中证500ETF",
    "512500": "中证500ETF(华夏)",
    # 小盘
    "159845": "中证1000ETF",
    "512100": "中证1000ETF(南方)",
    # 创业板
    "159915": "创业板ETF",
    "159952": "创业板ETF(广发)",
    # 科创板
    "588000": "科创50ETF",
    "588080": "科创50ETF(易方达)",
    # 红利
    "510880": "红利ETF",
    "515180": "红利ETF(易方达)",
    # 价值
    "510030": "价值ETF",
    # 成长
    "159967": "成长ETF",
    # 军工
    "512660": "军工ETF",
    "512710": "军工ETF(龙头)",
    # 半导体
    "512480": "半导体ETF",
    "159813": "半导体ETF(芯片)",
    # 新能源
    "515030": "新能源车ETF",
    "159857": "光伏ETF",
    # 机器人
    "562500": "机器人ETF",
    # 医药
    "512010": "医药ETF",
    "159929": "医药ETF(汇添富)",
    # 消费
    "159928": "消费ETF",
    "510150": "消费ETF(招商)",
    # 金融
    "510230": "金融ETF",
    "512800": "银行ETF",
    # 有色
    "512400": "有色金属ETF",
    # 黄金
    "518880": "黄金ETF",
    "159934": "黄金ETF(易方达)",
    # 债券
    "511010": "国债ETF",
    "511880": "银华日利(货币)",
    # 跨境
    "513100": "纳指ETF",
    "513050": "中概互联ETF",
    "159941": "纳指ETF(广发)",
    # 科技
    "515000": "科技ETF",
    "159807": "科技ETF(易方达)",
}


@dataclass
class ETFInfo:
    symbol: str
    name: str
    asset_class: str = "equity_sector"
    group: str = "equity"

    @classmethod
    def from_symbol(cls, symbol: str) -> ETFInfo:
        name = INITIAL_RESEARCH_UNIVERSE.get(symbol, symbol)
        return cls(symbol=symbol, name=name)


@dataclass
class EligibilityCriteria:
    min_history_days: int = 252
    min_median_turnover_20d: float = 10_000_000  # 1000万
    min_fund_size: float = 100_000_000           # 1亿
    max_bid_ask_spread: float = 0.005            # 0.5%
    max_premium_discount: float = 0.02           # 2%
    max_lot_amount: float = 50_000               # MICRO 账户一手最大 5万
    min_data_completeness: float = 0.95          # 95%


@dataclass
class EligibilityResult:
    symbol: str
    eligible: bool
    reasons: list[str] = field(default_factory=list)
    median_turnover: float | None = None
    history_days: int = 0


def check_eligibility(
    symbol: str,
    frame: pd.DataFrame,
    criteria: EligibilityCriteria | None = None,
    account_mode: str = "MICRO",
) -> EligibilityResult:
    """检查 ETF 是否符合准入条件。"""
    c = criteria or EligibilityCriteria()
    reasons: list[str] = []

    if frame is None or frame.empty:
        return EligibilityResult(symbol, False, ["无数据"], history_days=0)

    history = len(frame)
    if history < c.min_history_days:
        reasons.append(f"历史长度 {history} < {c.min_history_days}")

    if "turnover" in frame.columns:
        median_turnover = float(frame["turnover"].tail(20).median())
        if median_turnover < c.min_median_turnover_20d:
            reasons.append(f"20日成交额中位数 {median_turnover:.0f} < {c.min_median_turnover_20d:.0f}")
    else:
        median_turnover = 0.0
        reasons.append("缺少成交额数据")

    if account_mode == "MICRO":
        if "close" in frame.columns:
            last_price = float(frame["close"].iloc[-1])
            lot_amount = last_price * 100
            if lot_amount > c.max_lot_amount:
                reasons.append(f"一手金额 {lot_amount:.0f} > {c.max_lot_amount:.0f}（MICRO 账户限制）")

    return EligibilityResult(
        symbol=symbol,
        eligible=len(reasons) == 0,
        reasons=reasons,
        median_turnover=median_turnover if "median_turnover" in dir() else None,
        history_days=history,
    )


def classify_universe(
    research_pool: dict[str, str],
    frames: dict[str, pd.DataFrame],
    criteria: EligibilityCriteria | None = None,
    account_mode: str = "MICRO",
    max_live_etfs: int = 20,
) -> tuple[list[str], list[str], list[str]]:
    """将研究池分类为三级。

    Returns:
        (research, eligible, live) — 三个分层池的 symbol 列表
    """
    c = criteria or EligibilityCriteria()
    eligible_list: list[str] = []
    live_list: list[str] = []

    for symbol in research_pool:
        frame = frames.get(symbol)
        result = check_eligibility(symbol, frame, c, account_mode)
        if result.eligible:
            eligible_list.append(symbol)
            if len(live_list) < max_live_etfs:
                # Further filter: prefer higher turnover
                live_list.append(symbol)

    # Sort live by turnover
    live_list.sort(
        key=lambda s: float(frames[s]["turnover"].tail(20).median()) if s in frames and frames[s] is not None and "turnover" in frames[s].columns else 0,
        reverse=True,
    )
    live_list = live_list[:max_live_etfs]

    research_list = list(research_pool.keys())
    return research_list, eligible_list, live_list
