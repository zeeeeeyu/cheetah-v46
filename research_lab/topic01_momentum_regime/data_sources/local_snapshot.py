"""LocalSnapshotProvider：只读冻结本地数据集，研究回测唯一允许的数据接口。

安全边界：
- 仅读取 frozen 本地数据集（默认 root = quant_v4_6/data，可配置）。
- 构造时强制加载血缘清单；若请求资产无记录或 validation_status 不在
  {FROZEN, OK, VALID}，抛出 LineageMissingError，拒绝用于正式研究。
- Point-in-Time：仅返回 [start, end] 内（含 end）的行，且绝不返回晚于今天
  的日期（fail-closed）。不发起任何网络请求。
- index 数据读取 data/index/<code>.csv；ETF 读取 data/market/<symbol>.csv。
"""
from __future__ import annotations

import hashlib
import os
from typing import Optional

import pandas as pd

from .base import LineageMissingError, MarketDataProvider
from .lineage import LineageManifest
from .schemas import (
    CANONICAL_COLUMNS,
    ETF,
    FUND_NAV,
    INDEX,
    INDEX_PROXY,
    QFQ,
    UNKNOWN,
)

# 允许用于正式研究的血缘状态
_VALID_STATUSES = {"FROZEN", "OK", "VALID"}

# 研究环境「今天」（PIT fail-closed 锚点）
_TODAY = "2026-08-07"

_HERE = os.path.dirname(os.path.abspath(__file__))
# data_sources -> topic01_momentum_regime -> research_lab -> quant_v4_6
_TOPIC_DIR = os.path.normpath(os.path.join(_HERE, ".."))
_DEFAULT_DATA_ROOT = os.path.normpath(os.path.join(_HERE, "..", "..", "..", "data"))
_DEFAULT_MANIFEST = os.path.join(_TOPIC_DIR, "datasets", "lineage_manifest.json")


