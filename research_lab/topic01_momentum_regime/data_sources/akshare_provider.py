"""Akshare 数据源骨架（默认禁用，绝不自动提升为 PRIMARY）。

安全边界：
- 不在模块顶层 import akshare；仅在方法体内惰性 import。
- 默认 is_enabled() == False；只有 EXPLICIT_NETWORK_AUTHORIZATION=True 且
  环境变量 AKSHARE_TOKEN 存在时才视为可用（akshare 通常无需 token，此处仍
  以 token 存在作为显式授权信号，fail-closed）。
- 任何 get_* 调用都会抛 NetworkAccessDisabledError（本轮网络未授权）。
- 远程 provider 仅供血缘来源标注，研究回测不得直接调用其 get_*。
- 该 provider 不应被自动提升为 PRIMARY（详见 DATA_PROVIDER_CONTRACT.md）。
"""
from __future__ import annotations

import os
from typing import Optional

import pandas as pd

from .base import (
    EXPLICIT_NETWORK_AUTHORIZATION,
    LineageMissingError,
    MarketDataProvider,
    NetworkAccessDisabledError,
)
from .schemas import CANONICAL_COLUMNS

AKSHARE_TOKEN_ENV = "AKSHARE_TOKEN"


class AkshareProvider(MarketDataProvider):
    """Akshare 远程 provider 骨架。默认禁用，永不直接抓取。"""

    provider_name = "akshare"
    required_token_env = AKSHARE_TOKEN_ENV

    def is_enabled(self) -> bool:
        """仅当显式授权且存在 AKSHARE_TOKEN 时才启用。"""
        return bool(
            EXPLICIT_NETWORK_AUTHORIZATION and os.environ.get(AKSHARE_TOKEN_ENV)
        )

    def _refuse(self, op: str) -> None:
        self._assert_network_allowed(op)

    def get_index_daily(
        self, index_code: str, start: str, end: str, adjustment: str = "QFQ"
    ) -> pd.DataFrame:
        self._refuse("get_index_daily")
        import akshare as ak  # noqa: F401  (仅占位，绝不真正调用)
        raise NetworkAccessDisabledError("Akshare 抓取在本轮被禁用")

    def get_etf_daily(
        self, symbol: str, start: str, end: str, adjustment: str = "QFQ"
    ) -> pd.DataFrame:
        self._refuse("get_etf_daily")
        import akshare as ak  # noqa: F401
        raise NetworkAccessDisabledError("Akshare 抓取在本轮被禁用")

    def get_etf_metadata(self) -> dict:
        self._refuse("get_etf_metadata")
        raise NetworkAccessDisabledError("Akshare 抓取在本轮被禁用")

    def get_trading_calendar(self, start: str, end: str) -> list[str]:
        self._refuse("get_trading_calendar")
        raise NetworkAccessDisabledError("Akshare 抓取在本轮被禁用")
