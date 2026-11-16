from __future__ import annotations

"""单基金风险观察器。

该模块服务于“错单/补仓前观察”，而不是生成买入指令：
- 仅读取同基金的直接累计净值；
- 不使用 ETF 代理、成交量、OHLCV、ATR 或固定网格价格；
- 输出风险结构和复核结论，不生成申购/赎回计划。
"""

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from .config import load_json, project_path
from .fund_datahub import FundNAVRepository, fund_nav_quality_gate, normalize_fund_code
from .fund_features import compute_fund_nav_features
from .models import DataStatus


@dataclass
class FundWatchResult:
    symbol: str
    name: str
    status: str
    conclusion: str
    reasons: list[str]
    metrics: dict[str, float | str | None]

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "status": self.status,
            "conclusion": self.conclusion,
            "reasons": self.reasons,
            "metrics": self.metrics,
        }


def _rolling_downside_vol(nav: pd.Series, window: int) -> pd.Series:
    returns = nav.astype(float).pct_change(fill_method=None)

    def _calc(values: np.ndarray) -> float:
        downside = np.minimum(values, 0.0)
        return float(np.sqrt(np.mean(np.square(downside))) * np.sqrt(252))

    return returns.rolling(window).apply(_calc, raw=True).dropna()


def _rolling_fragmentation(nav: pd.Series, window: int) -> pd.Series:
    def _calc(values: np.ndarray) -> float:
        displacement = abs(float(values[-1] - values[0]))
        path_length = float(np.abs(np.diff(values)).sum())
        return 0.0 if path_length == 0 else float(1.0 - displacement / path_length)

    return nav.astype(float).rolling(window).apply(_calc, raw=True).dropna()


def _dynamic_quiet_zone(nav: pd.Series, window: int, quantile: float, vol_floor: float, vol_ceiling: float,
                        fi_floor: float, fi_ceiling: float) -> tuple[float, float]:
    """Return diagnostic 'quiet-zone' references, never a trade trigger."""
    rolling_vol = _rolling_downside_vol(nav, window)
    rolling_fi = _rolling_fragmentation(nav, window)
    if rolling_vol.empty or rolling_fi.empty:
        raise ValueError("历史长度不足以估计动态风险参考线")
    vol_reference = float(np.clip(rolling_vol.quantile(quantile), vol_floor, vol_ceiling))
    fi_reference = float(np.clip(rolling_fi.quantile(quantile), fi_floor, fi_ceiling))
    return vol_reference, fi_reference


def _watch_config() -> dict[str, Any]:
    return load_json("config/fund/watchlist.json")


