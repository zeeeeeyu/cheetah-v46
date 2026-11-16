"""Topic-01 数据源层（离线优先，研究回测唯一入口 = LocalSnapshotProvider）。

导出约定：
- MarketDataProvider: 抽象基类
- LocalSnapshotProvider: 冻结本地快照（研究回测唯一允许接口）
- TushareProvider / AkshareProvider: 远程骨架（默认禁用，绝不直接调用）
- NetworkAccessDisabledError / LineageMissingError: 安全异常
- 各类 schema 数据载体
"""
from __future__ import annotations

from .base import (
    EXPLICIT_NETWORK_AUTHORIZATION,
    LineageMissingError,
    MarketDataProvider,
    NetworkAccessDisabledError,
)
from .schemas import (
    CANONICAL_COLUMNS,
    ETF,
    FUND_NAV,
    HFQ,
    INDEX,
    INDEX_PROXY,
    LineageRecord,
    NONE,
    QFQ,
    SCHEMA_VERSION,
    UNKNOWN,
    IndexBar,
    EtfBar,
)
from .local_snapshot import LocalSnapshotProvider
from .tushare_provider import TushareProvider
from .akshare_provider import AkshareProvider
from .lineage import LineageManifest
from .validators import ValidationResult, validate_dataset
from .normalizer import normalize_raw_to_research
from .cross_source import CrossSourceReport, compare_sources

__all__ = [
    "EXPLICIT_NETWORK_AUTHORIZATION",
    "LineageMissingError",
    "MarketDataProvider",
    "NetworkAccessDisabledError",
    # schemas
    "SCHEMA_VERSION",
    "CANONICAL_COLUMNS",
    "NONE",
    "QFQ",
    "HFQ",
    "UNKNOWN",
    "ETF",
    "INDEX",
    "INDEX_PROXY",
    "FUND_NAV",
    "IndexBar",
    "EtfBar",
    "LineageRecord",
    # providers
    "LocalSnapshotProvider",
    "TushareProvider",
    "AkshareProvider",
    # helpers
    "LineageManifest",
    "ValidationResult",
    "validate_dataset",
    "normalize_raw_to_research",
    "CrossSourceReport",
    "compare_sources",
]
