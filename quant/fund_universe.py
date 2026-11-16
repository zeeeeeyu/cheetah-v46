from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from .calendar_groups import CALENDAR_GROUPS, resolve_calendar_group
from .fund_features import compute_fund_nav_features
from .models import CheckResult, DataStatus, Signal


_ALLOWED_SHARE_CLASSES = {"A", "B", "C", "LOF", "MIXED", "UNKNOWN"}
_ALLOWED_FEE_MODELS = {"MANUAL_VERIFY", "C_LONG_HOLD_ZERO", "EXPLICIT_RESEARCH", "EXCLUDE_UNTIL_SET"}


@dataclass
class FundInstrument:
    """A direct-NAV tradable fund identity.

    ``symbol == signal_symbol == trade_symbol`` is a hard invariant in the open-fund branch.
    Share class and fee model are metadata used for execution/governance only; they never cause
    one fund's NAV to be replaced with an ETF or another share class.
    """

    symbol: str
    name: str
    asset_class: str
    group: str
    signal_symbol: str
    trade_symbol: str
    nav_basis_required: str
    active: bool
    calendar_group: str = ""
    quality_failure_policy: str = "QUARANTINE"
    max_staleness_calendar_days: int | None = None
    quality_window_trading_days: int | None = None
    share_class: str = "UNKNOWN"
    fund_type: str = "OPEN_FUND"
    is_c_class: bool = False
    fee_model: str = "MANUAL_VERIFY"
    historical_active_from: str | None = None
    historical_active_to: str | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "FundInstrument":
        raw_share = str(payload.get("share_class", "UNKNOWN") or "UNKNOWN").upper()
        # Existing projects sometimes persisted an A/LOF product as a name ending in C.  Metadata,
        # not the display name, controls the share-class treatment.
        share_class = raw_share if raw_share in _ALLOWED_SHARE_CLASSES else "UNKNOWN"
        return cls(
            symbol=str(payload["symbol"]),
            name=str(payload["name"]),
            asset_class=str(payload["asset_class"]),
            group=str(payload["group"]),
            signal_symbol=str(payload["signal_symbol"]),
            trade_symbol=str(payload["trade_symbol"]),
            nav_basis_required=str(payload.get("nav_basis_required", "cumulative_nav")),
            active=bool(payload.get("active", True)),
            # Fail-closed: an instrument that cannot be resolved to CN/HK/QDII is rejected here
            # rather than inheriting the A-share calendar by default.
            calendar_group=resolve_calendar_group(payload),
            quality_failure_policy=str(payload.get("quality_failure_policy", "QUARANTINE")).upper(),
            max_staleness_calendar_days=(
                int(payload["max_staleness_calendar_days"])
                if payload.get("max_staleness_calendar_days") not in (None, "")
                else None
            ),
            quality_window_trading_days=(
                int(payload["quality_window_trading_days"])
                if payload.get("quality_window_trading_days") not in (None, "")
                else None
            ),
            share_class=share_class,
            fund_type=str(payload.get("fund_type", "OPEN_FUND") or "OPEN_FUND").upper(),
            is_c_class=bool(payload.get("is_c_class", share_class == "C")),
            fee_model=str(payload.get("fee_model", "MANUAL_VERIFY") or "MANUAL_VERIFY").upper(),
            historical_active_from=(str(payload["historical_active_from"]) if payload.get("historical_active_from") else None),
            historical_active_to=(str(payload["historical_active_to"]) if payload.get("historical_active_to") else None),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "asset_class": self.asset_class,
            "group": self.group,
            "signal_symbol": self.signal_symbol,
            "trade_symbol": self.trade_symbol,
            "nav_basis_required": self.nav_basis_required,
            "active": self.active,
            "calendar_group": self.calendar_group,
            "quality_failure_policy": self.quality_failure_policy,
            "max_staleness_calendar_days": self.max_staleness_calendar_days,
            "quality_window_trading_days": self.quality_window_trading_days,
            "share_class": self.share_class,
            "fund_type": self.fund_type,
            "is_c_class": self.is_c_class,
            "fee_model": self.fee_model,
            "historical_active_from": self.historical_active_from,
            "historical_active_to": self.historical_active_to,
        }


