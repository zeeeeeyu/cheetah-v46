"""市场哨兵测试（V4.6 审计建议修改顺序第 4 步 / P0）。

9 场景 + 附加覆盖：
  1. 健康指标 → GREEN
  2. 轻度恶化 → YELLOW
  3. 显著压力 → ORANGE
  4. 崩盘指标 → RED
  5. 升级即时（GREEN 一步到 RED）
  6. 降级需连续确认（RED 不能一次回落）
  7. 降级每次只降一级（RED→ORANGE，非 RED→GREEN）
  8. 单日噪声不翻级（确认计数被打断）
  9. 事件去重（同唯一键只计一次）
  + 事件幂等（重复摄取不重复触发）
  + 已确认黑天鹅强制 RED；未确认事件不强制升级
"""

from __future__ import annotations

import pytest

from quant.sentinel import (
    MarketSentinel,
    SentinelEvent,
    SentinelEventLog,
    SentinelLevel,
    SentinelReading,
    compute_raw_level,
)


def _calm(as_of: str = "2026-07-01") -> SentinelReading:
    return SentinelReading(as_of, drawdown=0.01, volatility_percentile=0.30,
                           breadth_above_ma50=0.72, cross_asset_stress=0.20)


# ── 场景 1-4：等级识别 ──────────────────────────────────────────────

class TestLevelRecognition:
    def test_healthy_indicators_are_green(self):
        level, _ = compute_raw_level(_calm())
        assert level == SentinelLevel.GREEN

    def test_mild_deterioration_is_yellow(self):
        level, _ = compute_raw_level(SentinelReading("d", drawdown=0.06, volatility_percentile=0.72))
        assert level == SentinelLevel.YELLOW

    def test_significant_stress_is_orange(self):
        level, _ = compute_raw_level(SentinelReading("d", drawdown=0.11, volatility_percentile=0.86))
        assert level == SentinelLevel.ORANGE

    def test_crash_indicators_are_red(self):
        level, _ = compute_raw_level(SentinelReading("d", drawdown=0.20, volatility_percentile=0.96))
        assert level == SentinelLevel.RED

    def test_worst_indicator_dominates(self):
        # 回撤温和(YELLOW)但波动崩溃(RED) → 取最高 RED（防御优先）
        level, _ = compute_raw_level(SentinelReading("d", drawdown=0.06, volatility_percentile=0.97))
        assert level == SentinelLevel.RED

    def test_low_breadth_raises_level(self):
        level, _ = compute_raw_level(SentinelReading("d", breadth_above_ma50=0.18))
        assert level == SentinelLevel.RED


# ── 场景 5：升级即时 ────────────────────────────────────────────────

class TestImmediateEscalation:
    def test_escalation_is_immediate_green_to_red(self):
        s = MarketSentinel()
        s.evaluate(_calm("2026-07-01"))
        assert s.level == SentinelLevel.GREEN
        d = s.evaluate(SentinelReading("2026-07-02", drawdown=0.20, volatility_percentile=0.97))
        assert d.level == SentinelLevel.RED       # 一步到位
        assert d.changed is True

    def test_escalation_resets_deescalation_streak(self):
        s = MarketSentinel(deescalation_confirmations=2)
        s.evaluate(SentinelReading("d1", drawdown=0.20))          # RED
        s.evaluate(_calm("d2"))                                   # calm → streak 1
        assert s.deescalation_streak == 1
        s.evaluate(SentinelReading("d3", drawdown=0.20))          # 再冲击 → 升级重置
        assert s.deescalation_streak == 0
        assert s.level == SentinelLevel.RED


# ── 场景 6-7：降级需确认 + 每次只降一级 ─────────────────────────────

class TestHystereticDeescalation:
    def test_deescalation_requires_confirmation(self):
        s = MarketSentinel(deescalation_confirmations=2)
        s.evaluate(SentinelReading("d1", drawdown=0.20))          # RED
        d = s.evaluate(_calm("d2"))                               # 第 1 次 calm
        assert d.level == SentinelLevel.RED                       # 仍 RED，未达确认数

    def test_deescalation_steps_down_one_level_only(self):
        s = MarketSentinel(deescalation_confirmations=2)
        s.evaluate(SentinelReading("d1", drawdown=0.20))          # RED
        s.evaluate(_calm("d2"))                                   # calm 1
        d = s.evaluate(_calm("d3"))                               # calm 2 → 降一级
        assert d.level == SentinelLevel.ORANGE                    # RED→ORANGE，不是 GREEN
        assert d.raw_level == SentinelLevel.GREEN                 # 原始已 GREEN，但迟滞限制

    def test_full_deescalation_takes_multiple_cycles(self):
        s = MarketSentinel(deescalation_confirmations=2)
        s.evaluate(SentinelReading("d1", drawdown=0.20))          # RED
        # 需要多轮 calm 才能回到 GREEN
        levels = [s.evaluate(_calm(f"c{i}")).level for i in range(6)]
        # RED -> (c0 streak1) RED -> (c1) ORANGE -> (c2 streak1) ORANGE -> (c3) YELLOW -> (c4 streak1) YELLOW -> (c5) GREEN
        assert levels[-1] == SentinelLevel.GREEN
        assert SentinelLevel.RED not in levels[2:]                # 第 3 次起不再 RED


