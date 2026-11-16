"""Tushare 数据源骨架（默认禁用，绝不自动提升为 PRIMARY）。

安全边界：
- 不在模块顶层 import tushare；仅在方法体内惰性 import。
- 默认 is_enabled() == False；只有 EXPLICIT_NETWORK_AUTHORIZATION=True 且
  环境变量 TUSHARE_TOKEN 存在时才视为可用。
- 任何 get_* 调用都会抛 NetworkAccessDisabledError（本轮网络未授权）。
- 远程 provider 仅供血缘记录来源标注，研究回测不得直接调用其 get_*。
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

TUSHARE_TOKEN_ENV = "TUSHARE_TOKEN"


class TushareProvider(MarketDataProvider):
    """Tushare 远程 provider 骨架。默认禁用，永不直接抓取。"""

    provider_name = "tushare"
    required_token_env = TUSHARE_TOKEN_ENV

    def is_enabled(self) -> bool:
        """仅当显式授权且存在 TUSHARE_TOKEN 时才启用。"""
        return bool(
            EXPLICIT_NETWORK_AUTHORIZATION and os.environ.get(TUSHARE_TOKEN_ENV)
        )

    def _refuse(self, op: str) -> None:
        # 任何真实抓取路径都必须在发起请求前调用，确保不触网。
        self._assert_network_allowed(op)

    def get_index_daily(
        self, index_code: str, start: str, end: str, adjustment: str = "QFQ"
    ) -> pd.DataFrame:
        self._refuse("get_index_daily")
        # 以下代码在授权后才会执行；本轮不会到达。
        # 惰性 import，确保未授权时不加载远程库。
        import tushare as ts  # noqa: F401  (仅占位，绝不真正调用)
        raise NetworkAccessDisabledError("Tushare 抓取在本轮被禁用")

    def get_etf_daily(
        self, symbol: str, start: str, end: str, adjustment: str = "QFQ"
    ) -> pd.DataFrame:
        self._refuse("get_etf_daily")
        import tushare as ts  # noqa: F401
        raise NetworkAccessDisabledError("Tushare 抓取在本轮被禁用")

    def get_etf_metadata(self) -> dict:
        self._refuse("get_etf_metadata")
        raise NetworkAccessDisabledError("Tushare 抓取在本轮被禁用")

    def get_trading_calendar(self, start: str, end: str) -> list[str]:
        self._refuse("get_trading_calendar")
        raise NetworkAccessDisabledError("Tushare 抓取在本轮被禁用")
