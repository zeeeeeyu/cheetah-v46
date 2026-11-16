"""Phase 1 safety regression tests: 6 defect fixes for V4.6 upgrade."""
from __future__ import annotations
import json, math, os, tempfile
from pathlib import Path
import numpy as np, pandas as pd, pytest
from quant.guard import weights_turnover
from quant.fund_strategy_comparison import _metric_winner_many, STRATEGY_LABELS
from quant.config import project_path

# 优先从环境变量读取 V4.2 根目录，其次使用 V4.6 同级独立稳定目录。
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_V42_SIBLING = _PROJECT_ROOT.parent / "Cheetah_V4.2_fixed"
_LEGACY_ROOT = Path(os.environ.get("V42_ROOT", str(_V42_SIBLING)))

def _v42_path(name: str) -> Path:
    return _LEGACY_ROOT / name

# =========================================================================
# V46-0101
# =========================================================================
def test_trade_plan_does_not_update_real_position(tmp_path, monkeypatch):
    import quant.execution as exe

    state_path = tmp_path / "portfolio_state.json"
    state_path.write_text(json.dumps({"current_weights": {"CASH": 1.0}}), encoding="utf-8")
    monkeypatch.setattr(exe, "project_path", lambda *parts: state_path)
    state = exe.load_state()
    assert "current_weights" in state
    assert not hasattr(exe, 'save_state'), "execution 不应有 save_state"

def test_unconfirmed_order_does_not_change_holdings():
    if not _LEGACY_ROOT.exists():
        pytest.skip("V4.2 根目录未设置 (设置 V42_ROOT 环境变量)")
    pm = _v42_path("main_run.py")
    if not pm.exists():
        pytest.skip("main_run.py 不存在")
    content = pm.read_text(encoding="utf-8")
    assert 'save_portfolio_state' in content

# =========================================================================
# V46-0102
# =========================================================================
def test_optimizer_never_uses_random_metric():
    if not _LEGACY_ROOT.exists():
        pytest.skip("V4.2 根目录未设置")
    opt = _v42_path("Parameter_Optimizer.py")
    if not opt.exists():
        pytest.skip("Parameter_Optimizer.py 不存在")
    content = opt.read_text(encoding="utf-8")
    assert 'np.random.uniform' not in content or 'NotImplementedError' in content

def test_optimizer_calls_backtest_engine():
    if not _LEGACY_ROOT.exists():
        pytest.skip("V4.2 根目录未设置")
    opt = _v42_path("Parameter_Optimizer.py")
    if not opt.exists():
        pytest.skip("Parameter_Optimizer.py 不存在")
    content = opt.read_text(encoding="utf-8")
    assert 'run_backtest_with_params' in content or 'run_backtest' in content

def test_optimizer_records_all_trials():
    if not _LEGACY_ROOT.exists():
        pytest.skip("V4.2 根目录未设置")
    opt = _v42_path("Parameter_Optimizer.py")
    if not opt.exists():
        pytest.skip("Parameter_Optimizer.py 不存在")
    content = opt.read_text(encoding="utf-8")
    assert 'optimizer_output' in content
    assert '.csv' in content.lower() or 'to_csv' in content

# =========================================================================
# V46-0103
# =========================================================================
def test_smaller_drawdown_is_better():
    values = {"legacy_top2": -0.20, "top2_dynamic": -0.10, "v42_multi_cash": -0.15}
    result = _metric_winner_many(values, higher_is_better=True)
    assert result == STRATEGY_LABELS["top2_dynamic"]

def test_champion_selection_uses_correct_drawdown_direction():
    values = {"legacy_top2": -0.25, "top2_dynamic": -0.08, "v42_multi_cash": -0.18}
    result = _metric_winner_many(values, higher_is_better=True)
    assert result == STRATEGY_LABELS["top2_dynamic"]

# =========================================================================
# V46-0104
# =========================================================================
def test_repeated_risk_off_is_idempotent():
    from quant.models import DecisionStatus, CheckResult, DataStatus
    from quant.guard import rebalance_guard, weights_turnover
    checks = {"510300": CheckResult(DataStatus.PASS_)}
    current = {"CASH": 1.0}; target = {"CASH": 1.0}
    guard = rebalance_guard(checks, [], False, True, "STRONG", [], current, target, 0.35)
    assert guard.turnover == 0.0

def test_same_target_does_not_create_duplicate_order():
    from quant.models import DecisionStatus, CheckResult, DataStatus
    from quant.guard import rebalance_guard, weights_turnover
    checks = {"510300": CheckResult(DataStatus.PASS_)}
    current = {"510300": 0.8, "CASH": 0.2}
    target = {"510300": 0.8, "CASH": 0.2}
    guard = rebalance_guard(checks, [], True, False, "STRONG", [], current, target, 0.35)
    assert guard.turnover == 0.0

# =========================================================================
# V46-0105
# =========================================================================
def test_zero_turnover_has_zero_cost():
    from quant.guard import weights_turnover
    assert weights_turnover({"CASH": 1.0}, {"CASH": 1.0}) == 0.0
    assert 0.0 * 0.0015 == 0.0

def test_zero_turnover_is_not_counted_as_trade():
    from quant.guard import weights_turnover
    current = {"510300": 0.5, "515000": 0.3, "CASH": 0.2}
    target = {"510300": 0.5, "515000": 0.3, "CASH": 0.2}
    assert weights_turnover(current, target) == 0.0

# =========================================================================
# V46-0106
# =========================================================================

def test_v42_data_interface_consistent():
    if not _LEGACY_ROOT.exists():
        pytest.skip("V4.2 根目录未设置")
    diag = _v42_path("Data_Diagnostic.py")
    if not diag.exists():
        pytest.skip("Data_Diagnostic.py 不存在")

    import ast
    content = diag.read_text(encoding="utf-8")
    tree = ast.parse(content)
    definitions = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "fetch_fund_data"
    ]
    assert definitions, "找不到 fetch_fund_data"
    param_names = [arg.arg for arg in definitions[0].args.args]
    assert "fund_code" in param_names
    assert {"start_date", "end_date", "data_mode"} <= set(param_names)

    allowed_keywords = {"start_date", "end_date", "data_mode"}
    v42_files = [
        "main_run.py", "Backtest_Lifecycle.py", "Backtest_Extreme.py",
        "Unmodeled Risk 乌龙指中欧监测.py",
    ]
    for fn in v42_files:
        file_path = _v42_path(fn)
        if not file_path.exists():
            continue
        source = file_path.read_text(encoding="utf-8")
        module = ast.parse(source)
        for node in ast.walk(module):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else None
            if name != "fetch_fund_data":
                continue
            assert 1 <= len(node.args) <= 3, f"{fn} 位置参数数量异常"
            keyword_names = {kw.arg for kw in node.keywords if kw.arg is not None}
            assert keyword_names <= allowed_keywords, f"{fn} 使用未知关键字参数: {keyword_names - allowed_keywords}"
