"""V4.6 风险簇暴露计算器。

根据资产持仓权重和资产-风险簇映射，计算各风险簇的暴露权重。
一个资产可以属于多个风险簇。
数据不足时标记为 ESTIMATED 或 UNKNOWN。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from quant.config import project_path
from quant.domain.assets import Asset
from quant.assets import AssetMasterLoader


@dataclass
class ClusterDef:
    """风险簇配置定义。"""
    id: str
    name: str
    max_exposure: float
    description: str = ""
    estimation_quality: str = "ESTIMATED"  # HIGH / MODERATE / ESTIMATED / UNKNOWN


@dataclass
class ClusterExposure:
    """单个风险簇的暴露计算结果。"""
    cluster_id: str
    cluster_name: str
    total_weight: float
    max_exposure: float
    breached: bool
    asset_contributions: dict[str, float]  # {symbol: weight_contribution}
    estimation_quality: str
    notes: str = ""


@dataclass
class PortfolioClusterReport:
    """组合风险簇暴露报告。"""
    account_id: str
    as_of_date: str
    total_equity_weight: float
    cash_weight: float
    cluster_exposures: list[ClusterExposure]
    highest_exposure: ClusterExposure | None = None
    total_breached_clusters: int = 0
    warnings: list[str] = field(default_factory=list)
    data_quality_notes: list[str] = field(default_factory=list)


class RiskClusterCalculator:
    """风险簇暴露计算器。

    根据资产目录的风险簇映射和当前持仓权重，计算各风险簇的暴露。
    支持一个资产对应多个风险簇（权重按资产权重全额计入每个簇）。
    """

    def __init__(self, loader: AssetMasterLoader | None = None):
        self.loader = loader or AssetMasterLoader()
        self._clusters: dict[str, ClusterDef] | None = None

    @property
    def cluster_defs(self) -> dict[str, ClusterDef]:
        if self._clusters is None:
            self._clusters = self._load_cluster_defs()
        return self._clusters

    def _load_cluster_defs(self) -> dict[str, ClusterDef]:
        path = project_path("config", "risk", "risk_clusters.json")
        raw = json.loads(path.read_text(encoding="utf-8"))
        result: dict[str, ClusterDef] = {}
        for item in raw.get("clusters", []):
            cd = ClusterDef(
                id=item["id"],
                name=item["name"],
                max_exposure=float(item["max_exposure"]),
                description=item.get("description", ""),
                estimation_quality=item.get("estimation_quality", "ESTIMATED"),
            )
            result[cd.id] = cd
        return result

    def compute(
        self,
        holdings: dict[str, float],  # {symbol: weight} 权重总和为 1.0
        assets: list[Asset] | None = None,
    ) -> PortfolioClusterReport:
        """根据持仓权重计算风险簇暴露。

        权重分配原则：
        - 一个资产属于多个风险簇时，其权重全额计入每个簇
        - 例如：013566 权重 30%，属于 [MILITARY_DEFENSE, ADVANCED_MANUFACTURING, SMALL_MID_GROWTH]
          → 三个簇各计入 30%
        - 现金（CASH）不计入风险簇
        """
        if assets is None:
            assets = self.loader.load_all()

        asset_map = {a.symbol: a for a in assets}
        cluster_defs = self.cluster_defs
        cluster_weights: dict[str, float] = {}
        cluster_contributions: dict[str, dict[str, float]] = {}
        warnings: list[str] = []
        quality_notes: list[str] = []

        for symbol, weight in holdings.items():
            if symbol.upper() == "CASH":
                continue

            asset = asset_map.get(symbol)
            if asset is None:
                warnings.append(f"未知资产 {symbol}，已跳过风险簇计算")
                continue

            clusters = asset.risk_clusters
            if not clusters:
                warnings.append(f"资产 {symbol} 未分配风险簇")
                continue

            for cluster_id in clusters:
                cluster_weights[cluster_id] = cluster_weights.get(cluster_id, 0.0) + weight
                if cluster_id not in cluster_contributions:
                    cluster_contributions[cluster_id] = {}
                cluster_contributions[cluster_id][symbol] = (
                    cluster_contributions[cluster_id].get(symbol, 0.0) + weight
                )

        # Build exposure list
        exposures: list[ClusterExposure] = []
        total_equity = sum(weight for sym, weight in holdings.items()
                          if sym.upper() != "CASH")
        cash_weight = holdings.get("CASH", 0.0) + holdings.get("现金/货基", 0.0)
        breached_count = 0
        highest: ClusterExposure | None = None

        for cluster_id, total_weight in sorted(cluster_weights.items()):
            defn = cluster_defs.get(cluster_id)
            max_exposure = defn.max_exposure if defn else 0.5
            quality = defn.estimation_quality if defn else "UNKNOWN"
            if quality in ("ESTIMATED", "UNKNOWN"):
                quality_notes.append(
                    f"风险簇 {cluster_id} 的质量为 {quality}，"
                    f"资产暴露为估算值，非精确穿透计算"
                )
            breached = total_weight > max_exposure + 1e-6
            if breached:
                breached_count += 1
            exp = ClusterExposure(
                cluster_id=cluster_id,
                cluster_name=defn.name if defn else cluster_id,
                total_weight=total_weight,
                max_exposure=max_exposure,
                breached=breached,
                asset_contributions=cluster_contributions.get(cluster_id, {}),
                estimation_quality=quality,
            )
            exposures.append(exp)
            if highest is None or exp.total_weight > highest.total_weight:
                highest = exp

        warnings.extend(quality_notes)

        return PortfolioClusterReport(
            account_id="MAIN",
            as_of_date="2026-07-21",
            total_equity_weight=total_equity,
            cash_weight=cash_weight,
            cluster_exposures=exposures,
            highest_exposure=highest,
            total_breached_clusters=breached_count,
            warnings=warnings,
            data_quality_notes=quality_notes,
        )

    def compute_for_current_holdings(
        self,
        holdings: dict[str, float] | None = None,
    ) -> PortfolioClusterReport:
        """为当前基金持仓计算风险簇暴露。

        从持仓配置中读取权重，或使用默认值。
        """
        if holdings is None:
            # 当前持有：005658 (0.644) + 016263 (0.356) 来自 fund_portfolio_state.json
            holdings = {
                "005658": 0.644,
                "016263": 0.356,
                "CASH": 0.0,
            }
        return self.compute(holdings)
