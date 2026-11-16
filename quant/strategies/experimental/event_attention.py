"""V4.6 EVENT_ATTENTION_ETF 实验策略。

利用事件、注意力、市场情绪和热点扩散，在 PAPER 环境下生成 ETF 研究信号。
不生成 LIVE 订单。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd

from quant.config import project_path, load_json
from quant.domain.enums import Environment, CapitalPermission
from quant.domain.signals import StrategySignal
from quant.feat.event import EventRepository, Event
from quant.feat.sentiment import analyze_hotspot_diffusion, compute_sentiment, HotspotAnalysis, SentimentSnapshot


@dataclass
class EventAttentionResult:
    """实验策略输出。"""
    as_of_date: str
    signals: list[StrategySignal] = field(default_factory=list)
    sentiment: SentimentSnapshot | None = None
    hotspot: HotspotAnalysis | None = None
    active_events: list[Event] = field(default_factory=list)
    selected_etfs: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of_date": self.as_of_date,
            "signals": [s.to_dict() for s in self.signals],
            "sentiment": self.sentiment.to_dict() if self.sentiment else None,
            "hotspot": self.hotspot.to_dict() if self.hotspot else None,
            "active_events": [e.to_dict() for e in self.active_events[:5]],
            "selected_etfs": list(self.selected_etfs),
            "notes": list(self.notes),
        }


def run_event_attention_etf(
    as_of_date: str,
    frames: dict[str, pd.DataFrame] | None = None,
    event_repo: EventRepository | None = None,
) -> EventAttentionResult:
    """运行事件注意力实验策略。

    默认权限：PAPER，不生成 LIVE 订单。
    事件数据缺失不阻断核心策略或基础战术趋势。
    """
    result = EventAttentionResult(as_of_date=as_of_date)
    notes: list[str] = []

    # 1. 事件
    repo = event_repo or EventRepository()
    actionable = repo.find_actionable(as_of_date)
    result.active_events = actionable
    if not actionable:
        notes.append("无可交易事件，跳过事件信号")

    # 2. 主题映射
    mapping_path = project_path("config", "events", "event_etf_mapping.json")
    if mapping_path.exists():
        mapping = load_json("config/events/event_etf_mapping.json")
        theme_to_etf: dict[str, list[str]] = {}
        for m in mapping.get("mappings", []):
            theme_to_etf[m["theme"]] = m["etf_symbols"]
    else:
        theme_to_etf = {}
        notes.append("event_etf_mapping.json 不存在，跳过主题映射")

    # 3. 热点分析
    if frames:
        hotspot = analyze_hotspot_diffusion(frames, theme_to_etf, as_of_date)
        result.hotspot = hotspot
        sentiment = compute_sentiment(frames, as_of_date, hotspot=hotspot)
        result.sentiment = sentiment

        # 4. 信号生成
        for event in actionable[:3]:  # 最多 3 个事件
            for theme in event.related_themes:
                etfs = theme_to_etf.get(theme, [])
                for etf in etfs:
                    if etf not in result.selected_etfs:
                        sig = StrategySignal(
                            strategy_id="EVENT_ATTENTION_ETF",
                            strategy_version="0.1.0-exp",
                            symbol=etf,
                            signal_date=as_of_date,
                            signal_type="EVENT_ATTENTION",
                            score=event.confidence * 100,
                            confidence=event.confidence,
                            expected_horizon=event.expected_horizon,
                            risk_direction=event.direction,
                            reasons=(f"事件: {event.title}", f"主题: {theme}", f"来源: {event.source}"),
                        )
                        result.signals.append(sig)
                        result.selected_etfs.append(etf)
    else:
        notes.append("无行情数据，仅输出事件信号")

    result.notes = notes
    return result


def generate_paper_report(result: EventAttentionResult) -> str:
    """生成 PAPER 环境报告。"""
    lines = [
        "# EVENT_ATTENTION_ETF 实验策略报告",
        f"> 生成日期: {result.as_of_date}",
        f"> 权限: PAPER（无真实资金）",
        "",
        "## 活跃事件",
    ]
    for e in result.active_events:
        lines.append(f"- [{e.event_type}] {e.title} | 方向: {e.direction} | 置信度: {e.confidence:.1%} | 状态: {e.review_status}")

    lines.extend(["", "## 情绪快照"])
    if result.sentiment:
        s = result.sentiment
        lines.append(f"- 上涨比例: {s.advance_ratio:.1%}" if s.advance_ratio else "- 上涨比例: N/A")
        lines.append(f"- 创新高比例: {s.new_high_ratio:.1%}" if s.new_high_ratio else "- 创新高比例: N/A")
        lines.append(f"- 高波动比例: {s.high_vol_ratio:.1%}" if s.high_vol_ratio else "- 高波动比例: N/A")
        lines.append(f"- 热点扩散: {s.diffusion_level}")

    lines.extend(["", "## 生成信号"])
    for sig in result.signals:
        lines.append(f"- {sig.symbol}: 评分 {sig.score:.1f}, 置信度 {sig.confidence:.0%}, {', '.join(sig.reasons)}")

    if not result.signals:
        lines.append("- 本次无事件信号")

    lines.extend(["", "## 注意事项", "- 本策略为实验性质，PAPER 权限", "- 事件数据缺失不阻断核心策略", "- 不生成 LIVE 订单"])

    report = "\n".join(lines)

    # Save
    out_dir = project_path("output", "paper_reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"event_attention_{result.as_of_date}.md"
    out_path.write_text(report, encoding="utf-8")

    return str(out_path)
