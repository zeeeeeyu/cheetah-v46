"""V4.6 资产主数据（Asset Master）—— 每个可交易标的的权威身份。

资产主数据是"数据身份治理"的根：任何标的在进入策略/执行/对账之前，
都必须能在这里查到它的**权威身份**（交易代码、信号代码、资产类型、
交易权限、是否使用代理信号等）。

代理信号（proxy signal）的定义
==============================
当一个标的的 ``signal_symbol`` 与 ``trade_symbol`` 不一致时，意味着我们用
"另一个标的（如指数）的数据"来产生信号，却在"这个标的（如场外基金）"上交易。
这是量化系统里最隐蔽的风险之一——代理数据一旦被误当作直连数据消费，
回测和实盘就会系统性失真。资产主数据把这种关系显式登记下来，供
``lineage`` 层施加机器约束。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

__all__ = ["AssetRecord", "AssetMaster", "AssetMasterError"]


class AssetMasterError(KeyError):
    """资产主数据相关错误（未知标的、重复标的等）。"""


@dataclass(frozen=True)
class AssetRecord:
    """单个标的的权威身份（不可变）。"""

    symbol: str
    name: str
    asset_type: str
    trade_symbol: str
    signal_symbol: str
    permission: str = "OBSERVATION_ONLY"
    strategy_eligible: bool = False
    execution_eligible: bool = False
    currency: str = "CNY"
    exchange: str | None = None
    fund_type: str | None = None
    share_class: str | None = None
    nav_basis: str | None = None
    lot_size: int = 1
    risk_clusters: tuple[str, ...] = field(default_factory=tuple)
    notes: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def uses_proxy_signal(self) -> bool:
        """信号代码与交易代码不一致 → 使用了代理信号。"""
        return str(self.signal_symbol) != str(self.trade_symbol)

    @property
    def is_open_end_fund(self) -> bool:
        """是否为场外基金（走 T+1 净值 / T+2 份额确认的 fund_orders 路径）。"""
        return self.asset_type in {"ACTIVE_FUND", "ETF_LINKED_FUND"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "asset_type": self.asset_type,
            "trade_symbol": self.trade_symbol,
            "signal_symbol": self.signal_symbol,
            "permission": self.permission,
            "strategy_eligible": self.strategy_eligible,
            "execution_eligible": self.execution_eligible,
            "currency": self.currency,
            "exchange": self.exchange,
            "fund_type": self.fund_type,
            "share_class": self.share_class,
            "nav_basis": self.nav_basis,
            "lot_size": self.lot_size,
            "risk_clusters": list(self.risk_clusters),
            "notes": self.notes,
            "uses_proxy_signal": self.uses_proxy_signal,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AssetRecord:
        known = {
            "symbol", "name", "asset_type", "trade_symbol", "signal_symbol",
            "permission", "strategy_eligible", "execution_eligible", "currency",
            "exchange", "fund_type", "share_class", "nav_basis", "lot_size",
            "risk_clusters", "notes",
        }
        symbol = str(data["symbol"])
        return cls(
            symbol=symbol,
            name=str(data.get("name", symbol)),
            asset_type=str(data.get("asset_type", "UNKNOWN")),
            trade_symbol=str(data.get("trade_symbol", symbol)),
            signal_symbol=str(data.get("signal_symbol", symbol)),
            permission=str(data.get("permission", "OBSERVATION_ONLY")),
            strategy_eligible=bool(data.get("strategy_eligible", False)),
            execution_eligible=bool(data.get("execution_eligible", False)),
            currency=str(data.get("currency", "CNY")),
            exchange=data.get("exchange"),
            fund_type=data.get("fund_type"),
            share_class=data.get("share_class"),
            nav_basis=data.get("nav_basis"),
            lot_size=int(data.get("lot_size", 1) or 1),
            risk_clusters=tuple(data.get("risk_clusters", []) or []),
            notes=str(data.get("notes", "")),
            extra={k: v for k, v in data.items() if k not in known},
        )


class AssetMaster:
    """资产主数据仓库（只读查询接口）。"""

    def __init__(self, records: Iterable[AssetRecord]) -> None:
        self._by_symbol: dict[str, AssetRecord] = {}
        for rec in records:
            if rec.symbol in self._by_symbol:
                raise AssetMasterError(f"资产主数据存在重复标的：{rec.symbol}")
            self._by_symbol[rec.symbol] = rec

    def __len__(self) -> int:  # pragma: no cover - trivial
        return len(self._by_symbol)

    def __contains__(self, symbol: str) -> bool:  # pragma: no cover - trivial
        return symbol in self._by_symbol

    def get(self, symbol: str) -> AssetRecord:
        try:
            return self._by_symbol[symbol]
        except KeyError as exc:
            raise AssetMasterError(f"未知标的（不在资产主数据中）：{symbol}") from exc

    def all(self) -> tuple[AssetRecord, ...]:
        return tuple(self._by_symbol.values())

    def symbols(self) -> tuple[str, ...]:
        return tuple(self._by_symbol.keys())

    def proxy_signal_symbols(self) -> tuple[str, ...]:
        """所有使用了代理信号的标的（signal_symbol != trade_symbol）。"""
        return tuple(s for s, r in self._by_symbol.items() if r.uses_proxy_signal)

    @classmethod
    def from_json(cls, path: Path | str) -> AssetMaster:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise AssetMasterError("asset_master.json 顶层必须是列表")
        return cls(AssetRecord.from_dict(item) for item in raw)
