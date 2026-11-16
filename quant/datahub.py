from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from .config import project_path
from .models import CheckResult, DataStatus

CANONICAL_COLUMNS = [
    "date", "open", "high", "low", "close", "volume", "turnover",
    "source", "source_symbol", "retrieved_at",
]


class DataRepository:
    """Only direct-instrument datasets are accepted in the V4.1 production path."""

    def __init__(self, data_dir: Path | None = None):
        self.data_dir = data_dir or project_path("data", "market")
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, symbol: str) -> Path:
        return self.data_dir / f"{symbol}.csv"

    def load(self, symbol: str) -> pd.DataFrame:
        path = self.path_for(symbol)
        if not path.exists():
            raise FileNotFoundError(f"未找到 {symbol} 的标准化直连 ETF 数据：{path}")
        frame = pd.read_csv(path)
        frame["date"] = pd.to_datetime(frame["date"])
        return frame.sort_values("date").reset_index(drop=True)

    def save(self, symbol: str, frame: pd.DataFrame) -> Path:
        standard = standardize_frame(frame, symbol=symbol)
        path = self.path_for(symbol)
        standard.to_csv(path, index=False, encoding="utf-8-sig")
        return path


def standardize_frame(frame: pd.DataFrame, symbol: str, source: str = "local_csv") -> pd.DataFrame:
    out = frame.copy()
    # Prefer direct ETF OHLC close. Use NAV only when a close column is absent.
    mapping = {
        "日期": "date", "净值日期": "date", "交易日期": "date",
        "开盘": "open", "最高": "high", "最低": "low", "收盘": "close",
        "成交量": "volume", "成交额": "turnover",
    }
    out = out.rename(columns={key: value for key, value in mapping.items() if key in out.columns})
    if "close" not in out.columns and "单位净值" in out.columns:
        out = out.rename(columns={"单位净值": "close"})

    required = {"date", "open", "high", "low", "close", "volume", "turnover"}
    missing = required - set(out.columns)
    if missing:
        raise ValueError(f"无法标准化为可信 OHLCV 数据，缺少字段：{sorted(missing)}")

    out["date"] = pd.to_datetime(out["date"])
    for col in ["open", "high", "low", "close", "volume", "turnover"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    if "source" not in out.columns:
        out["source"] = source
    else:
        out["source"] = out["source"].fillna(source).astype(str)
    if "source_symbol" not in out.columns:
        out["source_symbol"] = str(symbol)
    else:
        out["source_symbol"] = out["source_symbol"].fillna(str(symbol)).astype(str)
    if "retrieved_at" not in out.columns:
        out["retrieved_at"] = datetime.utcnow().isoformat(timespec="seconds")
    else:
        out["retrieved_at"] = out["retrieved_at"].fillna(datetime.utcnow().isoformat(timespec="seconds")).astype(str)
    out = out[CANONICAL_COLUMNS].drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)
    return out


def latest_weekday(as_of: pd.Timestamp) -> pd.Timestamp:
    candidate = pd.Timestamp(as_of).normalize()
    while candidate.weekday() >= 5:
        candidate -= pd.Timedelta(days=1)
    return candidate


def staleness_calendar_days(frame: pd.DataFrame, as_of_date: pd.Timestamp) -> int | None:
    """数据最新日期相对运行日的陈旧日历天数。

    这是信号年龄判定的唯一原语，`quality_gate` 与战术 ETF 引擎共用，
    避免两条生产路径各自实现出不一致的陈旧口径（GAP-2 / REQ-DATA）。

    Returns:
        陈旧天数；frame 为空或缺少 date 列时返回 None（调用方必须按 Fail-Closed 处理，
        不得把 None 当作「新鲜」）。负值表示数据日期晚于运行日预期（疑似时点穿越）。
    """
    if frame is None or frame.empty or "date" not in frame.columns:
        return None
    latest = pd.to_datetime(frame["date"]).max().normalize()
    expected = latest_weekday(pd.Timestamp(as_of_date))
    return int((expected - latest).days)


