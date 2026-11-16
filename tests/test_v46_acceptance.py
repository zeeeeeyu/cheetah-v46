"""V4.6 发布验收 —— 元测试。

把"发布验收脚本"本身纳入回归：任何一条工程/安全不变量被破坏，
这里都会红灯，从而保证验收清单始终可信。

本文件同时守护「诚实盘点」机制：
待办清单不得被悄悄删空，覆盖率不得被改标签粉饰。
"""

from __future__ import annotations

from app.v46_acceptance import CONDITIONS, PENDING_CONDITIONS, collect_results, run_all

VALID_AXES = {"策略轴", "工程轴", "双轴"}


def test_condition_numbering_is_continuous():
    numbers = [c[0] for c in CONDITIONS]
    assert numbers == list(range(1, len(CONDITIONS) + 1)), "已实现条件编号必须从 1 连续"


def test_all_conditions_pass():
    results = collect_results()
    failed = [(n, name, detail) for n, _axis, name, ok, detail in results if not ok]
    assert not failed, f"发布验收未全绿：{failed}"


def test_run_all_returns_zero():
    assert run_all() == 0


def test_every_condition_has_valid_axis():
    """每条验收必须且只能标注一次轴（docs/ORTHOGONAL_AXES.md §2）。"""
    for num, axis, name, _fn in CONDITIONS:
        assert axis in VALID_AXES, f"条件 {num}「{name}」轴标注非法：{axis}"


def test_pending_conditions_are_not_silently_dropped():
    """待办清单是诚实盘点的载体，不得为了「全绿」而删空。"""
    assert PENDING_CONDITIONS, "待办条件清单被清空 —— 若确已全部实现，应上移到 CONDITIONS"
    for entry in PENDING_CONDITIONS:
        num, axis, name, req_id, reason = entry
        assert axis in VALID_AXES, f"待办 {num}「{name}」轴标注非法：{axis}"
        assert req_id.startswith("REQ-"), f"待办 {num} 缺少需求编号"
        assert reason.strip(), f"待办 {num} 缺少未完成原因"


def test_pending_numbering_follows_implemented():
    """待办编号必须紧接已实现条件，保证全局编号唯一。"""
    pending_numbers = [p[0] for p in PENDING_CONDITIONS]
    expected_start = len(CONDITIONS) + 1
    assert pending_numbers == list(range(expected_start, expected_start + len(PENDING_CONDITIONS)))


def test_strategy_axis_gap_is_registered():
    """策略轴缺口不得被悄悄掩盖：要么已有已实现策略轴条件，要么仍以待办登记。

    历史上第 1-6 条曾被标为「策略轴」，但断言的是环境安全/路径可移植/入口存在性——
    全是工程轴内容，重新核定后策略轴归零。2026-08-01 续作已落地 c32（评分公式
    序数不变量），策略轴首条验收到位；本测试继续守护「归零却不登记」的失真形态。
    """
    implemented_strategy = [c for c in CONDITIONS if c[1] == "策略轴"]
    if not implemented_strategy:
        assert any(p[1] == "策略轴" for p in PENDING_CONDITIONS), (
            "策略轴既无已实现条件、也未登记为待办 —— 这正是被掩盖的失真形态"
        )


def test_no_condition_claims_both_implemented_and_pending():
    implemented_names = {c[2] for c in CONDITIONS}
    pending_names = {p[2] for p in PENDING_CONDITIONS}
    assert not (implemented_names & pending_names)
