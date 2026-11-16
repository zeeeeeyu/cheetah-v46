from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .features import compute_signal_features
from .models import CheckResult, DataStatus, Signal


@dataclass
class Instrument:
    symbol: str
    name: str
    asset_class: str
    group: str
    signal_symbol: str
    trade_symbol: str
    active: bool

    @classmethod
    def from_dict(cls, payload: dict) -> "Instrument":
        return cls(
            symbol=str(payload["symbol"]),
            name=str(payload["name"]),
            asset_class=str(payload["asset_class"]),
            group=str(payload["group"]),
            signal_symbol=str(payload["signal_symbol"]),
            trade_symbol=str(payload["trade_symbol"]),
            active=bool(payload.get("active", True)),
        )


def validate_catalog(instruments: list[Instrument]) -> None:
    symbols = set()
    for inst in instruments:
        if inst.symbol in symbols:
            raise ValueError(f"重复交易标的：{inst.symbol}")
        symbols.add(inst.symbol)
        if inst.symbol != inst.signal_symbol or inst.symbol != inst.trade_symbol:
            raise ValueError(
                f"禁止代理资产：{inst.symbol} 的 signal_symbol/trade_symbol 必须等于自身；"
                f"当前为 {inst.signal_symbol}/{inst.trade_symbol}"
            )


def build_signals(
    instruments: list[Instrument],
    frames: dict[str, pd.DataFrame],
    checks: dict[str, CheckResult],
    settings: dict,
) -> list[Signal]:
    signals: list[Signal] = []
    min_turnover = float(settings["data"]["min_median_turnover_20d"])
    signal_cfg = settings["signals"]

    for inst in instruments:
        if not inst.active:
            continue
        reasons: list[str] = []
        check = checks[inst.symbol]
        if check.status != DataStatus.PASS_:
            signals.append(Signal(inst.symbol, inst.name, inst.asset_class, inst.group, False, ["数据质量未通过"] + check.reasons))
            continue
        feature = compute_signal_features(frames[inst.symbol], signal_cfg)
        if feature["median_turnover_20d"] < min_turnover:
            reasons.append(f"流动性不足：20日成交额中位数 {feature['median_turnover_20d']:.0f} < {min_turnover:.0f}")
        if feature["r60"] <= float(signal_cfg["minimum_absolute_momentum"]):
            reasons.append("60日绝对动量未达标")
        if feature["trend_quality"] < float(signal_cfg["minimum_trend_quality"]):
            reasons.append("趋势质量不足")
        if frames[inst.symbol]["close"].iloc[-1] <= feature["ma120"]:
            reasons.append("收盘价未站上 MA120")

        eligible = not reasons
        signals.append(
            Signal(
                symbol=inst.symbol,
                name=inst.name,
                asset_class=inst.asset_class,
                group=inst.group,
                eligible=eligible,
                reasons=reasons,
                r20=feature["r20"],
                r60=feature["r60"],
                r120=feature["r120"],
                downside_vol=feature["downside_vol"],
                fragmentation=feature["fragmentation"],
                trend_quality=feature["trend_quality"],
                score=feature["score"],
                median_turnover_20d=feature["median_turnover_20d"],
            )
        )
    return signals
