"""原始 provider 帧 -> 研究域规范帧的归一化工具。

约束：
- 仅做列对齐 / 单位统一 / 元数据填充，绝不伪造或扩展历史（不补行、不插值）。
- raw_hash 取原始帧内容的 sha256 hex（用于血缘校验）。
- retrieved_at 取归一化时刻（UTC ISO）；冻结本地数据集在 LocalSnapshotProvider
  中保留原始 retrieved_at，不重新归一化，以维护溯源。
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from .schemas import CANONICAL_COLUMNS, UNKNOWN

# 原始列 -> 规范列的常见别名映射
_AMOUNT_ALIASES = ["amount", "turnover", "amt", "trade_val"]
_VOLUME_ALIASES = ["volume", "vol", "volumn", "trade_vol"]


def _resolve_alias(df: pd.DataFrame, aliases: list[str]) -> Optional[str]:
    for a in aliases:
        if a in df.columns:
            return a
    return None


def _raw_content_hash(raw_df: pd.DataFrame) -> str:
    """对原始帧内容求 sha256（排序列以保证确定性）。"""
    ordered = raw_df[list(raw_df.columns)].copy()
    ordered = ordered.sort_index(axis=1)
    blob = ordered.to_csv(index=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def normalize_raw_to_research(
    raw_df: pd.DataFrame,
    asset_type: str,
    source_provider: str,
    adjustment: str,
) -> pd.DataFrame:
    """将原始 provider 帧转为研究域规范帧。

    参数：
        raw_df: 远程 provider 返回的原始 OHLCV 帧。
        asset_type: schemas 中的资产类型常量（ETF/INDEX/...）。
        source_provider: provider 标识（如 "tushare"）。
        adjustment: 复权类型常量（NONE/QFQ/HFQ/UNKNOWN）。

    返回：
        含 CANONICAL_COLUMNS 及 source/retrieved_at/raw_hash/adjustment 的 DataFrame。
    """
    if raw_df is None or len(raw_df) == 0:
        raise ValueError("raw_df 为空，无法归一化（禁止伪造空数据集）")

    df = pd.DataFrame()
    # 日期
    if "date" not in raw_df.columns:
        raise ValueError("raw_df 缺少 date 列")
    df["date"] = raw_df["date"].astype(str)

    # 价格列（直接采用，不扩展历史）
    for col in ["open", "high", "low", "close"]:
        if col not in raw_df.columns:
            raise ValueError(f"raw_df 缺少必需列: {col}")
        df[col] = pd.to_numeric(raw_df[col], errors="coerce")

    # 成交量
    vol_src = _resolve_alias(raw_df, _VOLUME_ALIASES)
    df["volume"] = (
        pd.to_numeric(raw_df[vol_src], errors="coerce") if vol_src else 0.0
    )

    # 成交额（turnover/amount 等）
    amt_src = _resolve_alias(raw_df, _AMOUNT_ALIASES)
    df["amount"] = (
        pd.to_numeric(raw_df[amt_src], errors="coerce") if amt_src else 0.0
    )

    # 元数据
    df["source"] = source_provider
    df["retrieved_at"] = datetime.now(timezone.utc).isoformat()
    df["raw_hash"] = _raw_content_hash(raw_df)
    df["adjustment"] = adjustment if adjustment else UNKNOWN

    # 不保证按日期排序（保留原始顺序），由校验器负责单调性检查。
    return df.reset_index(drop=True)
