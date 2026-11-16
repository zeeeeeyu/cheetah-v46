from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable
import json
import time

import numpy as np
import pandas as pd

from .config import project_path
from .models import CheckResult, DataStatus

FUND_NAV_COLUMNS = [
    "date", "nav", "nav_basis", "daily_return_pct", "purchase_status", "redemption_status",
    "source", "source_symbol", "retrieved_at",
]


def normalize_fund_code(value: object) -> str:
    """Preserve six-digit mainland mutual-fund codes across CSV round-trips."""
    raw = str(value).strip()
    if raw.endswith(".0") and raw[:-2].isdigit():
        raw = raw[:-2]
    return raw.zfill(6) if raw.isdigit() and len(raw) <= 6 else raw


class FundNAVRepository:
    """Repository for direct open-end fund NAV series.

    The production path accepts only the same fund code as source_symbol. It never falls back
    to an ETF proxy and it never fabricates OHLCV fields from NAV data.
    """

    def __init__(self, data_dir: Path | None = None):
        self.data_dir = data_dir or project_path("data", "fund_nav")
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, symbol: str) -> Path:
        return self.data_dir / f"{normalize_fund_code(symbol)}.csv"

    def exists(self, symbol: str) -> bool:
        return self.path_for(symbol).exists()

    def load(self, symbol: str) -> pd.DataFrame:
        symbol = normalize_fund_code(symbol)
        path = self.path_for(symbol)
        if not path.exists():
            raise FileNotFoundError(f"未找到 {symbol} 的直接场外基金净值文件：{path}")
        frame = pd.read_csv(path, dtype={"source_symbol": str})
        frame["date"] = pd.to_datetime(frame["date"])
        if "source_symbol" in frame.columns:
            frame["source_symbol"] = frame["source_symbol"].map(normalize_fund_code)
        return frame.sort_values("date").reset_index(drop=True)

    def latest_date(self, symbol: str) -> pd.Timestamp | None:
        if not self.exists(symbol):
            return None
        frame = self.load(symbol)
        if frame.empty:
            return None
        return pd.to_datetime(frame["date"]).max().normalize()

    def save(self, symbol: str, frame: pd.DataFrame, nav_preference: str = "cumulative_only", source: str = "local_csv") -> Path:
        symbol = normalize_fund_code(symbol)
        standard = standardize_fund_nav(frame, symbol=symbol, nav_preference=nav_preference, source=source)
        path = self.path_for(symbol)
        standard.to_csv(path, index=False, encoding="utf-8-sig")
        return path

    def merge_and_save(self, symbol: str, frame: pd.DataFrame, nav_preference: str = "cumulative_only", source: str = "akshare_direct_fund_nav") -> Path:
        """Merge a refreshed direct-NAV payload into local cache by date.

        Public providers may return the full history on every request. The local repository keeps a
        single normalized series and only refreshes rows for dates returned by the provider.
        """
        symbol = normalize_fund_code(symbol)
        fresh = standardize_fund_nav(frame, symbol=symbol, nav_preference=nav_preference, source=source)
        if self.exists(symbol):
            existing = self.load(symbol)
            merged = pd.concat([existing, fresh], ignore_index=True)
            merged = merged.drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)
        else:
            merged = fresh
        return self.save(symbol, merged, nav_preference=nav_preference, source=source)


@dataclass
class FundRefreshResult:
    symbol: str
    status: str
    latest_before: str | None
    latest_after: str | None
    message: str

    def to_dict(self) -> dict[str, str | None]:
        return {
            "symbol": self.symbol,
            "status": self.status,
            "latest_before": self.latest_before,
            "latest_after": self.latest_after,
            "message": self.message,
        }


def _clean_status(series: pd.Series | None, fallback: str = "UNKNOWN") -> pd.Series:
    if series is None:
        return pd.Series([fallback])
    return series.fillna(fallback).astype(str).replace({"": fallback, "nan": fallback})