def validate_fund_catalog(instruments: list[FundInstrument]) -> None:
    symbols: set[str] = set()
    for item in instruments:
        if item.symbol in symbols:
            raise ValueError(f"重复场外基金代码：{item.symbol}")
        symbols.add(item.symbol)
        if item.symbol != item.signal_symbol or item.symbol != item.trade_symbol:
            raise ValueError(
                f"场外基金分支禁止代理资产：{item.symbol} 的 signal_symbol 与 trade_symbol 必须等于自身；"
                f"当前为 {item.signal_symbol}/{item.trade_symbol}"
            )
        if item.calendar_group not in CALENDAR_GROUPS:
            raise ValueError(
                f"{item.symbol} 未声明合法的 calendar_group（当前 {item.calendar_group!r}）；"
                f"只能是 {'/'.join(CALENDAR_GROUPS)}，不得默认归入 CN"
            )
        if item.nav_basis_required not in {"cumulative_nav", "unit_nav"}:
            raise ValueError(f"{item.symbol} 的 nav_basis_required 非法：{item.nav_basis_required}")
        if item.quality_failure_policy not in {"BLOCK_GLOBAL", "QUARANTINE"}:
            raise ValueError(
                f"{item.symbol} 的 quality_failure_policy 非法：{item.quality_failure_policy}；"
                "只能是 BLOCK_GLOBAL 或 QUARANTINE"
            )
        if item.max_staleness_calendar_days is not None and item.max_staleness_calendar_days < 0:
            raise ValueError(f"{item.symbol} 的 max_staleness_calendar_days 不能为负")
        if item.quality_window_trading_days is not None and item.quality_window_trading_days < 1:
            raise ValueError(f"{item.symbol} 的 quality_window_trading_days 必须大于 0")
        if item.share_class not in _ALLOWED_SHARE_CLASSES:
            raise ValueError(f"{item.symbol} 的 share_class 非法：{item.share_class}")
        if item.fee_model not in _ALLOWED_FEE_MODELS:
            raise ValueError(f"{item.symbol} 的 fee_model 非法：{item.fee_model}")
        if item.is_c_class != (item.share_class == "C"):
            raise ValueError(f"{item.symbol} 的 is_c_class 与 share_class 不一致；请使用明确份额类别")


def instrument_active_on_date(instrument: FundInstrument, as_of: pd.Timestamp) -> bool:
    """Return historical membership status without fabricating pre-inception availability.

    This is used by long-horizon research.  It does not turn absent NAV data into a usable history;
    it merely provides an explicit membership boundary when the user later imports a historical
    candidate pool.
    """
    stamp = pd.Timestamp(as_of).normalize()
    if instrument.historical_active_from and stamp < pd.Timestamp(instrument.historical_active_from).normalize():
        return False
    if instrument.historical_active_to and stamp > pd.Timestamp(instrument.historical_active_to).normalize():
        return False
    return bool(instrument.active)


def _purchase_open(snapshot: dict[str, Any] | None, allow_limited: bool) -> bool:
    status = str((snapshot or {}).get("purchase_status", "UNKNOWN")).upper()
    return status == "OPEN" or (allow_limited and status == "LIMITED")


def build_fund_signals(
    instruments: list[FundInstrument],
    frames: dict[str, pd.DataFrame],
    checks: dict[str, CheckResult],
    settings: dict,
    tradeability_snapshot: dict[str, Any] | None = None,
) -> list[Signal]:
    signals: list[Signal] = []
    cfg = settings["signals"]
    # Tradeability and channel-specific fees belong to the execution gate, not the signal gate.
    # A missing same-day snapshot must stop an executable subscription plan, but it must not erase
    # the underlying direct-NAV trend diagnostics or force the portfolio to look like CASH.

    for inst in instruments:
        if not inst.active:
            continue
        check = checks[inst.symbol]
        if check.status == DataStatus.BLOCK:
            signals.append(Signal(inst.symbol, inst.name, inst.asset_class, inst.group, False, ["数据质量未通过"] + check.reasons))
            continue
        reasons: list[str] = []
        feature = compute_fund_nav_features(frames[inst.symbol], cfg)
        if feature["r60"] <= float(cfg["minimum_absolute_momentum"]):
            reasons.append("60日绝对动量未达标")
        if feature["trend_quality"] < float(cfg["minimum_trend_quality"]):
            reasons.append("趋势质量不足")
        if frames[inst.symbol]["nav"].iloc[-1] <= feature["ma120"]:
            reasons.append("累计净值未站上 MA120")
        signals.append(
            Signal(
                symbol=inst.symbol,
                name=inst.name,
                asset_class=inst.asset_class,
                group=inst.group,
                eligible=not reasons,
                reasons=reasons,
                r20=feature["r20"],
                r60=feature["r60"],
                r120=feature["r120"],
                downside_vol=feature["downside_vol"],
                fragmentation=feature["fragmentation"],
                trend_quality=feature["trend_quality"],
                score=feature["score"],
                median_turnover_20d=None,
            )
        )
    return signals
