"""V4.6 数据身份治理。

对外统一入口：资产主数据 + 数据血缘/质量登记 + 代理隔离机器约束。
"""

from __future__ import annotations

from .asset_master import AssetMaster, AssetMasterError, AssetRecord
from .lineage import (
    DataIdentityError,
    DataIdentityRegistry,
    DataLineageRecord,
    DataOrigin,
    content_fingerprint,
)

__all__ = [
    "AssetRecord",
    "AssetMaster",
    "AssetMasterError",
    "DataOrigin",
    "DataLineageRecord",
    "DataIdentityRegistry",
    "DataIdentityError",
    "content_fingerprint",
]
