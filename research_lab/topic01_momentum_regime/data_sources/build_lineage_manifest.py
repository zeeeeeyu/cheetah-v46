"""离线构建 datasets/lineage_manifest.json（仅基于已冻结的本地数据，不触网、不伪造）。

对 data/market/*.csv 逐个计算 sha256、首尾日期、行数、上市日代理、复权缺口，生成
LineageRecord 列表写入 datasets/lineage_manifest.json。

诚实声明：
- 真实 Legacy 端点不可知，source_endpoint_id / request_parameters 记为 "NOT_FETCHED"。
- 现有冻结 CSV 无复权列，adjustment 记为 "UNKNOWN"（缺口记录于 T01_DATA_GAPS.md），
  校验器会据此标 ADJUSTMENT_MISMATCH；但 LocalSnapshotProvider 仍允许
  validation_status="FROZEN" 载入。
- 不修改任何行情文件，仅读取并计算血缘元数据。
"""
from __future__ import annotations

import hashlib
import json
import os

import pandas as pd

from .lineage import LineageManifest
from .schemas import LineageRecord, SCHEMA_VERSION, UNKNOWN

_HERE = os.path.dirname(os.path.abspath(__file__))
_TOPIC_DIR = os.path.normpath(os.path.join(_HERE, ".."))
_DATA_ROOT = os.path.normpath(os.path.join(_HERE, "..", "..", "..", "data"))
_MANIFEST = os.path.join(_TOPIC_DIR, "datasets", "lineage_manifest.json")
_MARKET_DIR = os.path.join(_DATA_ROOT, "market")


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def build() -> LineageManifest:
    man = LineageManifest()
    if not os.path.isdir(_MARKET_DIR):
        return man
    for fn in sorted(os.listdir(_MARKET_DIR)):
        if not fn.endswith(".csv"):
            continue
        sym = fn[:-4]
        fp = os.path.join(_MARKET_DIR, fn)
        df = pd.read_csv(fp)
        if "date" not in df.columns or len(df) == 0:
            continue
        first = str(df["date"].iloc[0])
        last = str(df["date"].iloc[-1])
        retrieved = (
            str(df["retrieved_at"].iloc[0]) if "retrieved_at" in df.columns else ""
        )
        source = (
            str(df["source"].iloc[0]) if "source" in df.columns else "legacy_direct_etf_cache_seed"
        )
        man.add_record(LineageRecord(
            dataset_id=f"market/{sym}",
            asset_id=sym,
            asset_type="ETF",
            source_provider=source,
            source_endpoint_id="NOT_FETCHED",
            request_parameters="NOT_FETCHED",
            retrieved_at=retrieved,
            adjustment=UNKNOWN,  # 冻结 CSV 无复权列（缺口）
            listing_date=first,  # 代理：CSV 首条记录
            first_date=first,
            last_date=last,
            row_count=int(len(df)),
            sha256=_sha256_file(fp),
            schema_version=SCHEMA_VERSION,
            validation_status="FROZEN",
        ))
    return man


def main() -> None:
    man = build()
    man.save(_MANIFEST)
    print(f"已写入血缘清单: {_MANIFEST}（{len(man.all_records())} 条记录）")
    print(f"dataset_version = {man.dataset_version()}")


if __name__ == "__main__":
    main()