class LocalSnapshotProvider(MarketDataProvider):
    """冻结本地快照 provider。

    参数：
        data_root: 数据集根目录（默认 quant_v4_6/data）。
        manifest_path: 血缘清单路径（默认 datasets/lineage_manifest.json）。
        as_of: 全局 PIT 截断点（可选，默认 None -> 以 end 为边界）。
    """

    provider_name = "local_snapshot"
    required_token_env = "LOCAL_SNAPSHOT_DISABLED"  # 本地源不需要远程 token

    def __init__(
        self,
        data_root: Optional[str] = None,
        manifest_path: Optional[str] = None,
        as_of: Optional[str] = None,
    ):
        self.data_root = os.path.normpath(data_root or _DEFAULT_DATA_ROOT)
        self.manifest_path = manifest_path or _DEFAULT_MANIFEST
        self.as_of = as_of
        if not os.path.exists(self.manifest_path):
            raise FileNotFoundError(
                f"血缘清单不存在: {self.manifest_path}。请先构建 lineage_manifest.json。"
            )
        self.manifest = LineageManifest.load(self.manifest_path)
        self._market_dir = os.path.join(self.data_root, "market")
        self._index_dir = os.path.join(self.data_root, "index")

    # ── 血缘守卫 ──
    def _guard_lineage(self, asset_id: str) -> None:
        rec = self.manifest.get_record(asset_id)
        if rec is None:
            raise LineageMissingError(
                f"资产 {asset_id} 无血缘记录，拒绝加载（缺失 lineage）。"
            )
        if rec.validation_status not in _VALID_STATUSES:
            raise LineageMissingError(
                f"资产 {asset_id} 血缘状态为 {rec.validation_status}，"
                f"不在允许集合 {sorted(_VALID_STATUSES)} 内，拒绝用于正式研究。"
            )

    # ── 读取 + 列对齐 + PIT 截断 ──
    def _read_frozen(self, path: str, asset_id: str, asset_type: str) -> pd.DataFrame:
        if not os.path.exists(path):
            raise FileNotFoundError(f"冻结数据集不存在: {path}")
        raw = pd.read_csv(path)
        with open(path, "rb") as fh:
            raw_hash = hashlib.sha256(fh.read()).hexdigest()

        df = pd.DataFrame()
        df["date"] = raw["date"].astype(str)
        for col in ["open", "high", "low", "close"]:
            df[col] = pd.to_numeric(raw[col], errors="coerce")
        df["volume"] = pd.to_numeric(
            raw["volume"] if "volume" in raw.columns else 0.0, errors="coerce"
        )
        # turnover / amount 对齐
        if "amount" in raw.columns:
            df["amount"] = pd.to_numeric(raw["amount"], errors="coerce")
        elif "turnover" in raw.columns:
            df["amount"] = pd.to_numeric(raw["turnover"], errors="coerce")
        else:
            df["amount"] = 0.0

        # 溯源元数据（保留冻结快照原始值）
        df["source"] = raw["source"].astype(str).iloc[0] if "source" in raw.columns else "local_snapshot"
        df["retrieved_at"] = (
            raw["retrieved_at"].astype(str).iloc[0] if "retrieved_at" in raw.columns else ""
        )
        rec = self.manifest.get_record(asset_id)
        df["adjustment"] = rec.adjustment if rec else UNKNOWN
        df["asset_id"] = asset_id
        df["raw_hash"] = raw_hash

        # Point-in-Time：按 [start? 由调用方传 end] 截断
        # 这里保证不晚于今天；具体窗口由 get_* 的 end/as_of 处理。
        df = df[df["date"] <= _TODAY].reset_index(drop=True)
        return df

    def _window(self, df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
        as_of = self.as_of or end
        cut = min(as_of, end, _TODAY)
        out = df[(df["date"] >= str(start)) & (df["date"] <= str(cut))].copy()
        return out.reset_index(drop=True)

    # ── 接口实现 ──
    def get_index_daily(
        self, index_code: str, start: str, end: str, adjustment: str = "QFQ"
    ) -> pd.DataFrame:
        self._guard_lineage(index_code)
        path = os.path.join(self._index_dir, f"{index_code}.csv")
        df = self._read_frozen(path, index_code, INDEX)
        return self._window(df, start, end)

    def get_etf_daily(
        self, symbol: str, start: str, end: str, adjustment: str = "QFQ"
    ) -> pd.DataFrame:
        self._guard_lineage(symbol)
        path = os.path.join(self._market_dir, f"{symbol}.csv")
        df = self._read_frozen(path, symbol, ETF)
        return self._window(df, start, end)

    def get_etf_metadata(self) -> dict:
        """读取 data/metadata/etf_master.csv（若存在），按 symbol 索引。"""
        meta_path = os.path.join(self.data_root, "metadata", "etf_master.csv")
        out: dict = {}
        if not os.path.exists(meta_path):
            return out
        m = pd.read_csv(meta_path)
        for _, row in m.iterrows():
            sym = str(row["symbol"]).strip()
            out[sym] = {
                "symbol": sym,
                "name": str(row.get("name", sym)),
                "asset_class": str(row.get("asset_class", "")),
                "group": str(row.get("group", "")),
                "active": bool(int(row.get("active", 1))),
                "listing_date": str(row["listing_date"]) if "listing_date" in row and pd.notna(row.get("listing_date")) else None,
            }
        return out

    def get_trading_calendar(self, start: str, end: str) -> list[str]:
        """交易日历：取本地 market 数据在窗口内的全部日期并集（升序）。

        注：本地未单独维护交易日历文件，此处以可用数据集日期并集近似，
        仅供研究便利；正式校验应传入显式交易日历。
        """
        dates: set[str] = set()
        if os.path.isdir(self._market_dir):
            for fn in os.listdir(self._market_dir):
                if not fn.endswith(".csv"):
                    continue
                try:
                    f = pd.read_csv(os.path.join(self._market_dir, fn))
                    dates.update(str(d) for d in f["date"].tolist())
                except Exception:
                    continue
        if os.path.isdir(self._index_dir):
            for fn in os.listdir(self._index_dir):
                if not fn.endswith(".csv"):
                    continue
                try:
                    f = pd.read_csv(os.path.join(self._index_dir, fn))
                    dates.update(str(d) for d in f["date"].tolist())
                except Exception:
                    continue
        return sorted(d for d in dates if str(start) <= d <= str(end) and d <= _TODAY)