def quality_gate(
    frame: pd.DataFrame,
    symbol: str,
    as_of_date: pd.Timestamp,
    min_history_days: int,
    max_staleness_trading_days: int,
    require_ohlcv: bool,
    max_single_day_return_abs: float,
) -> CheckResult:
    reasons: list[str] = []
    metrics: dict[str, float | str | int] = {}

    if frame is None or frame.empty:
        return CheckResult(DataStatus.BLOCK, ["数据为空"], metrics)

    mandatory = {"date", "close", "source_symbol"}
    if require_ohlcv:
        mandatory |= {"open", "high", "low", "volume", "turnover"}
    missing = mandatory - set(frame.columns)
    if missing:
        return CheckResult(DataStatus.BLOCK, [f"缺少必要字段：{sorted(missing)}"], metrics)

    if (frame["source_symbol"].astype(str) != str(symbol)).any():
        return CheckResult(DataStatus.BLOCK, ["source_symbol 与交易标的不一致：禁止代理资产进入生产路径"], metrics)

    if frame["date"].duplicated().any():
        reasons.append("存在重复交易日期，已要求人工修复")
    if not frame["date"].is_monotonic_increasing:
        reasons.append("日期不是单调递增，已要求人工修复")

    numeric_columns = ["close"] + (["open", "high", "low", "volume", "turnover"] if require_ohlcv else [])
    if frame[numeric_columns].isna().any().any():
        reasons.append("存在空值")
    if (frame[["open", "high", "low", "close"]] <= 0).any().any():
        reasons.append("存在非正价格")
    if ((frame["high"] < frame[["open", "close", "low"]].max(axis=1)) | (frame["low"] > frame[["open", "close", "high"]].min(axis=1))).any():
        reasons.append("OHLC 逻辑不一致")
    if (frame[["volume", "turnover"]] < 0).any().any():
        reasons.append("存在负成交量或负成交额")

    latest = pd.to_datetime(frame["date"]).max().normalize()
    expected = latest_weekday(pd.Timestamp(as_of_date))
    stale_calendar_days = staleness_calendar_days(frame, as_of_date)
    metrics["latest_date"] = str(latest.date())
    metrics["expected_weekday"] = str(expected.date())
    metrics["stale_calendar_days"] = stale_calendar_days
    metrics["rows"] = int(len(frame))

    if latest > expected:
        reasons.append("数据日期晚于运行日期，疑似时点穿越")
    if stale_calendar_days > max_staleness_trading_days:
        reasons.append(f"数据滞后：最新 {latest.date()}，运行日预期至少 {expected.date()}")
    if len(frame) < min_history_days:
        reasons.append(f"历史长度不足：{len(frame)} < {min_history_days}")

    returns = frame["close"].pct_change(fill_method=None).dropna()
    if not returns.empty and returns.abs().max() > max_single_day_return_abs:
        reasons.append(f"存在单日绝对收益率超过 {max_single_day_return_abs:.0%} 的异常点")

    median_turnover = float(frame["turnover"].tail(20).median()) if len(frame) >= 20 else np.nan
    metrics["median_turnover_20d"] = median_turnover

    critical_fragments = [
        "缺少", "source_symbol", "重复", "单调", "空值", "非正", "OHLC", "负成交", "滞后", "穿越", "历史长度", "异常点",
    ]
    status = DataStatus.BLOCK if any(any(token in reason for token in critical_fragments) for reason in reasons) else DataStatus.PASS_
    return CheckResult(status=status, reasons=reasons, metrics=metrics)


def refresh_direct_etf_from_akshare(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    """Optional direct-ETF refresh. No proxying, IP rotation, retries, or fallback data mode."""
    try:
        import akshare as ak  # type: ignore
    except ImportError as exc:
        raise RuntimeError("未安装 akshare；请改用本地授权数据或安装可选依赖。") from exc

    raw = ak.fund_etf_hist_em(
        symbol=str(symbol), period="daily",
        start_date=pd.Timestamp(start_date).strftime("%Y%m%d"),
        end_date=pd.Timestamp(end_date).strftime("%Y%m%d"),
        adjust="hfq",
    )
    if raw is None or raw.empty:
        raise RuntimeError(f"AKShare 未返回 {symbol} 的直连 ETF 数据。系统不会降级或替换为其他资产。")
    return standardize_frame(raw, symbol=symbol, source="akshare_direct_etf")