def standardize_fund_nav(
    frame: pd.DataFrame,
    symbol: str,
    nav_preference: str = "cumulative_only",
    source: str = "local_csv",
) -> pd.DataFrame:
    """Standardize direct fund NAV data.

    `nav` uses cumulative NAV when available.  Unit NAV is accepted only when the caller explicitly
    permits it; this avoids silently dropping distribution effects from a total-return series.
    """
    out = frame.copy()
    mapping = {
        "净值日期": "date", "日期": "date", "交易日期": "date",
        "单位净值": "unit_nav", "累计净值": "cumulative_nav",
        "日增长率": "daily_return_pct", "日涨幅": "daily_return_pct",
        "申购状态": "purchase_status", "赎回状态": "redemption_status",
    }
    out = out.rename(columns={key: value for key, value in mapping.items() if key in out.columns})
    if "date" not in out.columns:
        raise ValueError("无法标准化场外基金净值：缺少日期/净值日期字段")

    nav_preference = str(nav_preference).lower()
    has_standard_nav = "nav" in out.columns and "nav_basis" in out.columns
    has_cumulative = "cumulative_nav" in out.columns
    has_unit = "unit_nav" in out.columns
    if has_standard_nav:
        basis_values = set(out["nav_basis"].dropna().astype(str))
        if len(basis_values) != 1:
            raise ValueError(f"标准化净值的 nav_basis 不一致：{sorted(basis_values)}")
        basis = next(iter(basis_values))
        if nav_preference == "cumulative_only" and basis != "cumulative_nav":
            raise ValueError("配置要求累计净值，但输入标准化数据不是 cumulative_nav")
        if nav_preference == "unit_only" and basis != "unit_nav":
            raise ValueError("配置要求单位净值，但输入标准化数据不是 unit_nav")
        out["nav"] = pd.to_numeric(out["nav"], errors="coerce")
    elif has_cumulative and nav_preference in {"cumulative_only", "cumulative_preferred"}:
        out["nav"] = pd.to_numeric(out["cumulative_nav"], errors="coerce")
        out["nav_basis"] = "cumulative_nav"
    elif has_unit and nav_preference in {"unit_allowed", "unit_only", "cumulative_preferred"}:
        out["nav"] = pd.to_numeric(out["unit_nav"], errors="coerce")
        out["nav_basis"] = "unit_nav"
    elif has_cumulative and nav_preference == "unit_allowed":
        out["nav"] = pd.to_numeric(out["cumulative_nav"], errors="coerce")
        out["nav_basis"] = "cumulative_nav"
    else:
        available = [name for name in ("累计净值" if has_cumulative else None, "单位净值" if has_unit else None, "标准化nav" if has_standard_nav else None) if name]
        raise ValueError(
            f"无法获得符合 nav_preference={nav_preference} 的净值字段；可用字段：{available or '无'}。"
        )

    out["date"] = pd.to_datetime(out["date"])
    if "daily_return_pct" in out.columns:
        out["daily_return_pct"] = (
            out["daily_return_pct"].astype(str).str.replace("%", "", regex=False).str.strip().replace({"": np.nan, "---": np.nan})
        )
        out["daily_return_pct"] = pd.to_numeric(out["daily_return_pct"], errors="coerce")
    else:
        out["daily_return_pct"] = out["nav"].pct_change(fill_method=None) * 100.0

    if "purchase_status" not in out.columns:
        out["purchase_status"] = "UNKNOWN"
    else:
        out["purchase_status"] = out["purchase_status"].fillna("UNKNOWN").astype(str)
    if "redemption_status" not in out.columns:
        out["redemption_status"] = "UNKNOWN"
    else:
        out["redemption_status"] = out["redemption_status"].fillna("UNKNOWN").astype(str)

    if "source" not in out.columns:
        out["source"] = source
    else:
        out["source"] = out["source"].fillna(source).astype(str)
    if "source_symbol" not in out.columns:
        out["source_symbol"] = normalize_fund_code(symbol)
    else:
        out["source_symbol"] = out["source_symbol"].fillna(str(symbol)).map(normalize_fund_code)
    if "retrieved_at" not in out.columns:
        out["retrieved_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    else:
        out["retrieved_at"] = out["retrieved_at"].fillna(datetime.now(timezone.utc).isoformat(timespec="seconds")).astype(str)

    out = out[FUND_NAV_COLUMNS].drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)
    return out


