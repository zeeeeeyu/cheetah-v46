"""Phase-1.5 离线数据获取 CLI（OFFLINE-ONLY，绝不触网）。

本模块导入时不做任何网络请求。远程库（tushare/akshare）一律惰性 import，且仅在
显式授权时才会被引用，而本 CLI 永不在未授权情况下发起请求。

用法：
    python fetch_phase15_data.py                 # 默认 --offline，打印 dry-run
    python fetch_phase15_data.py --enable-network # 打印 NETWORK_NOT_AUTHORIZED 并退出
    python fetch_phase15_data.py --build-fixtures # 生成本地 MOCK 夹具（不触网）

说明：
- fetch_offline_dryrun() 仅依据本地元数据（冻结数据集目录 / 血缘清单）打印
  “将会获取哪些数据集”，不发起任何网络调用。
- --enable-network 被显式传入时，立即打印 NETWORK_NOT_AUTHORIZED 并退出，
  绝不发起请求。
"""
from __future__ import annotations

import argparse
import os
from typing import Optional

# 注意：此处不 import tushare / akshare，避免任何触网风险。

_HERE = os.path.dirname(os.path.abspath(__file__))
_FIXTURES_DIR = os.path.join(_HERE, "fixtures")
_TOPIC_DIR = os.path.normpath(os.path.join(_HERE, ".."))
_DATA_ROOT = os.path.normpath(os.path.join(_HERE, "..", "..", "..", "data"))
_MANIFEST = os.path.join(_TOPIC_DIR, "datasets", "lineage_manifest.json")


# ───────────────────────────── 夹具生成 ─────────────────────────────
def build_fixtures(fixtures_dir: Optional[str] = None) -> list[str]:
    """将 10 个 MOCK 夹具写入磁盘（明确非真实行情）。返回写入的文件路径列表。"""
    root = os.path.normpath(fixtures_dir or _FIXTURES_DIR)
    os.makedirs(os.path.join(root, "index"), exist_ok=True)
    os.makedirs(os.path.join(root, "market"), exist_ok=True)

    HEADER = "date,open,high,low,close,volume,amount,adjustment,source,retrieved_at,source_asset_id"
    written: list[str] = []

    def write(rel: str, rows: list[str]) -> None:
        fp = os.path.join(root, rel)
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        with open(fp, "w", encoding="utf-8") as fh:
            fh.write(HEADER + "\n")
            fh.write("\n".join(rows) + "\n")
        written.append(fp)

    META = "2026-08-07T00:00:00"  # 仅 retrieved_at 字段（source 已在各行显式给出）

    # (1) 正常指数
    write("index/000300_mock.csv", [
        f"2026-01-05,3800.12,3810.5,3795.3,3805.2,150000000,5.7e10,QFQ,index,{META},000300",
        f"2026-01-06,3805.2,3820.1,3800.0,3815.6,162000000,6.1e10,QFQ,index,{META},000300",
        f"2026-01-07,3815.6,3818.0,3790.2,3798.4,140000000,5.4e10,QFQ,index,{META},000300",
    ])

    # (2) 正常 ETF
    write("market/510300_mock.csv", [
        f"2026-01-05,3.850,3.862,3.840,3.855,5000000,1.9e10,QFQ,etf,{META},510300",
        f"2026-01-06,3.855,3.870,3.850,3.868,5200000,2.0e10,QFQ,etf,{META},510300",
        f"2026-01-07,3.868,3.875,3.860,3.872,4800000,1.8e10,QFQ,etf,{META},510300",
    ])

    # (3) 早于上市日（listing_date=2026-01-06）
    write("market/510880_prelisting.csv", [
        f"2026-01-05,4.500,4.520,4.490,4.510,900000,4.0e9,QFQ,etf,{META},510880",  # < listing
        f"2026-01-06,4.520,4.540,4.510,4.530,950000,4.2e9,QFQ,etf,{META},510880",
        f"2026-01-07,4.530,4.550,4.520,4.545,920000,4.1e9,QFQ,etf,{META},510880",
    ])

    # (4) 重复日期（2026-01-06 出现两次）
    write("market/515000_dupdate.csv", [
        f"2026-01-05,1.200,1.210,1.195,1.205,300000,3.6e8,QFQ,etf,{META},515000",
        f"2026-01-06,1.205,1.215,1.200,1.208,310000,3.7e8,QFQ,etf,{META},515000",
        f"2026-01-06,1.208,1.218,1.203,1.210,315000,3.8e8,QFQ,etf,{META},515000",
        f"2026-01-07,1.210,1.220,1.205,1.212,305000,3.6e8,QFQ,etf,{META},515000",
    ])

    # (5) 未来日期（> 2026-08-07）
    write("market/512660_future.csv", [
        f"2026-01-05,2.000,2.010,1.995,2.005,200000,4.0e8,QFQ,etf,{META},512660",
        f"2026-01-06,2.005,2.015,2.000,2.008,210000,4.2e8,QFQ,etf,{META},512660",
        f"2026-08-10,2.100,2.120,2.090,2.110,250000,5.2e8,QFQ,etf,{META},512660",
    ])

    # (6) 复权冲突两源（同日期不同复权/价格）
    write("market/515030_adj_qfq.csv", [
        f"2026-01-05,2.000,2.010,1.995,2.000,400000,8.0e8,QFQ,etf,{META},515030",
        f"2026-01-06,2.000,2.020,1.998,2.010,410000,8.2e8,QFQ,etf,{META},515030",
        f"2026-01-07,2.010,2.025,2.005,2.015,405000,8.1e8,QFQ,etf,{META},515030",
    ])
    write("market/515030_adj_hfq.csv", [
        f"2026-01-05,3.500,3.515,3.490,3.500,400000,1.4e9,HFQ,etf,{META},515030",
        f"2026-01-06,3.500,3.530,3.495,3.515,410000,1.44e9,HFQ,etf,{META},515030",
        f"2026-01-07,3.515,3.540,3.505,3.525,405000,1.42e9,HFQ,etf,{META},515030",
    ])

    # (7) 主/次源价格不匹配（约 5% 偏差）
    write("market/518880_primary.csv", [
        f"2026-01-05,1.000,1.005,0.998,1.000,600000,6.0e8,QFQ,etf,{META},518880",
        f"2026-01-06,1.000,1.012,0.999,1.010,610000,6.1e8,QFQ,etf,{META},518880",
        f"2026-01-07,1.010,1.022,1.008,1.020,605000,6.2e8,QFQ,etf,{META},518880",
    ])
    write("market/518880_secondary.csv", [
        f"2026-01-05,1.050,1.055,1.048,1.050,600000,6.3e8,QFQ,etf,{META},518880",
        f"2026-01-06,1.050,1.063,1.049,1.061,610000,6.4e8,QFQ,etf,{META},518880",
        f"2026-01-07,1.061,1.074,1.059,1.072,605000,6.5e8,QFQ,etf,{META},518880",
    ])

    # (9) hash 不匹配（内容正常，但 lineage_mismatch.json 记录错误 sha256）
    write("market/510050_hashmismatch.csv", [
        f"2026-01-05,3.200,3.210,3.195,3.205,700000,2.2e9,QFQ,etf,{META},510050",
        f"2026-01-06,3.205,3.220,3.200,3.215,710000,2.3e9,QFQ,etf,{META},510050",
        f"2026-01-07,3.215,3.230,3.210,3.222,705000,2.25e9,QFQ,etf,{META},510050",
    ])

    # 8/10 类：缺失血缘 / provider 禁用 仅文档说明，无独立 CSV。
    return written


