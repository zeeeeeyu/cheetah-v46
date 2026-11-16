"""V4.6 资产领域模型。

Asset 是核心领域对象，代表一个可交易的金融资产。
基金代码必须保留前导零。
signal_symbol 和 trade_symbol 必须显式保存。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .enums import AssetType, CapitalPermission, MarketRegime


@dataclass(frozen=True)
class Asset:
    """不可变的资产主数据。

    基金代码必须保留前导零（如 "004813"）。
    基金和 ETF 不得仅通过代码格式推断类型。
    signal_symbol 和 trade_symbol 必须显式保存。
    ETF 代理关系只能作为研究关系，不能作为身份替代。
    """
    symbol: str
    """交易代码。基金代码保留前导零。"""

    name: str
    """资产名称。"""

    asset_type: AssetType
    """资产类型（ETF / ACTIVE_FUND / ETF_LINKED_FUND 等）。"""

    exchange: str | None = None
    """交易所代码（如 "SSE", "SZSE"），ETF 必填，基金可为 None。"""

    currency: str = "CNY"
    """计价货币。"""

    lot_size: int = 1
    """最小交易单位。ETF 为 100（一手），基金为 1（元）。"""

    signal_symbol: str | None = None
    """信号来源代码。默认等于 symbol。显式表达，不允许隐式推断。"""

    trade_symbol: str | None = None
    """实际交易代码。默认等于 symbol。显式表达，不允许隐式推断。"""

    risk_clusters: tuple[str, ...] = field(default_factory=tuple)
    """所属风险簇。一个资产可以属于多个风险簇。"""

    strategy_eligible: bool = True
    """是否可被策略纳入候选。"""

    execution_eligible: bool = True
    """是否可实际交易。False 表示仅用于研究/观察。"""

    permission: CapitalPermission = CapitalPermission.REAL
    """策略对该资产的默认权限。"""

    def __post_init__(self) -> None:
        # 确保显式值
        if self.signal_symbol is None:
            object.__setattr__(self, "signal_symbol", self.symbol)
        if self.trade_symbol is None:
            object.__setattr__(self, "trade_symbol", self.symbol)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "asset_type": self.asset_type.value,
            "exchange": self.exchange,
            "currency": self.currency,
            "lot_size": self.lot_size,
            "signal_symbol": self.signal_symbol,
            "trade_symbol": self.trade_symbol,
            "risk_clusters": list(self.risk_clusters),
            "strategy_eligible": self.strategy_eligible,
            "execution_eligible": self.execution_eligible,
            "permission": self.permission.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Asset:
        kwargs = dict(data)
        if "asset_type" in kwargs and isinstance(kwargs["asset_type"], str):
            kwargs["asset_type"] = AssetType(kwargs["asset_type"])
        if "permission" in kwargs and isinstance(kwargs["permission"], str):
            kwargs["permission"] = CapitalPermission(kwargs["permission"])
        if "risk_clusters" in kwargs and isinstance(kwargs["risk_clusters"], list):
            kwargs["risk_clusters"] = tuple(kwargs["risk_clusters"])
        return cls(**kwargs)


@dataclass(frozen=True)
class Position:
    """持仓快照（不可变）。

    由 Transaction Ledger 聚合生成。
    不得直接从目标权重或交易建议创建。
    """
    account_id: str
    symbol: str
    quantity: float
    available_quantity: float
    average_cost: float
    market_value: float
    strategy_allocations: dict[str, float] = field(default_factory=dict)
    updated_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "symbol": self.symbol,
            "quantity": self.quantity,
            "available_quantity": self.available_quantity,
            "average_cost": self.average_cost,
            "market_value": self.market_value,
            "strategy_allocations": dict(self.strategy_allocations),
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass(frozen=True)
class Transaction:
    """不可变的交易事件。

    不允许覆盖修改历史交易。
    错误修正通过新增冲正记录完成。
    """
    transaction_id: str
    account_id: str
    symbol: str
    strategy_id: str
    side: str  # BUY / SELL
    quantity: float
    gross_amount: float
    fee: float
    execution_date: str
    confirmation_date: str | None = None
    source: str = "MANUAL"

    def to_dict(self) -> dict[str, Any]:
        return {
            "transaction_id": self.transaction_id,
            "account_id": self.account_id,
            "symbol": self.symbol,
            "strategy_id": self.strategy_id,
            "side": self.side,
            "quantity": self.quantity,
            "gross_amount": self.gross_amount,
            "fee": self.fee,
            "execution_date": self.execution_date,
            "confirmation_date": self.confirmation_date,
            "source": self.source,
        }


@dataclass(frozen=True)
class FundETFRelation:
    """基金与 ETF 的关系（研究级映射，非身份替代）。

    禁止使用 IDENTITY_PROXY。
    """
    fund_symbol: str
    etf_symbol: str
    relation_type: str  # BENCHMARK_RELATION / THEMATIC_REFERENCE / UNDERLYING_ETF / RESEARCH_PROXY / NO_DIRECT_RELATION
    correlation: float | None = None
    notes: str = ""
