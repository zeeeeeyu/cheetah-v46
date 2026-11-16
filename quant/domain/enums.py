"""V4.6 统一领域枚举。

兼容旧版 quant/models.py 中的枚举，通过别名方式保持向后兼容。
"""

from __future__ import annotations

from enum import Enum
from typing import Final


class Environment(str, Enum):
    """运行环境"""
    BACKTEST = "BACKTEST"
    PAPER = "PAPER"
    LIVE = "LIVE"


class AssetType(str, Enum):
    """资产类型"""
    CASH = "CASH"
    MONEY_MARKET = "MONEY_MARKET"
    BOND = "BOND"
    BROAD_EQUITY = "BROAD_EQUITY"
    STYLE_EQUITY = "STYLE_EQUITY"
    INDUSTRY_EQUITY = "INDUSTRY_EQUITY"
    THEME_EQUITY = "THEME_EQUITY"
    GOLD = "GOLD"
    COMMODITY = "COMMODITY"
    CROSS_BORDER = "CROSS_BORDER"
    ACTIVE_FUND = "ACTIVE_FUND"
    ETF_LINKED_FUND = "ETF_LINKED_FUND"
    ETF = "ETF"


class DataStatus(str, Enum):
    """数据质量状态"""
    PASS = "PASS"
    DEGRADED = "DEGRADED"
    QUARANTINED = "QUARANTINED"
    BLOCKED = "BLOCKED"


class DecisionStatus(str, Enum):
    """决策状态"""
    DATA_BLOCKED = "DATA_BLOCKED"
    MIGRATION_REQUIRED = "MIGRATION_REQUIRED"
    HOLD = "HOLD"
    HOLD_RECHECK = "HOLD_RECHECK"
    REBALANCE_CANDIDATE = "REBALANCE_CANDIDATE"
    RISK_OVERRIDE = "RISK_OVERRIDE"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class OrderStatus(str, Enum):
    """订单状态机状态"""
    DRAFT = "DRAFT"
    RISK_APPROVED = "RISK_APPROVED"
    USER_APPROVED = "USER_APPROVED"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    RECONCILED = "RECONCILED"
    # 异常终止状态
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"


class OrderSide(str, Enum):
    """订单方向"""
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class StrategyLifecycle(str, Enum):
    """策略生命周期状态"""
    IDEA = "IDEA"
    RESEARCH = "RESEARCH"
    BACKTESTED = "BACKTESTED"
    PAPER = "PAPER"
    REAL_LIMITED = "REAL_LIMITED"
    REAL = "REAL"
    SUSPENDED = "SUSPENDED"
    RETIRED = "RETIRED"

    def can_promote_to(self, target: StrategyLifecycle) -> bool:
        valid = {
            StrategyLifecycle.IDEA: [StrategyLifecycle.RESEARCH],
            StrategyLifecycle.RESEARCH: [StrategyLifecycle.BACKTESTED],
            StrategyLifecycle.BACKTESTED: [StrategyLifecycle.PAPER],
            StrategyLifecycle.PAPER: [StrategyLifecycle.REAL_LIMITED],
            StrategyLifecycle.REAL_LIMITED: [StrategyLifecycle.REAL],
        }
        return target in valid.get(self, [])


class MarketRegime(str, Enum):
    """市场状态"""
    TREND_RISK_ON = "TREND_RISK_ON"
    RANGE_NEUTRAL = "RANGE_NEUTRAL"
    STRESS_RISK_OFF = "STRESS_RISK_OFF"
    RECOVERY_PROBE = "RECOVERY_PROBE"
    UNKNOWN = "UNKNOWN"


class CapitalPermission(str, Enum):
    """策略资金权限"""
    REAL = "REAL"
    REAL_LIMITED = "REAL_LIMITED"
    PAPER = "PAPER"
    DISABLED = "DISABLED"
    OBSERVATION_ONLY = "OBSERVATION_ONLY"


class ModelStatus(str, Enum):
    """模型状态"""
    MODEL_OK = "MODEL_OK"
    MODEL_WARNING = "MODEL_WARNING"
    MODEL_REVIEW_REQUIRED = "MODEL_REVIEW_REQUIRED"
    MODEL_SUSPENDED = "MODEL_SUSPENDED"

    def can_promote_to_live(self) -> bool:
        return self == ModelStatus.MODEL_OK


class TrustLevel(int, Enum):
    """研究/学习型模型的可信级别（L0-L6）。

    级别是单调递增的：数值越大，模型输出越可信。
    生产环境默认只消费 >= L4 的模型输出（研究模型默认冻结、默认关闭）。
    这是"研究/生产隔离"与"Kronos 冻结原则"的量化载体。
    """
    L0_UNVERIFIED = 0   # 原始/未验证：仅研究记录，禁止影响任何决策
    L1_SANITY = 1       # 通过基本合理性检查（无 NaN、范围合理）
    L2_BACKTEST = 2     # 通过离线回测验证
    L3_PAPER = 3        # 通过纸面（PAPER）验证
    L4_SHADOW = 4       # 影子跟踪：与生产并行观察但不出资金
    L5_LIMITED = 5      # 小额实盘（REAL_LIMITED）验证中
    L6_PROVEN = 6       # 长期验证、可信，可参与真实决策

    @property
    def is_production_eligible(self) -> bool:
        """是否达到生产可消费门槛（默认 >= L4_SHADOW）。"""
        return self.value >= TrustLevel.L4_SHADOW.value


# ── 兼容别名：旧 Regime → MarketRegime 映射 ──────────────────────────

REGIME_LEGACY_MAP: Final[dict[str, MarketRegime]] = {
    "RISK_ON": MarketRegime.TREND_RISK_ON,
    "NEUTRAL": MarketRegime.RANGE_NEUTRAL,
    "RISK_OFF": MarketRegime.STRESS_RISK_OFF,
}
