"""市场哨兵数据失效 Fail-Closed 行为（GAP-3 / REQ-MKT-05）。

修复前 ``compute_raw_level`` 以 ``max(scores) if scores else 0`` 收尾，
含义是「一个风险指标都取不到 → 等级 0 → GREEN → 允许开仓」。
即数据源全挂时，哨兵报告一切正常 —— 系统在最该报警的时刻最安静。

修复后引入第五级 ``DATA_ERROR = 4``，**刻意排在 RED 之上**：
"不知道市场状态" 至少要和 "已知处于最坏状态" 同等保守。

本文件锁死三件事：
  1. 无指标 → DATA_ERROR，不是 GREEN；
  2. 断数期间**不会**发生静默降级（最危险的次生后果）；
  3. 覆盖率保护不存在旁路开关。
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quant.sentinel.events import SentinelEvent, SentinelEventLog
from quant.sentinel.levels import SentinelLevel, next_level_down
from quant.sentinel.sentinel import (
    MarketSentinel,
    SentinelReading,
    compute_raw_level,
)


def _full_calm(as_of: str = "2026-08-01") -> SentinelReading:
    """四项指标齐全且健康 —— 唯一有资格判 GREEN 的形态。"""
    return SentinelReading(as_of, drawdown=0.01, volatility_percentile=0.30,
                           breadth_above_ma50=0.72, cross_asset_stress=0.20)


def _no_data(as_of: str = "2026-08-01") -> SentinelReading:
    """所有指标缺失（数据源全挂）。"""
    return SentinelReading(as_of)


# ── 1. 无数据不得判绿灯 ──────────────────────────────────────────────

class TestNoDataIsNotGreen:
    def test_all_indicators_missing_yields_data_error(self):
        level, reasons = compute_raw_level(_no_data())
        assert level == SentinelLevel.DATA_ERROR
        assert level != SentinelLevel.GREEN
        assert any("DATA_ERROR" in r for r in reasons), reasons

    def test_data_error_is_more_conservative_than_red(self):
        # 「不知道」必须 ≥「已知最坏」，否则 Fail-Closed 不成立
        assert SentinelLevel.DATA_ERROR > SentinelLevel.RED

    def test_data_error_blocks_new_entries_and_is_risk_off(self):
        assert not SentinelLevel.DATA_ERROR.allows_new_entries
        assert SentinelLevel.DATA_ERROR.is_risk_off
        assert SentinelLevel.DATA_ERROR.is_data_failure

    def test_data_failure_is_distinguishable_from_crisis(self):
        # RED 是「判断出危机」，DATA_ERROR 是「无法判断」，二者语义不能混同
        assert not SentinelLevel.RED.is_data_failure
        assert SentinelLevel.DATA_ERROR.is_data_failure

    def test_full_healthy_coverage_still_green(self):
        # 反向保护：修复不能把正常行情误判成故障
        level, _ = compute_raw_level(_full_calm())
        assert level == SentinelLevel.GREEN


# ── 2. 断数期间不得静默降级（本次修复真正的杀手锏）─────────────────────

class TestOutageDoesNotSilentlyDeescalate:
    def test_outage_escalates_from_red_instead_of_decaying(self):
        s = MarketSentinel(deescalation_confirmations=2)
        s.evaluate(SentinelReading("d1", drawdown=0.20))          # RED
        assert s.level == SentinelLevel.RED
        d = s.evaluate(_no_data("d2"))                            # 数据源挂了
        # 若 DATA_ERROR 低于 RED，这里会被当成「风险缓解」而开始降级确认
        assert d.level == SentinelLevel.DATA_ERROR
        assert d.changed is True

    def test_prolonged_outage_never_walks_level_down(self):
        s = MarketSentinel(deescalation_confirmations=2)
        s.evaluate(SentinelReading("d1", drawdown=0.20))          # RED
        levels = [s.evaluate(_no_data(f"out{i}")).level for i in range(6)]
        # 连续 6 天断数：等级只能停在 DATA_ERROR，绝不允许 RED→ORANGE→YELLOW 衰减
        assert set(levels) == {SentinelLevel.DATA_ERROR}
        assert s.deescalation_streak == 0

    def test_outage_does_not_advance_deescalation_streak(self):
        s = MarketSentinel(deescalation_confirmations=2)
        s.evaluate(SentinelReading("d1", drawdown=0.20))          # RED
        s.evaluate(_no_data("d2"))
        s.evaluate(_no_data("d3"))
        # 断数不是「风险解除的证据」，不得累计降级确认
        assert s.deescalation_streak == 0

    def test_recovery_from_data_error_is_stepwise(self):
        s = MarketSentinel(deescalation_confirmations=2)
        s.evaluate(_no_data("d1"))
        assert s.level == SentinelLevel.DATA_ERROR
        # 数据恢复且行情健康，也必须逐级回落，不能一步回 GREEN
        first = s.evaluate(_full_calm("d2"))
        assert first.level == SentinelLevel.DATA_ERROR            # 未达确认数
        second = s.evaluate(_full_calm("d3"))
        assert second.level == SentinelLevel.RED                  # 每次只降一级
        assert second.level != SentinelLevel.GREEN

    def test_next_level_down_from_data_error_is_red(self):
        assert next_level_down(SentinelLevel.DATA_ERROR) == SentinelLevel.RED


# ── 3. 覆盖率不足 → 判断不可靠，抬到 YELLOW ──────────────────────────

class TestPartialCoverageIsUnreliable:
    def test_single_healthy_indicator_is_not_green(self):
        # 1/4 指标说「平静」不足以宣布平静
        level, reasons = compute_raw_level(SentinelReading("d", drawdown=0.0))
        assert level == SentinelLevel.YELLOW
        assert any("覆盖率" in r for r in reasons), reasons

    def test_coverage_floor_never_downgrades_a_bad_reading(self):
        # 下限只抬不压：单指标已报 RED 时不得被"抬到 YELLOW"反而降级
        level, _ = compute_raw_level(SentinelReading("d", breadth_above_ma50=0.18))
        assert level == SentinelLevel.RED

    def test_half_coverage_meets_threshold(self):
        # 2/4 = 50%，恰好达标，不触发下限抬升
        level, reasons = compute_raw_level(
            SentinelReading("d", drawdown=0.0, volatility_percentile=0.30)
        )
        assert level == SentinelLevel.GREEN
        assert not any("覆盖率" in r for r in reasons), reasons


# ── 4. 事件与数据失效的叠加 ──────────────────────────────────────────

class TestEventsCombinedWithDataFailure:
    def test_confirmed_event_does_not_mask_data_failure(self):
        log = SentinelEventLog()
        log.ingest(SentinelEvent(
            event_type="BLACK_SWAN", event_time="2026-08-01T09:30:00",
            severity="CRITICAL", review_status="CONFIRMED",
        ))
        assert log.max_implied_level() == int(SentinelLevel.RED)
        level, _ = compute_raw_level(_no_data(), log)
        # 取两者较高者：事件最高 RED，数据失效 DATA_ERROR 更保守
        assert level == SentinelLevel.DATA_ERROR

    def test_event_still_drives_level_when_indicators_present(self):
        # 反向保护：指标齐全时，已确认事件仍能强制升级（未被 DATA_ERROR 改动误伤）
        log = SentinelEventLog()
        log.ingest(SentinelEvent(
            event_type="BLACK_SWAN", event_time="2026-08-01T09:30:00",
            severity="CRITICAL", review_status="CONFIRMED",
        ))
        level, _ = compute_raw_level(_full_calm(), log)
        assert level == SentinelLevel.RED


# ── 5. 不允许旁路开关 ────────────────────────────────────────────────

class TestNoBypassSwitch:
    @pytest.mark.parametrize(
        "forbidden",
        ["min_indicator_coverage", "bypass", "skip", "enforce", "force",
         "allow_missing", "unsafe"],
    )
    def test_compute_raw_level_exposes_no_bypass_parameter(self, forbidden):
        # 可选的严格等于不严格。阈值只能改模块常量，让改动进入 diff 与评审，
        # 不能由调用方在运行时软化 —— 参考被本项目否决的 throwOnInvalid=false。
        params = set(inspect.signature(compute_raw_level).parameters)
        assert forbidden not in params, f"compute_raw_level 暴露了旁路参数 {forbidden}"

    def test_evaluate_exposes_no_bypass_parameter(self):
        params = set(inspect.signature(MarketSentinel.evaluate).parameters)
        forbidden = {"bypass", "skip", "enforce", "force", "allow_missing",
                     "min_indicator_coverage"}
        assert not (params & forbidden), params
