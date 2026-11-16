"""D-01 落地：PROVISIONAL 参数标记与校验（采纳方案 A）。

验证：settings.json 已登记 provisional_parameters 且覆盖完整；从 settings 解析出的
当前生效值与 PROVISIONAL_PARAMETERS 登记的 current 一致；is_provisional 判定正确。
"""

from __future__ import annotations

import pytest

from quant.config import load_json
from quant.provisional import (
    PROVISIONAL_KEYS,
    get_provisional_parameters,
    is_provisional,
    verify_provisional_markers,
    verify_provisional_values,
)


@pytest.fixture(scope="module")
def settings():
    return load_json("config/settings.json")


def test_provisional_keys_registered_in_settings(settings):
    listed = set(settings.get("provisional_parameters", []))
    assert listed == set(PROVISIONAL_KEYS), listed ^ set(PROVISIONAL_KEYS)


def test_no_missing_markers(settings):
    assert verify_provisional_markers(settings) == []


def test_values_match_registry(settings):
    assert verify_provisional_values(settings) == []


def test_resolved_current_values(settings):
    got = get_provisional_parameters(settings)
    assert got == {
        "minimum_history_days": 126,
        "data.min_median_turnover_20d": 100_000_000,
        "portfolio.max_single_weight": 0.45,
        "execution.max_non_emergency_turnover": 0.35,
    }


@pytest.mark.parametrize("key,expected", [
    ("minimum_history_days", True),
    ("portfolio.max_single_weight", True),
    ("mode", False),
    ("governance.champion_name", False),
])
def test_is_provisional(key, expected):
    assert is_provisional(key) is expected


# ── W2-1：生产接线 + Fail-Closed 负向测试 ──────────────────────────
# 背景：V46_PRODUCTION_WIRING_AUDIT 发现 verify_provisional_values 零生产调用方，
# 属「有测试无落地」。以下测试固化其已接入 inspect_system（run_daily 的前置闸门），
# 并证明参数漂移会真的把系统拦下——而非仅仅打印一条警告。

import copy

from app.inspect_system import inspect_system


def _check(result: dict, name: str) -> dict:
    for item in result["checks"]:
        if item["name"] == name:
            return item
    raise AssertionError(f"诊断项 {name} 不存在——PROVISIONAL 闸门未接入 inspect_system")


def test_provisional_gate_is_wired_into_inspect_system():
    """闸门必须出现在生产诊断项中（接线证明，防止日后被悄悄摘掉）。"""
    result = inspect_system()
    assert _check(result, "PROVISIONAL参数登记")["ok"]
    assert _check(result, "PROVISIONAL生效值一致")["ok"]


def test_provisional_drift_fails_closed(monkeypatch, settings):
    """参数漂移 → 诊断不通过 → run_daily 终止运行（Fail-Closed 证明）。"""
    drifted = copy.deepcopy(settings)
    drifted["portfolio"]["max_single_weight"] = 0.99  # 未经回测依据的私改

    monkeypatch.setattr("app.inspect_system.load_json", lambda _p: drifted)
    result = inspect_system()

    item = _check(result, "PROVISIONAL生效值一致")
    assert not item["ok"], "参数漂移未被拦截——闸门形同虚设"
    assert "portfolio.max_single_weight" in item["detail"]
    assert result["status"] == "NEEDS_ATTENTION", "漂移未导致 Fail-Closed"


def test_provisional_marker_removal_fails_closed(monkeypatch, settings):
    """有人删掉 settings 中的登记项 → 同样必须拦下。"""
    tampered = copy.deepcopy(settings)
    tampered["provisional_parameters"] = ["minimum_history_days"]  # 只留一项

    monkeypatch.setattr("app.inspect_system.load_json", lambda _p: tampered)
    result = inspect_system()

    item = _check(result, "PROVISIONAL参数登记")
    assert not item["ok"], "登记缺失未被拦截"
    assert result["status"] == "NEEDS_ATTENTION"


def test_settings_unreadable_fails_closed(monkeypatch):
    """配置读不出来时不得放行（不可 Fail-Open）。"""
    def _boom(_p):
        raise OSError("simulated unreadable settings.json")

    monkeypatch.setattr("app.inspect_system.load_json", _boom)
    result = inspect_system()

    assert not _check(result, "PROVISIONAL参数登记")["ok"]
    assert result["status"] == "NEEDS_ATTENTION"
