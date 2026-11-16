"""GAP-5 日历分组对齐 —— 专项测试。

验证 fund_pipeline.align_fund_frames_by_calendar_group() 不再用
全局 min(latest_dates) 截断所有序列，而是按 CN/HK/QDII 分组独立对齐。

核心断言：混合组合中 A 股基金的信号不被 QDII 的滞后日期拖垮。
"""

from __future__ import annotations

import pandas as pd
import pytest

from quant.calendar_groups import CALENDAR_GROUPS, CalendarGroupError, normalize_calendar_group, resolve_calendar_group
from quant.fund_pipeline import (
    CalendarGroupAlignment,
    align_fund_frames_by_calendar_group,
)
from quant.models import CheckResult, DataStatus


# ── 辅助 ──────────────────────────────────────────────────────


def _frame(symbol: str, dates: list[str]) -> pd.DataFrame:
    """构造一个有 date 列的 DataFrame，日期从 dates 列表取。"""
    return pd.DataFrame({"date": dates, "nav": [1.0 + i * 0.01 for i in range(len(dates))]})


def _pass_check() -> CheckResult:
    return CheckResult(status=DataStatus.PASS_)


def _block_check() -> CheckResult:
    return CheckResult(status=DataStatus.BLOCK)


# ── calendar_groups 原语 ─────────────────────────────────────


class TestCalendarGroups:
    """日历分组基础原语。"""

    def test_three_groups(self):
        assert CALENDAR_GROUPS == ("CN", "HK", "QDII")

    @pytest.mark.parametrize("raw,expected", [("CN", "CN"), ("cn", "CN"), ("QDII", "QDII"), ("hk", "HK")])
    def test_normalize_valid(self, raw, expected):
        assert normalize_calendar_group(raw) == expected

    def test_normalize_invalid_rejected(self):
        with pytest.raises(CalendarGroupError):
            normalize_calendar_group("US")

    def test_resolve_explicit_declaration(self):
        payload = {"symbol": "F001", "calendar_group": "QDII"}
        assert resolve_calendar_group(payload) == "QDII"

    def test_resolve_from_qdii_fund_type(self):
        payload = {"symbol": "F001", "fund_type": "QDII"}
        assert resolve_calendar_group(payload) == "QDII"

    def test_resolve_from_cross_border(self):
        payload = {"symbol": "F001", "asset_type": "CROSS_BORDER"}
        assert resolve_calendar_group(payload) == "QDII"

    def test_resolve_from_cn_exchange(self):
        payload = {"symbol": "510300", "exchange": "SSE"}
        assert resolve_calendar_group(payload) == "CN"

    def test_resolve_from_hk_exchange(self):
        payload = {"symbol": "00700", "exchange": "HKEX"}
        assert resolve_calendar_group(payload) == "HK"

    def test_resolve_unidentifiable_rejected(self):
        """Fail-Closed：无法判定的资产不默认归入 CN。"""
        with pytest.raises(CalendarGroupError):
            resolve_calendar_group({"symbol": "UNKNOWN"})


# ── 分组对齐核心 ──────────────────────────────────────────────