def run_fund_watchlist(as_of_date: str | None = None) -> list[FundWatchResult]:
    """Evaluate configured single-fund observers without creating an order or trade plan."""
    config = _watch_config()
    fund_settings = load_json("config/fund/settings.json")
    as_of = pd.Timestamp(as_of_date).normalize() if as_of_date else pd.Timestamp.today().normalize()
    monitor = config.get("monitor", {})
    risk_window = int(monitor.get("risk_window", 60))
    history_days = int(monitor.get("minimum_history_days", 180))
    quantile = float(monitor.get("quiet_zone_quantile", 0.20))
    bounds = monitor.get("threshold_bounds", {})
    repo = FundNAVRepository()
    results: list[FundWatchResult] = []

    for raw_item in config.get("watchlist", []):
        symbol = normalize_fund_code(raw_item.get("symbol", ""))
        name = str(raw_item.get("name") or symbol)
        if not symbol:
            continue
        try:
            frame = repo.load(symbol)
            frame = frame[frame["date"] <= as_of].copy()
            check = fund_nav_quality_gate(
                frame=frame,
                symbol=symbol,
                as_of_date=as_of,
                min_history_days=max(120, history_days),
                max_staleness_calendar_days=int(fund_settings["data"]["max_staleness_calendar_days"]),
                max_single_day_return_abs=float(fund_settings["data"]["max_single_day_return_abs"]),
                required_nav_basis="cumulative_nav",
                require_direct_symbol_match=True,
            )
            if check.status != DataStatus.PASS_:
                results.append(FundWatchResult(
                    symbol=symbol,
                    name=name,
                    status="DATA_BLOCKED",
                    conclusion="今天不对该基金作补仓或止损判断。先修复直接累计净值数据。",
                    reasons=check.reasons,
                    metrics={"latest_date": str(check.metrics.get("latest_date", "N/A"))},
                ))
                continue

            nav = pd.to_numeric(frame["nav"], errors="coerce").astype(float)
            feature = compute_fund_nav_features(frame, fund_settings["signals"])
            vol_ref, fi_ref = _dynamic_quiet_zone(
                nav=nav,
                window=risk_window,
                quantile=quantile,
                vol_floor=float(bounds.get("downside_vol_floor", 0.10)),
                vol_ceiling=float(bounds.get("downside_vol_ceiling", 0.35)),
                fi_floor=float(bounds.get("fragmentation_floor", 0.45)),
                fi_ceiling=float(bounds.get("fragmentation_ceiling", 0.75)),
            )
            high_risk = bool(feature["downside_vol"] > vol_ref or feature["fragmentation"] > fi_ref)
            nav_above_ma120 = bool(nav.iloc[-1] > feature["ma120"])

            if high_risk:
                conclusion = "风险结构仍偏高：不建议基于该监控模块补仓；仅保留观察。"
                reasons = [
                    "60日下行波动率或路径破碎度高于自身历史相对平稳区参考线",
                    "该结论只描述风险结构，不预测后续净值。",
                ]
                status = "HIGH_RISK"
            elif not nav_above_ma120:
                conclusion = "波动已较平稳，但累计净值仍未站上 MA120：不因“平稳”本身补仓。"
                reasons = [
                    "风险结构不高不等于趋势已修复",
                    "等待直接净值趋势与个人资金计划共同复核。",
                ]
                status = "WATCH"
            else:
                conclusion = "风险结构相对平稳、趋势未明显破坏：仅进入人工复核，不生成买入指令。"
                reasons = [
                    "本观察器不包含估值、基本面或基金经理变动信息",
                    "即便通过，也不能据此自动补仓或扩大仓位。",
                ]
                status = "WATCH"

            cost_nav = raw_item.get("cost_nav")
            cost_nav_value: float | None
            try:
                cost_nav_value = float(cost_nav) if cost_nav not in (None, "") else None
            except (TypeError, ValueError):
                cost_nav_value = None
            return_from_cost = None
            if cost_nav_value and cost_nav_value > 0:
                return_from_cost = float(nav.iloc[-1] / cost_nav_value - 1.0)

            results.append(FundWatchResult(
                symbol=symbol,
                name=name,
                status=status,
                conclusion=conclusion,
                reasons=reasons,
                metrics={
                    "latest_nav": float(nav.iloc[-1]),
                    "ma120": float(feature["ma120"]),
                    "r20": float(feature["r20"]),
                    "r60": float(feature["r60"]),
                    "downside_vol_60": float(feature["downside_vol"]),
                    "fragmentation_60": float(feature["fragmentation"]),
                    "downside_vol_quiet_reference": vol_ref,
                    "fragmentation_quiet_reference": fi_ref,
                    "return_from_cost_nav": return_from_cost,
                    "as_of_date": str(as_of.date()),
                },
            ))
        except Exception as exc:  # User-facing console must fail closed, never infer a proxy.
            results.append(FundWatchResult(
                symbol=symbol,
                name=name,
                status="DATA_BLOCKED",
                conclusion="今天不对该基金作任何申购、赎回或补仓判断。",
                reasons=[f"无法读取同基金直接累计净值：{exc}"],
                metrics={"as_of_date": str(as_of.date())},
            ))
    return results


def write_fund_watch_report(results: list[FundWatchResult], as_of_date: str | None = None) -> str:
    as_of = pd.Timestamp(as_of_date).normalize() if as_of_date else pd.Timestamp.today().normalize()
    output = project_path("output", "fund_decisions")
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"fund_watch_{as_of.date()}.md"
    lines = [f"# 场外基金单基金观察报告｜{as_of.date()}", "", "- 本报告只做风险结构观察，不生成买入、补仓、赎回或止损订单。"]
    for item in results:
        lines.extend([
            "",
            f"## {item.symbol} {item.name}",
            f"- 状态：**{item.status}**",
            f"- 结论：{item.conclusion}",
            *[f"- {reason}" for reason in item.reasons],
        ])
        if item.metrics:
            lines.append("- 指标：")
            for key, value in item.metrics.items():
                if isinstance(value, float):
                    if "return" in key or "vol" in key:
                        lines.append(f"  - {key}: {value:.2%}")
                    else:
                        lines.append(f"  - {key}: {value:.4f}")
                else:
                    lines.append(f"  - {key}: {value}")
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path.relative_to(project_path()))