# ── 场景 8：单日噪声不翻级 ──────────────────────────────────────────

class TestNoiseImmunity:
    def test_single_day_noise_does_not_flip_level(self):
        s = MarketSentinel(deescalation_confirmations=2)
        s.evaluate(SentinelReading("d1", drawdown=0.20))          # RED
        s.evaluate(_calm("d2"))                                   # calm 1（streak 1）
        # 第 3 天又恶化（噪声反复）→ 打断降级确认，维持 RED
        d = s.evaluate(SentinelReading("d3", drawdown=0.20))
        assert d.level == SentinelLevel.RED
        assert s.deescalation_streak == 0

    def test_persistent_same_level_breaks_deescalation(self):
        s = MarketSentinel(deescalation_confirmations=2)
        s.evaluate(SentinelReading("d1", drawdown=0.11))          # ORANGE
        s.evaluate(_calm("d2"))                                   # streak 1
        # 与当前等级相同的压力持续 → 打断确认
        s.evaluate(SentinelReading("d3", drawdown=0.11))          # 仍 ORANGE, 重置 streak
        assert s.deescalation_streak == 0
        assert s.level == SentinelLevel.ORANGE


# ── 场景 9 + 幂等：事件去重与幂等 ───────────────────────────────────

class TestEventDedupAndIdempotency:
    def test_duplicate_event_is_deduplicated_by_unique_key(self):
        log = SentinelEventLog()
        ev = SentinelEvent("POLICY_SHOCK", "2026-07-05T09:00", severity="SEVERE",
                           review_status="CONFIRMED", event_id="POL-1")
        assert log.ingest(ev) is True
        assert log.ingest(ev) is False       # 重复
        assert len(log) == 1

    def test_events_without_id_dedup_by_derived_key(self):
        log = SentinelEventLog()
        a = SentinelEvent("MACRO_SHOCK", "2026-07-05", source="NEWS", title="CPI 超预期")
        b = SentinelEvent("MACRO_SHOCK", "2026-07-05", source="NEWS", title="CPI 超预期")
        assert a.unique_key == b.unique_key
        log.ingest(a)
        assert log.ingest(b) is False
        assert len(log) == 1

    def test_event_ingestion_is_idempotent_on_level(self):
        """重复摄取黑天鹅事件，等级只被推到 RED 一次，不产生二次副作用。"""
        s = MarketSentinel()
        log = SentinelEventLog()
        ev = SentinelEvent("BLACK_SWAN", "2026-07-05", severity="CRITICAL",
                           review_status="CONFIRMED", event_id="BS-1")
        log.ingest(ev)
        log.ingest(ev)     # 幂等
        d1 = s.evaluate(_calm("d1"), event_log=log)
        d2 = s.evaluate(_calm("d2"), event_log=log)
        assert d1.level == SentinelLevel.RED
        assert d2.level == SentinelLevel.RED     # 稳定在 RED（未因重复事件反复跳动）

    def test_confirmed_black_swan_forces_red(self):
        log = SentinelEventLog()
        log.ingest(SentinelEvent("BLACK_SWAN", "d", severity="CRITICAL",
                                 review_status="CONFIRMED", event_id="BS-2"))
        level, _ = compute_raw_level(_calm(), log)
        assert level == SentinelLevel.RED

    def test_unconfirmed_event_does_not_force_escalation(self):
        """未复核事件（谣言/噪声）不得强制升级。"""
        log = SentinelEventLog()
        log.ingest(SentinelEvent("BLACK_SWAN", "d", severity="CRITICAL",
                                 review_status="UNREVIEWED", event_id="BS-3"))
        level, _ = compute_raw_level(_calm(), log)
        assert level == SentinelLevel.GREEN


# ── 附加：决策可序列化（供运行快照） ────────────────────────────────

class TestDecisionSerialization:
    def test_decision_to_dict(self):
        s = MarketSentinel()
        d = s.evaluate(SentinelReading("2026-07-02", drawdown=0.20))
        payload = d.to_dict()
        assert payload["level"] == "RED"
        assert payload["is_risk_off"] is True
        assert payload["allows_new_entries"] is False
        assert isinstance(payload["reasons"], list)
