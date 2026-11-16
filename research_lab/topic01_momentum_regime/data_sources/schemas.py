"""Topic-01 数据层 schema 定义（纯 dataclass，不依赖 pydantic）。

本模块只定义常量与数据载体，不做任何 IO / 网络调用。
所有 schema 版本、资产类型、复权类型集中在此，供 provider / normalizer /
validator / lineage 统一引用。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# ── Schema 版本（用于血缘与数据集版本计算）──
SCHEMA_VERSION = "2026-08-07"

# ── 复权类型常量 ──
NONE = "NONE"      # 不复权
QFQ = "QFQ"        # 前复权
HFQ = "HFQ"        # 后复权
UNKNOWN = "UNKNOWN"  # 未声明（冻结数据不允许）

# ── 资产类型常量 ──
ETF = "ETF"
INDEX = "INDEX"
INDEX_PROXY = "INDEX_PROXY"
FUND_NAV = "FUND_NAV"

# 研究域规范列（provider 返回的 DataFrame 必须至少包含这些列）
CANONICAL_COLUMNS = ["date", "open", "high", "low", "close", "volume", "amount"]


@dataclass
class IndexBar:
    """指数日线棒（研究域）。"""

    asset_id: str
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float
    source: str
    retrieved_at: str
    adjustment: str
    raw_hash: str


@dataclass
class EtfBar:
    """ETF 日线棒（研究域）。"""

    symbol: str
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float
    source: str
    adjustment: str
    listing_date: Optional[str]
    retrieved_at: str
    source_asset_id: str
    raw_hash: str


@dataclass
class LineageRecord:
    """数据集血缘记录。

    本轮为离线冻结数据，source_endpoint_id / request_parameters 允许为
    "NOT_FETCHED"（不伪造真实端点）。sha256 必须由真实本地文件计算得出。
    """

    dataset_id: str
    asset_id: str
    asset_type: str
    source_provider: str
    source_endpoint_id: str
    request_parameters: str
    retrieved_at: str
    adjustment: str
    listing_date: Optional[str]
    first_date: str
    last_date: str
    row_count: int
    sha256: str
    schema_version: str
    validation_status: str
