"""Topic-01 数据源抽象基类与全局网络开关。

安全边界（详见 DATA_PROVIDER_CONTRACT.md）：
- EXPLICIT_NETWORK_AUTHORIZATION 默认 False；任何真实抓取路径必须校验
  `EXPLICIT_NETWORK_AUTHORIZATION is True` 且对应 provider token 环境变量存在，
  否则抛出 NetworkAccessDisabledError。
- 本文件不 import 任何远程库，不发起任何网络请求。
- 研究回测只允许通过 LocalSnapshotProvider 读取冻结本地数据集；绝不直接调用
  TushareProvider / AkshareProvider 的 get_* 方法。
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Optional

import pandas as pd

from .schemas import CANONICAL_COLUMNS

# ── 全局网络授权开关：默认关闭，绝不可在代码中被静默改为 True ──
EXPLICIT_NETWORK_AUTHORIZATION: bool = False


class NetworkAccessDisabledError(Exception):
    """尝试进行未授权的网络访问时抛出。"""


class LineageMissingError(Exception):
    """请求的资产没有血缘记录或状态不可用于正式研究时抛出。"""


class MarketDataProvider(ABC):
    """所有数据源的抽象基类。

    子类必须实现四个 get_* 方法，统一返回研究域 DataFrame
    （至少包含 CANONICAL_COLUMNS 规范列）。
    """

    # 子类覆盖：所需 token 环境变量名（用于授权校验）
    required_token_env: str = "DATA_PROVIDER_TOKEN"

    # 子类覆盖：provider 标识
    provider_name: str = "base"

    def _network_allowed(self) -> bool:
        """网络是否经授权且具备 token。

        显式授权开关 + 环境变量 token 同时存在才允许，否则视为禁用。
        """
        return bool(
            EXPLICIT_NETWORK_AUTHORIZATION and os.environ.get(self.required_token_env)
        )

    def _assert_network_allowed(self, op: str) -> None:
        """若未授权，抛出 NetworkAccessDisabledError。"""
        if not self._network_allowed():
            raise NetworkAccessDisabledError(
                f"[{self.provider_name}] 网络访问被禁用：操作 '{op}' 未获授权。"
                f"需要 EXPLICIT_NETWORK_AUTHORIZATION=True 且设置 "
                f"{self.required_token_env}。"
            )

    @abstractmethod
    def get_index_daily(
        self, index_code: str, start: str, end: str, adjustment: str = "QFQ"
    ) -> pd.DataFrame:
        """指数日线。返回研究域 DataFrame。"""
        raise NotImplementedError

    @abstractmethod
    def get_etf_daily(
        self, symbol: str, start: str, end: str, adjustment: str = "QFQ"
    ) -> pd.DataFrame:
        """ETF 日线。返回研究域 DataFrame。"""
        raise NotImplementedError

    @abstractmethod
    def get_etf_metadata(self) -> dict:
        """ETF 元数据，dict keyed by symbol。"""
        raise NotImplementedError

    @abstractmethod
    def get_trading_calendar(self, start: str, end: str) -> list[str]:
        """交易日历（YYYY-MM-DD 升序）。"""
        raise NotImplementedError

    @staticmethod
    def _ensure_canonical_columns(df: pd.DataFrame) -> pd.DataFrame:
        """确保返回帧包含规范列（用于自检，不扩展历史）。"""
        missing = [c for c in CANONICAL_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"返回帧缺少规范列: {missing}")
        return df