def fund_nav_quality_gate(
    frame: pd.DataFrame,
    symbol: str,
    as_of_date: pd.Timestamp,
    min_history_days: int,
    max_staleness_calendar_days: int,
    max_single_day_return_abs: float,
    required_nav_basis: str = "cumulative_nav",
    require_direct_symbol_match: bool = True,
    quality_window_trading_days: int | None = None,
) -> CheckResult:
    """Validate a direct fund-NAV series without overstating stale historical artefacts.

    The gate distinguishes two cases:
    1. Problems inside the active signal window: the instrument is blocked for this decision.
    2. Problems only in older history: they are retained as audit warnings, but do not erase an
       otherwise clean current signal window.

    It never fills, repairs, or removes provider data automatically.  The caller decides whether
    a blocked *non-benchmark* instrument should be quarantined or should block the whole portfolio.
    """
    blocking: list[str] = []
    warnings: list[str] = []
    metrics: dict[str, object] = {}
    if frame is None or frame.empty:
        return CheckResult(DataStatus.BLOCK, ["场外基金净值数据为空"], metrics)

    mandatory = {"date", "nav", "nav_basis", "source", "source_symbol", "retrieved_at"}
    missing = mandatory - set(frame.columns)
    if missing:
        return CheckResult(DataStatus.BLOCK, [f"缺少场外基金净值必要字段：{sorted(missing)}"], metrics)

    work = frame.copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce")
    if work["date"].isna().any():
        return CheckResult(DataStatus.BLOCK, ["存在无法解析的净值日期"], metrics)
    if require_direct_symbol_match and (work["source_symbol"].map(normalize_fund_code) != normalize_fund_code(symbol)).any():
        return CheckResult(DataStatus.BLOCK, ["source_symbol 与交易基金代码不一致：禁止 ETF 或其他代理资产进入场外基金路径"], metrics)

    if work["date"].duplicated().any():
        blocking.append("存在重复净值日期，需人工修复")
    if not work["date"].is_monotonic_increasing:
        blocking.append("净值日期不是单调递增，需人工修复")

    nav = pd.to_numeric(work["nav"], errors="coerce")
    if (nav <= 0).any():
        blocking.append("存在非正净值")

    nav_basis_values = set(work["nav_basis"].dropna().astype(str))
    if len(nav_basis_values) != 1:
        blocking.append(f"净值口径不一致：{sorted(nav_basis_values)}")
    elif required_nav_basis == "cumulative_nav" and nav_basis_values != {"cumulative_nav"}:
        blocking.append("当前策略要求累计净值，禁止自动用单位净值替代")

    latest = pd.to_datetime(work["date"]).max().normalize()
    as_of = pd.Timestamp(as_of_date).normalize()
    stale_days = int((as_of - latest).days)
    active_window = max(int(min_history_days), int(quality_window_trading_days or min_history_days))
    recent = work.tail(active_window).copy()
    recent_nav = nav.tail(active_window)
    historic_nav = nav.iloc[:-len(recent)] if len(work) > len(recent) else pd.Series(dtype=float)
    usable_rows = int(nav.notna().sum())

    metrics.update({
        "latest_date": str(latest.date()),
        "as_of_date": str(as_of.date()),
        "stale_calendar_days": stale_days,
        "rows": int(len(work)),
        "usable_rows": usable_rows,
        "quality_window_trading_days": active_window,
        "quality_window_start": str(pd.to_datetime(recent["date"]).min().date()) if not recent.empty else None,
        "recent_nav_missing_count": int(recent_nav.isna().sum()),
        "historical_nav_missing_count": int(historic_nav.isna().sum()),
    })

    if latest > as_of:
        blocking.append("数据日期晚于运行日期，疑似时点穿越")
    if stale_days > max_staleness_calendar_days:
        blocking.append(f"净值数据滞后：最新 {latest.date()}，运行日 {as_of.date()}，超出允许日历日滞后")
    if usable_rows < min_history_days:
        blocking.append(f"可用净值历史不足：{usable_rows} < {min_history_days}")
    if recent_nav.isna().any():
        blocking.append(f"近 {active_window} 个净值观测中存在空值；当前信号窗口不可用")
    elif not historic_nav.empty and historic_nav.isna().any():
        warnings.append(f"较早历史存在 {int(historic_nav.isna().sum())} 个空值，已保留审计记录；当前信号窗口不受影响")

    returns = nav.pct_change(fill_method=None)
    return_frame = pd.DataFrame({"date": work["date"], "return": returns})
    recent_start = pd.to_datetime(recent["date"]).min() if not recent.empty else pd.Timestamp.max
    recent_anomalies = return_frame.loc[
        (return_frame["date"] >= recent_start)
        & return_frame["return"].abs().gt(max_single_day_return_abs),
        ["date", "return"],
    ]
    historical_anomalies = return_frame.loc[
        (return_frame["date"] < recent_start)
        & return_frame["return"].abs().gt(max_single_day_return_abs),
        ["date", "return"],
    ]
    metrics["recent_anomaly_count"] = int(len(recent_anomalies))
    metrics["historical_anomaly_count"] = int(len(historical_anomalies))
    metrics["recent_anomaly_examples"] = [
        {"date": str(pd.Timestamp(row["date"]).date()), "return": float(row["return"])}
        for _, row in recent_anomalies.head(3).iterrows()
    ]
    metrics["historical_anomaly_examples"] = [
        {"date": str(pd.Timestamp(row["date"]).date()), "return": float(row["return"])}
        for _, row in historical_anomalies.head(3).iterrows()
    ]
    if not recent_anomalies.empty:
        blocking.append(
            f"近 {active_window} 个净值观测存在单日绝对净值变化超过 {max_single_day_return_abs:.0%} 的异常点；"
            "请核对分红、拆分或数据错误"
        )
    elif not historical_anomalies.empty:
        warnings.append(
            f"较早历史存在 {len(historical_anomalies)} 个超过 {max_single_day_return_abs:.0%} 的异常跳变；"
            "已隔离为审计提示，不自动修复，也不影响当前信号窗口"
        )

    if blocking:
        return CheckResult(DataStatus.BLOCK, blocking + warnings, metrics)
    if warnings:
        return CheckResult(DataStatus.WARNING, warnings, metrics)
    return CheckResult(DataStatus.PASS_, [], metrics)

