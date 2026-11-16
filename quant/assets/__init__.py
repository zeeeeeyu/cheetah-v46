"""V4.6 资产目录加载器与校验服务。

从 asset_master.json 加载统一资产目录，并提供：
- Schema 校验
- 资产身份校验（唯一性、前导零、类型一致性）
- 基金-ETF 关系管理
- 风险簇配置加载
- 旧配置适配器（V4.2 PROXY_MAPPING → fund_etf_relations.json）
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from quant.config import project_path
from quant.domain.enums import AssetType
from quant.domain.assets import Asset, FundETFRelation


# ── 有效关系类型 ──────────────────────────────────────────────

VALID_RELATION_TYPES = frozenset({
    "BENCHMARK_RELATION",
    "THEMATIC_REFERENCE",
    "UNDERLYING_ETF",
    "RESEARCH_PROXY",
    "NO_DIRECT_RELATION",
})

FORBIDDEN_RELATION_TYPES = frozenset({
    "IDENTITY_PROXY",
})


# ── 配置加载器 ────────────────────────────────────────────────

class AssetMasterLoader:
    """统一资产目录加载器。

    读取 asset_master.json，并为每个条目创建 Asset domain 对象。
    """

    def __init__(self, config_dir: Path | None = None):
        self.config_dir = config_dir or project_path("config", "assets")

    @property
    def master_path(self) -> Path:
        return self.config_dir / "asset_master.json"

    @property
    def relations_path(self) -> Path:
        return self.config_dir / "fund_etf_relations.json"

    def load_all(self) -> list[Asset]:
        """加载并校验全部资产。"""
        if not self.master_path.exists():
            raise FileNotFoundError(f"asset_master.json 不存在: {self.master_path}")
        raw = json.loads(self.master_path.read_text(encoding="utf-8"))
        assets = [self._parse_asset(item) for item in raw]
        self.validate_assets(assets)
        return assets

    def load_by_symbol(self, symbol: str) -> Asset | None:
        for asset in self.load_all():
            if asset.symbol == symbol:
                return asset
        return None

    def load_by_type(self, asset_type: AssetType) -> list[Asset]:
        return [a for a in self.load_all() if a.asset_type == asset_type]

    def load_relations(self) -> list[FundETFRelation]:
        """加载基金-ETF 关系表。"""
        if not self.relations_path.exists():
            return []
        raw = json.loads(self.relations_path.read_text(encoding="utf-8"))
        return [self._parse_relation(item) for item in raw]

    def _parse_asset(self, item: dict[str, Any]) -> Asset:
        kwargs = dict(item)
        if "asset_type" in kwargs and isinstance(kwargs["asset_type"], str):
            kwargs["asset_type"] = AssetType(kwargs["asset_type"])
        if "permission" in kwargs and isinstance(kwargs["permission"], str):
            from quant.domain.enums import CapitalPermission
            kwargs["permission"] = CapitalPermission(kwargs["permission"])
        if "risk_clusters" in kwargs and isinstance(kwargs["risk_clusters"], list):
            kwargs["risk_clusters"] = tuple(kwargs["risk_clusters"])
        # Strip extra fields not in Asset
        known = {"symbol", "name", "asset_type", "exchange", "currency", "lot_size",
                 "signal_symbol", "trade_symbol", "risk_clusters", "strategy_eligible",
                 "execution_eligible", "permission"}
        kwargs = {k: v for k, v in kwargs.items() if k in known}
        return Asset(**kwargs)

    def _parse_relation(self, item: dict[str, Any]) -> FundETFRelation:
        return FundETFRelation(
            fund_symbol=str(item["fund_symbol"]),
            etf_symbol=str(item["etf_symbol"]),
            relation_type=str(item["relation_type"]),
            correlation=float(item["correlation"]) if item.get("correlation") is not None else None,
            notes=str(item.get("notes", "")),
        )

    @staticmethod
    def validate_assets(assets: list[Asset]) -> None:
        """校验资产目录的完整性。"""
        symbols: set[str] = set()
        for asset in assets:
            # 唯一性
            if asset.symbol in symbols:
                raise ValueError(f"重复资产代码: {asset.symbol}")
            symbols.add(asset.symbol)

            # 基金代码类型一致性
            if asset.asset_type in (AssetType.ACTIVE_FUND, AssetType.ETF_LINKED_FUND):
                if not asset.symbol.isdigit() or len(asset.symbol) != 6:
                    raise ValueError(f"基金代码必须为6位数字: {asset.symbol}")
            if asset.asset_type == AssetType.ETF:
                if not asset.exchange:
                    raise ValueError(f"ETF {asset.symbol} 必须指定 exchange")
                if asset.lot_size != 100:
                    raise ValueError(f"ETF {asset.symbol} lot_size 应为 100，当前 {asset.lot_size}")

            # 显式 signal/trade 符号
            if asset.signal_symbol != asset.symbol:
                raise ValueError(
                    f"asset_master 禁止代理: {asset.symbol} signal_symbol={asset.signal_symbol}"
                )
            if asset.trade_symbol != asset.symbol:
                raise ValueError(
                    f"asset_master 禁止代理: {asset.symbol} trade_symbol={asset.trade_symbol}"
                )

            # 风险簇
            if not asset.risk_clusters:
                raise ValueError(f"资产 {asset.symbol} 至少需要一个风险簇")

    @staticmethod
    def validate_relations(relations: list[FundETFRelation]) -> None:
        """校验基金-ETF 关系。"""
        for rel in relations:
            if rel.relation_type in FORBIDDEN_RELATION_TYPES:
                raise ValueError(
                    f"禁止使用 {rel.relation_type} 关系类型: "
                    f"{rel.fund_symbol} → {rel.etf_symbol}"
                )
            if rel.relation_type not in VALID_RELATION_TYPES:
                raise ValueError(
                    f"未知关系类型 {rel.relation_type}: "
                    f"{rel.fund_symbol} → {rel.etf_symbol}"
                )


# ── 旧配置适配器 ──────────────────────────────────────────────

V42_PROXY_MAPPING: dict[str, str] = {
    "000823": "515000",
    "002251": "512660",
    "000051": "510300",
    "160216": "518880",
    "100032": "510880",
    "000216": "518880",
    "004813": "515030",
    "005658": "510300",
}


def legacy_proxy_to_research_relations(
    proxy_map: dict[str, str] | None = None,
) -> list[FundETFRelation]:
    """将 V4.2 PROXY_MAPPING 转换为研究级关系。

    所有转换后的关系类型设为 RESEARCH_PROXY，
    明确标记为"研究参考"，非身份替代。
    """
    mapping = proxy_map or V42_PROXY_MAPPING
    relations: list[FundETFRelation] = []
    for fund_code, etf_code in mapping.items():
        relations.append(FundETFRelation(
            fund_symbol=fund_code,
            etf_symbol=etf_code,
            relation_type="RESEARCH_PROXY",
            correlation=None,
            notes=f"V4.2 旧代理映射转换。仅研究参考，非身份替代。"
        ))
    return relations


# ── 配置 Schema ────────────────────────────────────────────────

ASSET_MASTER_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "required": ["symbol", "name", "asset_type", "risk_clusters"],
        "properties": {
            "symbol": {"type": "string", "pattern": "^[A-Z0-9]{6}$"},
            "name": {"type": "string"},
            "asset_type": {"type": "string"},
            "exchange": {"type": ["string", "null"]},
            "currency": {"type": "string"},
            "calendar_group": {"type": "string", "enum": ["CN", "HK", "QDII"]},
            "lot_size": {"type": "integer"},
            "signal_symbol": {"type": "string"},
            "trade_symbol": {"type": "string"},
            "risk_clusters": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "strategy_eligible": {"type": "boolean"},
            "execution_eligible": {"type": "boolean"},
            "permission": {"type": "string"},
        },
    },
}

RELATIONS_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "required": ["fund_symbol", "etf_symbol", "relation_type"],
        "properties": {
            "fund_symbol": {"type": "string"},
            "etf_symbol": {"type": "string"},
            "relation_type": {"type": "string", "enum": list(VALID_RELATION_TYPES)},
            "correlation": {"type": ["number", "null"]},
            "notes": {"type": "string"},
        },
    },
}


def validate_asset_config() -> list[str]:
    """校验所有资产配置文件，返回错误列表。"""
    errors: list[str] = []
    loader = AssetMasterLoader()

    try:
        assets = loader.load_all()
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as e:
        errors.append(f"asset_master.json: {e}")
        return errors

    try:
        relations = loader.load_relations()
        loader.validate_relations(relations)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as e:
        errors.append(f"fund_etf_relations.json: {e}")

    # Check all fund/ETF relation symbols exist in master
    master_symbols = {a.symbol for a in assets}
    for rel in relations:
        if rel.fund_symbol not in master_symbols:
            errors.append(f"关系中的基金 {rel.fund_symbol} 不在 asset_master.json 中")
        if rel.etf_symbol not in master_symbols:
            errors.append(f"关系中的ETF {rel.etf_symbol} 不在 asset_master.json 中")

    return errors
