"""V4.6 TACTICAL_TREND 子策略。

趋势跟随策略，适用于 TREND_RISK_ON 和 RECOVERY_PROBE 市场状态。
使用多周期动量、趋势质量和横截面排名。

BREAKOUT 和 MEAN_REVERSION 仅提供接口定义，首版不进入实盘。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from quant.models import Regime
from .ranking import rank_universe, RankedETF


@dataclass
class TacticalSubStrategyResult:
    """战术子策略输出。"""
    strategy_name: str
    ranked_etfs: list[RankedETF]
    selected_symbols: list[str]
    target_weights: dict[str, float]
    reason: str = ""
    regime_note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_name": self.strategy_name,
            "selected_symbols": list(self.selected_symbols),
            "target_weights": dict(self.target_weights),
            "reason": self.reason,
            "regime_note": self.regime_note,
            "ranked": [r.to_dict() for r in self.ranked_etfs[:5]],
        }


def run_tactical_trend(
    frames: dict[str, pd.DataFrame],
    pool: list[str],
    settings: dict | None = None,
    regime: Regime | None = None,
    max_positions: int = 3,
    max_single_weight: float = 0.5,
    cash_symbol: str = "CASH",
) -> TacticalSubStrategyResult:
    """执行 TACTICAL_TREND 策略。

    适用于：TREND_RISK_ON, RECOVERY_PROBE
    在 RANGE_NEUTRAL 下降低参与，STRESS_RISK_OFF 下停止。
    """
    from quant.portfolio import cap_single_weights

    if regime == Regime.RISK_OFF:
        return TacticalSubStrategyResult(
            strategy_name="TACTICAL_TREND",
            ranked_etfs=[],
            selected_symbols=[],
            target_weights={cash_symbol: 1.0},
            reason="Risk-Off: 不执行趋势策略",
            regime_note="STRESS_RISK_OFF",
        )

    # 排名
    min_momentum = 0.0 if regime == Regime.RISK_ON else 0.02
    ranked = rank_universe(
        frames, pool, settings,
        max_results=max_positions * 2,  # 选择更多用于后备
        regime=regime,
        min_absolute_momentum=min_momentum,
    )

    # 筛选符合条件的
    selected = [r for r in ranked if r.eligible][:max_positions]

    if not selected:
        return TacticalSubStrategyResult(
            strategy_name="TACTICAL_TREND",
            ranked_etfs=ranked,
            selected_symbols=[],
            target_weights={cash_symbol: 1.0},
            reason="无符合条件的趋势资产",
            regime_note=regime.value if regime else "UNKNOWN",
        )

    # 逆下行波动率加权 + 集中度上限（超额转为现金，不静默加杠杆）
    # 使用 quant.portfolio.cap_single_weights 保证归一后单标的权重仍 <= max_single_weight。
    symbols = [r.symbol for r in selected]
    weight_map = {r.symbol: max(r.score, 0.001) for r in selected}
    total = sum(weight_map.values())
    raw = {sym: val / total for sym, val in weight_map.items()}
    weights = cap_single_weights(raw, max_single_weight, cash_symbol)

    regime_note_map = {
        Regime.RISK_ON: "TREND_RISK_ON — 正常趋势跟随",
        Regime.NEUTRAL: "RANGE_NEUTRAL — 减少趋势暴露",
        Regime.RISK_OFF: "STRESS_RISK_OFF — 不执行",
    }

    return TacticalSubStrategyResult(
        strategy_name="TACTICAL_TREND",
        ranked_etfs=ranked,
        selected_symbols=symbols,
        target_weights=weights,
        reason=f"趋势跟随: 选取 {len(selected)} 只 ETF",
        regime_note=regime_note_map.get(regime or Regime.NEUTRAL, "UNKNOWN"),
    )


# ── BREAKOUT 和 MEAN_REVERSION 接口（首版仅接口） ──────────

def run_tactical_breakout(
    frames: dict[str, pd.DataFrame],
    pool: list[str],
    settings: dict | None = None,
    regime: Regime | None = None,
) -> TacticalSubStrategyResult:
    """TACTICAL_BREAKOUT 策略接口。

    首版仅返回未实现提示。
    """
    return TacticalSubStrategyResult(
        strategy_name="TACTICAL_BREAKOUT",
        ranked_etfs=[],
        selected_symbols=[],
        target_weights={},
        reason="BREAKOUT 策略尚未实现 — 预留接口",
        regime_note="NOT_IMPLEMENTED",
    )


def run_tactical_mean_reversion(
    frames: dict[str, pd.DataFrame],
    pool: list[str],
    settings: dict | None = None,
    regime: Regime | None = None,
) -> TacticalSubStrategyResult:
    """TACTICAL_MEAN_REVERSION 策略接口。

    首版仅返回未实现提示。
    """
    return TacticalSubStrategyResult(
        strategy_name="TACTICAL_MEAN_REVERSION",
        ranked_etfs=[],
        selected_symbols=[],
        target_weights={},
        reason="MEAN_REVERSION 策略尚未实现 — 预留接口",
        regime_note="NOT_IMPLEMENTED",
    )