def refresh_direct_fund_nav_from_akshare(symbol: str, nav_preference: str = "cumulative_only") -> pd.DataFrame:
    """Optional direct fund-NAV adapter.

    It makes one documented AKShare request, has no retry/rotation/proxy fallback, and raises on failure.
    Because upstream public pages can be unavailable, it should not be treated as a guaranteed production feed.
    """
    try:
        import akshare as ak  # type: ignore
    except ImportError as exc:
        raise RuntimeError("未安装 akshare；请使用本地导出数据或安装可选依赖。") from exc
    indicator = "累计净值走势" if nav_preference in {"cumulative_only", "cumulative_preferred"} else "单位净值走势"
    raw = ak.fund_open_fund_info_em(symbol=str(symbol), indicator=indicator)
    if raw is None or raw.empty:
        raise RuntimeError(f"AKShare 未返回 {symbol} 的直接场外基金净值。系统不会改用 ETF 代理，也不会开启反爬绕过。")
    return standardize_fund_nav(raw, symbol=symbol, nav_preference=nav_preference, source="akshare_direct_fund_nav")


def _refresh_state_path() -> Path:
    path = project_path("state", "fund_data_refresh_state.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load_refresh_state(path: Path) -> dict:
    if not path.exists():
        return {"schema_version": 1, "funds": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("refresh state is not a dict")
        payload.setdefault("schema_version", 1)
        payload.setdefault("funds", {})
        return payload
    except Exception:
        # A corrupt refresh ledger must not make a trading decision fail. It is recreated after
        # the next bounded request and is never used as market data.
        return {"schema_version": 1, "funds": {}}


def _write_refresh_state(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _parse_iso_timestamp(value: object) -> pd.Timestamp | None:
    if not value:
        return None
    try:
        timestamp = pd.Timestamp(str(value))
        if timestamp.tzinfo is not None:
            timestamp = timestamp.tz_convert(None)
        return timestamp
    except Exception:
        return None


def _latest_text(value: pd.Timestamp | None) -> str | None:
    return str(value.date()) if value is not None else None


def auto_refresh_direct_fund_navs(
    symbols: Iterable[str],
    data_config: dict,
    as_of_date: str | pd.Timestamp | None = None,
    *,
    repository: FundNAVRepository | None = None,
    state_path: Path | None = None,
    fetcher: Callable[[str, str], pd.DataFrame] | None = None,
) -> list[FundRefreshResult]:
    """Bounded, direct public-data refresh for a personal fund workflow.

    This is intentionally conservative but practical:
    - one normal direct-fund request per symbol at a time;
    - no proxy, IP rotation, browser automation, or endpoint fallback;
    - cache-first; provider failure keeps a recent cache available for observation only;
    - repeated same-day refresh attempts are throttled through a local ledger.

    It refreshes *direct fund NAV only*. It never uses an ETF proxy and never changes the user's
    portfolio state or tradeability/fee snapshot.
    """
    normalized_symbols = []
    for raw in symbols:
        symbol = normalize_fund_code(raw)
        if symbol and symbol not in normalized_symbols:
            normalized_symbols.append(symbol)

    enabled = bool(data_config.get("auto_refresh_on_run", False)) and bool(data_config.get("allow_network_refresh", False))
    provider_mode = str(data_config.get("provider_mode", "local_only"))
    as_of = pd.Timestamp(as_of_date).normalize() if as_of_date is not None else pd.Timestamp.today().normalize()
    repo = repository or FundNAVRepository()
    state_file = state_path or _refresh_state_path()
    state = _load_refresh_state(state_file)
    entries = state.setdefault("funds", {})
    preference = str(data_config.get("nav_preference", "cumulative_only"))
    max_attempts = max(1, int(data_config.get("max_refresh_attempts_per_day", 2)))
    retry_minutes = max(0, int(data_config.get("min_refresh_retry_minutes", 120)))
    interval = max(0.0, float(data_config.get("request_interval_seconds", 1.0)))
    actual_fetcher = fetcher or refresh_direct_fund_nav_from_akshare

    results: list[FundRefreshResult] = []
    for index, symbol in enumerate(normalized_symbols):
        before = repo.latest_date(symbol)
        before_text = _latest_text(before)
        if not enabled or provider_mode not in {"auto_public_direct", "akshare_direct"}:
            results.append(FundRefreshResult(symbol, "LOCAL_ONLY", before_text, before_text, "自动网络更新未启用；仅读取本地直接净值缓存"))
            continue
        if before is not None and before >= as_of:
            results.append(FundRefreshResult(symbol, "CURRENT", before_text, before_text, "本地直接净值已覆盖本次决策日，无需联网刷新"))
            continue

        record = entries.get(symbol, {})
        record_day = str(record.get("attempt_day", ""))
        attempts = int(record.get("attempts", 0)) if record_day == str(as_of.date()) else 0
        last_attempt = _parse_iso_timestamp(record.get("last_attempt_at"))
        wait_not_over = (
            last_attempt is not None
            and (pd.Timestamp.now() - last_attempt).total_seconds() < retry_minutes * 60
        )
        if attempts >= max_attempts or (attempts > 0 and wait_not_over):
            message = "今日已达到刷新次数上限" if attempts >= max_attempts else f"距上次刷新不足 {retry_minutes} 分钟，继续使用本地缓存"
            results.append(FundRefreshResult(symbol, "THROTTLED", before_text, before_text, message))
            continue

        if index > 0 and interval > 0:
            time.sleep(interval)
        now_text = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            fresh = actual_fetcher(symbol, preference)
            repo.merge_and_save(symbol, fresh, nav_preference=preference, source="akshare_direct_fund_nav")
            after = repo.latest_date(symbol)
            entries[symbol] = {
                "attempt_day": str(as_of.date()),
                "attempts": attempts + 1,
                "last_attempt_at": now_text,
                "status": "UPDATED",
                "latest_date": _latest_text(after),
                "error": None,
            }
            results.append(FundRefreshResult(symbol, "UPDATED", before_text, _latest_text(after), "已用同代码直接基金净值更新本地缓存"))
        except Exception as exc:
            entries[symbol] = {
                "attempt_day": str(as_of.date()),
                "attempts": attempts + 1,
                "last_attempt_at": now_text,
                "status": "FAILED",
                "latest_date": before_text,
                "error": str(exc),
            }
            fallback_message = "网络更新失败；将由后续数据质量闸门判断本地缓存是否仍可仅用于观察"
            results.append(FundRefreshResult(symbol, "FAILED", before_text, before_text, f"{fallback_message}：{exc}"))
    _write_refresh_state(state_file, state)
    return results


def summarize_fund_refresh(results: list[FundRefreshResult]) -> list[str]:
    """Return concise user-facing notes for the one-file daily console."""
    if not results:
        return []
    counts: dict[str, int] = {}
    for item in results:
        counts[item.status] = counts.get(item.status, 0) + 1
    notes: list[str] = []
    if counts.get("UPDATED"):
        notes.append(f"自动更新直接基金净值：{counts['UPDATED']} 只")
    if counts.get("CURRENT"):
        notes.append(f"本地缓存已是当日：{counts['CURRENT']} 只")
    if counts.get("FAILED"):
        notes.append(f"网络更新失败：{counts['FAILED']} 只；不会改用 ETF 代理，是否可继续观察由缓存新鲜度决定")
    if counts.get("THROTTLED"):
        notes.append(f"为避免重复请求，{counts['THROTTLED']} 只基金暂按本地缓存处理")
    if counts.get("LOCAL_ONLY"):
        notes.append("自动网络更新未启用；当前仅读取本地直接基金净值")
    return notes