class TestPerGroupAlignment:
    """GAP-5 核心断言：CN 不被 QDII 拖垮。"""

    def test_cn_not_dragged_by_qdii(self):
        """混合组合：CN 最新 T日 + QDII 最新 T-2日 → CN 保留到 T，不被截断到 T-2。"""
        cn_dates = ["2026-07-28", "2026-07-29", "2026-07-30"]
        qdii_dates = ["2026-07-28", "2026-07-29"]  # 少一天（T-2）
        frames = {"F_CN": _frame("F_CN", cn_dates), "F_QD": _frame("F_QD", qdii_dates)}
        checks = {"F_CN": _pass_check(), "F_QD": _pass_check()}
        groups = {"F_CN": "CN", "F_QD": "QDII"}

        aligned, alignment = align_fund_frames_by_calendar_group(
            frames, checks, groups, max_cross_group_lag_calendar_days=5,
        )

        # CN 的最新日期仍然是 07-30，没有被截断
        cn_latest = pd.to_datetime(aligned["F_CN"]["date"]).max().strftime("%Y-%m-%d")
        assert cn_latest == "2026-07-30", f"CN 被截断到 {cn_latest}，应保留 07-30"

        # QDII 对齐到自己的组内最新
        qdii_latest = pd.to_datetime(aligned["F_QD"]["date"]).max().strftime("%Y-%m-%d")
        assert qdii_latest == "2026-07-29"

        # 跨组滞后可见
        assert alignment is not None
        assert "CN" in alignment.signal_dates
        assert "QDII" in alignment.signal_dates
        assert alignment.signal_age_days["QDII"] == 1  # 1 calendar day behind

    def test_single_group_no_regression(self):
        """单一分组 → 行为与原来一致（组内取 min）。"""
        dates_a = ["2026-07-28", "2026-07-29", "2026-07-30"]
        dates_b = ["2026-07-28", "2026-07-29"]
        frames = {"A": _frame("A", dates_a), "B": _frame("B", dates_b)}
        checks = {"A": _pass_check(), "B": _pass_check()}
        groups = {"A": "CN", "B": "CN"}

        aligned, alignment = align_fund_frames_by_calendar_group(
            frames, checks, groups, max_cross_group_lag_calendar_days=5,
        )

        # 同组内取 min → 都对齐到 07-29
        assert pd.to_datetime(aligned["A"]["date"]).max().strftime("%Y-%m-%d") == "2026-07-29"
        assert pd.to_datetime(aligned["B"]["date"]).max().strftime("%Y-%m-%d") == "2026-07-29"
        assert alignment is not None
        assert alignment.max_cross_group_lag_days == 0  # 只有一组，无跨组滞后

    def test_blocked_excluded_from_alignment(self):
        """被 BLOCK 隔离的品种不参与任何分组的日期交集。"""
        cn_good = ["2026-07-28", "2026-07-29", "2026-07-30"]
        cn_bad = ["2026-07-28"]  # 只有一天数据
        frames = {"GOOD": _frame("GOOD", cn_good), "BAD": _frame("BAD", cn_bad)}
        checks = {"GOOD": _pass_check(), "BAD": _block_check()}
        groups = {"GOOD": "CN", "BAD": "CN"}

        aligned, alignment = align_fund_frames_by_calendar_group(
            frames, checks, groups, max_cross_group_lag_calendar_days=5,
        )

        # GOOD 不被 BAD 的短数据拖到 07-28
        assert pd.to_datetime(aligned["GOOD"]["date"]).max().strftime("%Y-%m-%d") == "2026-07-30"
        # BAD 保留原帧不变
        assert pd.to_datetime(aligned["BAD"]["date"]).max().strftime("%Y-%m-%d") == "2026-07-28"

    def test_usable_empty_returns_original(self):
        """usable 为空 → 返回原帧 + None alignment。"""
        frames = {"X": _frame("X", ["2026-07-28"])}
        checks = {"X": _block_check()}
        groups = {"X": "CN"}

        aligned, alignment = align_fund_frames_by_calendar_group(
            frames, checks, groups, max_cross_group_lag_calendar_days=5,
        )
        assert alignment is None
        assert "X" in aligned

    def test_missing_group_rejected(self):
        """缺少日历分组的品种 → Fail-Closed 拒绝对齐。"""
        frames = {"X": _frame("X", ["2026-07-28"])}
        checks = {"X": _pass_check()}
        groups = {}  # X 没有分组信息

        with pytest.raises(CalendarGroupError):
            align_fund_frames_by_calendar_group(
                frames, checks, groups, max_cross_group_lag_calendar_days=5,
            )

    def test_cross_group_lag_exceeds_threshold(self):
        """跨组滞后超阈值 → stale_groups 非空。"""
        cn_dates = ["2026-07-28", "2026-07-29", "2026-07-30"]
        qdii_dates = ["2026-07-25"]  # 滞后 5 天
        frames = {"F_CN": _frame("F_CN", cn_dates), "F_QD": _frame("F_QD", qdii_dates)}
        checks = {"F_CN": _pass_check(), "F_QD": _pass_check()}
        groups = {"F_CN": "CN", "F_QD": "QDII"}

        aligned, alignment = align_fund_frames_by_calendar_group(
            frames, checks, groups, max_cross_group_lag_calendar_days=3,
        )

        assert alignment is not None
        assert "QDII" in alignment.stale_groups
        assert alignment.signal_age_days["QDII"] > 3
        # CN 仍保留自己的日期
        assert pd.to_datetime(aligned["F_CN"]["date"]).max().strftime("%Y-%m-%d") == "2026-07-30"

    def test_alignment_describe_has_info(self):
        """describe() 输出包含各组信号日和滞后信息。"""
        cn_dates = ["2026-07-28", "2026-07-30"]
        qdii_dates = ["2026-07-28", "2026-07-29"]
        frames = {"F_CN": _frame("F_CN", cn_dates), "F_QD": _frame("F_QD", qdii_dates)}
        checks = {"F_CN": _pass_check(), "F_QD": _pass_check()}
        groups = {"F_CN": "CN", "F_QD": "QDII"}

        _, alignment = align_fund_frames_by_calendar_group(
            frames, checks, groups, max_cross_group_lag_calendar_days=5,
        )
        assert alignment is not None
        desc = alignment.describe()
        assert any("CN" in line for line in desc)
        assert any("QDII" in line for line in desc)

    def test_to_dict_includes_convention(self):
        """to_dict() 包含约定名和各组信号日。"""
        frames = {"A": _frame("A", ["2026-07-28", "2026-07-30"])}
        checks = {"A": _pass_check()}
        groups = {"A": "CN"}

        _, alignment = align_fund_frames_by_calendar_group(
            frames, checks, groups, max_cross_group_lag_calendar_days=5,
        )
        d = alignment.to_dict()
        assert d["convention"] == "LATEST_PER_GROUP"
        assert "CN" in d["signal_dates"]
