"""D-04 落地：Riskfolio-Lib 统一适配接口骨架（Wave 1，不含重依赖）。

验证：5 个抽象接口不可被直接实例化；AllocationResult 协议自检（shadow_only 恒 True、
weights 含 CASH 且和为 1.0）；具体子类可正常返回契约对象。
"""

from __future__ import annotations

import dataclasses
import pytest

from quant.riskfolio_adapters import (
    AllocationConstraints,
    AllocationContractViolation,
    AllocationResult,
    PortfolioAllocator,
    PortfolioEvaluationEngine,
    RiskClusterAnalyzer,
    RiskContributionAnalyzer,
    RiskMeasureEngine,
)


EXPECTED_FIELDS = [
    "weights", "optimization_objective", "risk_measure", "solver", "solver_version",
    "solver_status", "is_optimal", "is_approximate", "solve_time_ms", "risk_contribution",
    "statistical_clusters", "constraint_violations", "input_data_version", "estimation_window",
    "covariance_method", "expected_return_method", "parameter_version", "riskfolio_lib_version",
    "cvxpy_version", "fallback_level", "fallback_reason", "diagnostics", "shadow_only",
]


def _valid_result(**overrides) -> AllocationResult:
    base = dict(
        weights={"600000": 0.6, "CASH": 0.4},
        optimization_objective="hrp",
        risk_measure="CVaR_95",
        solver="hrp_analytic",
        solver_version="0.0.0-skeleton",
        solver_status="N/A",
        is_optimal=True,
        is_approximate=False,
        solve_time_ms=0,
    )
    base.update(overrides)
    return AllocationResult(**base)


# ── AllocationResult 字段完整性 ────────────────────────────────────────


def test_allocation_result_has_all_23_fields():
    names = {f.name for f in dataclasses.fields(AllocationResult)}
    assert names == set(EXPECTED_FIELDS), names ^ set(EXPECTED_FIELDS)


# ── 协议自检（§4.2 / §6）────────────────────────────────────────────


def test_contract_valid_passes():
    _valid_result().validate_contract()  # 不应抛异常


def test_contract_rejects_shadow_only_false():
    with pytest.raises(AllocationContractViolation, match="shadow_only"):
        _valid_result(shadow_only=False).validate_contract()


def test_contract_rejects_weights_not_sum_to_one():
    with pytest.raises(AllocationContractViolation, match="1.0"):
        _valid_result(weights={"600000": 0.6, "CASH": 0.5}).validate_contract()


def test_contract_rejects_missing_cash():
    with pytest.raises(AllocationContractViolation, match="CASH"):
        _valid_result(weights={"600000": 1.0}).validate_contract()


# ── 抽象接口不可实例化 ────────────────────────────────────────────────


@pytest.mark.parametrize("cls", [
    PortfolioAllocator, RiskMeasureEngine, RiskContributionAnalyzer,
    RiskClusterAnalyzer, PortfolioEvaluationEngine,
])
def test_abstract_interfaces_not_instantiable(cls):
    with pytest.raises(TypeError):
        cls()


# ── 具体子类可正常返回契约对象 ───────────────────────────────────────


class _DummyAllocator(PortfolioAllocator):
    def allocate(self, target_weights, constraints=None):
        return _valid_result(
            weights={**target_weights, "CASH": 1.0 - sum(target_weights.values())},
            optimization_objective="dummy",
            solver="dummy",
        )


def test_concrete_subclass_returns_contract():
    alloc = _DummyAllocator()
    out = alloc.allocate({"600000": 0.6}, AllocationConstraints(max_single_weight=0.6))
    assert isinstance(out, AllocationResult)
    assert out.shadow_only is True
    out.validate_contract()


def test_constraints_dataclass_defaults():
    c = AllocationConstraints()
    assert c.max_single_weight is None
    assert c.additional == {}
