"""数据集校验器（fail-closed，研究域 DataFrame）。

状态集合：OK / ADJUSTMENT_MISMATCH / CALENDAR_UNVERIFIED / REJECTED。
优先级（高 -> 低）：REJECTED > ADJUSTMENT_MISMATCH > CALENDAR_UNVERIFIED > OK。

原则：
- 任何未来日期（> 今天 2026-08-07）一律判定 REJECTED（fail-closed）。
- 不允许伪造/补全历史；缺失或异常直接记录在 issues。
- 未提供交易日历时，遇到疑似非法交易日仅降级为 CALENDAR_UNVERIFIED。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from .schemas import SCHEMA_VERSION, UNKNOWN

# 研究环境「今天」（用于未来日期判定，fail-closed 锚点）
TODAY = "2026-08-07"

VALID_STATUS = {"OK", "ADJUSTMENT_MISMATCH", "CALENDAR_UNVERIFIED", "REJECTED"}


@dataclass
class ValidationResult:
    status: str
    issues: list[str] = field(default_factory=list)
    checks_passed: list[str] = field(default_factory=list)

    def ok(self) -> bool:
        return self.status == "OK"


def _check(name: str, passed: bool, result: ValidationResult, issue_msg: str) -> None:
    if passed:
        result.checks_passed.append(name)
    else:
        result.issues.append(issue_msg)


def validate_dataset(
    df: pd.DataFrame,
    asset_type: str,
    listing_date: Optional[str] = None,
    schema_version: str = SCHEMA_VERSION,
    trading_calendar: Optional[list[str]] = None,
) -> ValidationResult:
    """对研究域 DataFrame 执行完整性校验，返回 ValidationResult。"""
    result = ValidationResult(status="OK")

    # 0. 规范列存在性
    cols_ok = all(c in df.columns for c in ["date", "open", "high", "low", "close", "volume", "amount"])
    _check("schema_columns", cols_ok, result, "缺少规范列（date/open/high/low/close/volume/amount 之一）")
    if not cols_ok:
        result.status = "REJECTED"
        return result

    n = len(df)
    # 防御性数值化：夹具/外部帧可能以字符串载入
    for _c in ["open", "high", "low", "close", "volume", "amount"]:
        df[_c] = pd.to_numeric(df[_c], errors="coerce")
    dates = df["date"].astype(str).tolist()

    # 1. 日期单调递增
    mono = all(dates[i] <= dates[i + 1] for i in range(max(0, n - 1)))
    _check("date_monotonic_increasing", mono, result, "日期非单调递增")

    # 2. 日期唯一
    dup_dates = n - len(set(dates))
    unique = dup_dates == 0
    _check("date_unique", unique, result, f"存在重复日期（{dup_dates} 条）")

    # 3. OHLC 合法性
    o, h, l, c, v, a = (
        df["open"], df["high"], df["low"], df["close"], df["volume"], df["amount"]
    )
    ohlc_ok = bool(
        (h >= l).all()
        and (h >= pd.concat([o, c], axis=1).max(axis=1)).all()
        and (l <= pd.concat([o, c], axis=1).min(axis=1)).all()
        and (c > 0).all()
        and (df[["open", "high", "low", "close", "volume", "amount"]] >= 0).all().all()
    )
    _check("ohlc_valid", ohlc_ok, result, "OHLC 不合法（high<low / 越界 / close<=0 / 负值）")

    # 4. 成交量非负
    vol_ok = bool((v >= 0).all())
    _check("volume_nonnegative", vol_ok, result, "成交量存在负值")

    # 5. 成交额非负
    amt_ok = bool((a >= 0).all())
    _check("amount_nonnegative", amt_ok, result, "成交额存在负值")

    # 6. 上市日期守卫（不早于 listing_date）
    pre_listing = 0
    if listing_date is not None:
        pre_listing = sum(1 for d in dates if d < str(listing_date))
    listing_ok = pre_listing == 0
    _check("no_rows_before_listing_date", listing_ok, result,
           f"存在早于上市日期 {listing_date} 的 {pre_listing} 行")

    # 7. 未来日期（fail-closed -> REJECTED）
    future = sum(1 for d in dates if d > TODAY)
    future_ok = future == 0
    if not future_ok:
        result.issues.append(f"FUTURE_DATE: 存在 {future} 行日期晚于 {TODAY}")

    # 8. 复权类型显式（冻结数据不允许 UNKNOWN）
    adj = df["adjustment"].astype(str).iloc[0] if "adjustment" in df.columns and n > 0 else UNKNOWN
    adj_ok = adj != UNKNOWN
    _check("adjustment_explicit", adj_ok, result, "复权类型为 UNKNOWN（冻结数据必须显式声明）")

    # 9. source_asset_id 一致性（若提供）
    if "source_asset_id" in df.columns:
        sa = set(df["source_asset_id"].astype(str).unique())
        sid_ok = len(sa) == 1
        _check("source_asset_id_consistent", sid_ok, result, f"source_asset_id 不一致: {sa}")

    # 10. 非法交易日（仅在提供日历时校验）
    cal_ok = True
    if trading_calendar is not None:
        cal_set = set(trading_calendar)
        bad = [d for d in dates if d not in cal_set]
        cal_ok = len(bad) == 0
        _check("trading_dates_in_calendar", cal_ok, result, f"存在 {len(bad)} 个不在交易日历的日期")
    else:
        # 未提供日历：无法验证 -> 仅标记，状态降级为 CALENDAR_UNVERIFIED（若有疑似异常）
        _check("calendar_provided", False, result, "未提供交易日历，无法验证交易日合法性")

    # ── 状态综合 ──
    severe = not (mono and unique and ohlc_ok and vol_ok and amt_ok and listing_ok)
    if future > 0 or severe:
        result.status = "REJECTED"
    elif not adj_ok:
        result.status = "ADJUSTMENT_MISMATCH"
    elif trading_calendar is None and not cal_ok:
        result.status = "CALENDAR_UNVERIFIED"
    else:
        result.status = "OK"

    if result.status not in VALID_STATUS:
        result.status = "REJECTED"
    return result