def _write_lineage_mismatch(fixtures_dir: Optional[str] = None) -> str:
    """写入一个 sha256 错误的血缘清单（用于 hash 不匹配夹具测试）。"""
    import json

    root = os.path.normpath(fixtures_dir or _FIXTURES_DIR)
    os.makedirs(root, exist_ok=True)
    fp = os.path.join(root, "lineage_mismatch.json")
    payload = {
        "schema_version": "2026-08-07",
        "records": [
            {
                "dataset_id": "market/510050_hashmismatch",
                "asset_id": "510050",
                "asset_type": "ETF",
                "source_provider": "mock_source",
                "source_endpoint_id": "NOT_FETCHED",
                "request_parameters": "NOT_FETCHED",
                "retrieved_at": "2026-08-07T00:00:00",
                "adjustment": "QFQ",
                "listing_date": None,
                "first_date": "2026-01-05",
                "last_date": "2026-01-07",
                "row_count": 3,
                "sha256": "deadbeef00000000000000000000000000000000000000000000000000000000",
                "schema_version": "2026-08-07",
                "validation_status": "FROZEN",
            }
        ],
    }
    with open(fp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    return fp


# ───────────────────────────── dry-run ─────────────────────────────
def fetch_offline_dryrun() -> dict:
    """仅依据本地冻结元数据打印“将要获取”的数据集。绝不触网。"""
    index_dir = os.path.join(_DATA_ROOT, "index")
    market_dir = os.path.join(_DATA_ROOT, "market")
    index_codes = sorted(
        f[:-4] for f in os.listdir(index_dir) if f.endswith(".csv")
    ) if os.path.isdir(index_dir) else []
    etf_symbols = sorted(
        f[:-4] for f in os.listdir(market_dir) if f.endswith(".csv")
    ) if os.path.isdir(market_dir) else []

    print("[offline dry-run] 仅依据本地冻结元数据，不发起任何网络请求。")
    print(f"  指数数据集 (data/index): {index_codes if index_codes else '(无)'}")
    print(f"  ETF 数据集 (data/market): {etf_symbols if etf_symbols else '(无)'}")
    print(f"  血缘清单: {_MANIFEST} ({'存在' if os.path.exists(_MANIFEST) else '缺失'})")
    print("  说明：离线模式下，回测只使用已冻结的上述本地数据集。")
    return {"index": index_codes, "etf": etf_symbols}


def _cmd_enable_network() -> None:
    """显式要求联网：直接拒绝，绝不发起请求。"""
    print("NETWORK_NOT_AUTHORIZED")
    print("  本轮网络访问被禁用（EXPLICIT_NETWORK_AUTHORIZATION=False）。")
    print("  如需启用，必须由编排方显式设置开关与 token，并经审批。")
    raise SystemExit(0)


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase-1.5 离线数据获取（OFFLINE-ONLY）")
    parser.add_argument("--offline", action="store_true", default=True,
                        help="离线 dry-run（默认）。仅打印将获取的数据集，不触网。")
    parser.add_argument("--enable-network", action="store_true",
                        help="要求联网：将打印 NETWORK_NOT_AUTHORIZED 并退出，绝不请求。")
    parser.add_argument("--build-fixtures", action="store_true",
                        help="生成本地 MOCK 夹具（不触网）。")
    args = parser.parse_args()

    if args.enable_network:
        _cmd_enable_network()
        return

    if args.build_fixtures:
        written = build_fixtures()
        _write_lineage_mismatch()
        print(f"[build-fixtures] 已写入 {len(written)} 个 MOCK 夹具文件 + lineage_mismatch.json")
        for w in written:
            print(f"  - {w}")
        return

    # 默认：offline dry-run
    fetch_offline_dryrun()


if __name__ == "__main__":
    main()
