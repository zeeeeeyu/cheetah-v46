"""Topic-01 数据加载器：Point-in-Time 安全，含上市日期守卫。

设计约束（Topic-01 §三、§六、§七）：
- T 日只能读取 T 及之前真实可获得的信息，禁止未来泄漏。
- ETF 不得早于实际上市日期进入 Universe（本仓库 etf_master.csv 无上市日期列，
  故以「CSV 首条记录日期」作为上市日期代理；该缺口记录在 T01_DATA_GAPS.md）。
- 基金 NAV 不得伪造成 OHLC；本基金层研究暂不纳入第一阶段。

数据源：quant_v4_6/data/market/*.csv（只读）。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

# 数据根目录：research_lab/topic01_momentum_regime -> quant_v4_6/data（上两级）
_HERE = os.path.dirname(os.path.abspath(__file__))
_DATA_ROOT = os.path.normpath(os.path.join(_HERE, "..", "..", "data"))
_MARKET_DIR = os.path.join(_DATA_ROOT, "market")
_METADATA_DIR = os.path.join(_DATA_ROOT, "metadata")


@dataclass
class ETFMeta:
    symbol: str
    name: str
    asset_class: str
    group: str
    active: bool
    listing_date: str | None = None  # 代理：CSV 首条记录日期
    first_date: str | None = None
    last_date: str | None = None


def load_etf_master() -> dict[str, ETFMeta]:
    """读取 etf_master.csv，并以 CSV 首末日期补充元数据。"""
    path = os.path.join(_METADATA_DIR, "etf_master.csv")
    metas: dict[str, ETFMeta] = {}
    if not os.path.exists(path):
        return metas
    df = pd.read_csv(path)
    for _, row in df.iterrows():
        sym = str(row["symbol"]).strip()
        metas[sym] = ETFMeta(
            symbol=sym,
            name=str(row.get("name", sym)),
            asset_class=str(row.get("asset_class", "")),
            group=str(row.get("group", "")),
            active=bool(int(row.get("active", 1))),
        )
    # 用实际 CSV 补充首末日期（作为上市日期代理）
    for sym, meta in metas.items():
        fp = os.path.join(_MARKET_DIR, f"{sym}.csv")
        if os.path.exists(fp):
            f = pd.read_csv(fp)
            if "date" in f.columns and len(f) > 0:
                meta.first_date = str(f["date"].iloc[0])
                meta.last_date = str(f["date"].iloc[-1])
                meta.listing_date = meta.first_date  # 代理
    return metas


def load_etf_prices(symbol: str) -> pd.DataFrame:
    """加载单只 ETF 的 OHLCV，按日期升序，date 列保留字符串。"""
    fp = os.path.join(_MARKET_DIR, f"{symbol}.csv")
    if not os.path.exists(fp):
        raise FileNotFoundError(f"ETF 行情不存在: {fp}")
    df = pd.read_csv(fp)
    df = df.sort_values("date").reset_index(drop=True)
    return df


def load_all_etf(metas: dict[str, ETFMeta] | None = None) -> dict[str, pd.DataFrame]:
    """加载全部活跃 ETF 行情。"""
    metas = metas or load_etf_master()
    out: dict[str, pd.DataFrame] = {}
    for sym, m in metas.items():
        if not m.active:
            continue
        try:
            out[sym] = load_etf_prices(sym)
        except FileNotFoundError:
            continue
    return out


def window_as_of(frame: pd.DataFrame, as_of: str) -> pd.DataFrame:
    """返回截至 as_of（含）的历史窗口，强制 Point-in-Time。

    任何派生特征只能基于该窗口计算，禁止引用 as_of 之后的任何行。
    """
    f = frame.copy()
    f = f[f["date"] <= as_of]
    return f.reset_index(drop=True)


def effective_universe(
    metas: dict[str, ETFMeta],
    as_of: str,
    restrict_to: list[str] | None = None,
) -> list[str]:
    """返回 as_of 当日已上市的 ETF 池。

    上市日期守卫：任何 ETF 不得早于其 listing_date 进入 Universe。
    """
    pool: list[str] = []
    for sym, m in metas.items():
        if restrict_to and sym not in restrict_to:
            continue
        if not m.active:
            continue
        listing = m.listing_date or m.first_date
        if listing is None:
            continue
        if as_of >= listing:  # 已上市
            pool.append(sym)
    return sorted(pool)


def build_dataset_manifest(metas: dict[str, ETFMeta], frames: dict[str, pd.DataFrame]) -> dict[str, Any]:
    """生成 datasets/manifest，记录数据版本与缺口。"""
    assets = []
    for sym, m in metas.items():
        f = frames.get(sym)
        assets.append({
            "symbol": sym,
            "name": m.name,
            "asset_class": m.asset_class,
            "group": m.group,
            "listing_date_proxy": m.listing_date,
            "first_date": m.first_date,
            "last_date": m.last_date,
            "rows": len(f) if f is not None else 0,
        })
    all_dates = sorted({d for f in frames.values() for d in f["date"].tolist()})
    return {
        "source": "quant_v4_6/data/market (legacy_direct_etf_cache_seed)",
        "asset_count": len(assets),
        "global_first_date": all_dates[0] if all_dates else None,
        "global_last_date": all_dates[-1] if all_dates else None,
        "trading_days": len(all_dates),
        "index_layer_present": False,
        "fund_layer_in_phase1": False,
        "assets": assets,
        "gaps": [
            "无指数层数据（上证综指/沪深300指数/中证500/中证1000/创业板均缺失）",
            "etf_master.csv 无真实上市日期列，listing_date 以 CSV 首条记录代理",
            "样本窗仅约 133 个交易日（2025-12-15~2026-07-03），对状态归因严重不足",
        ],
    }


def write_manifest(manifest: dict[str, Any], path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
