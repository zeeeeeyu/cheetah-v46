import math

import numpy as np

from quant.riskfolio_adapters import AllocationConstraints
from quant.riskfolio_adapters_impl import (
    EqualWeightAllocator,
    InverseVolAllocator,
    PortfolioEvaluationEngineImpl,
    RiskContributionAnalyzerImpl,
    StatisticalClusterAnalyzer,
    VarianceRiskMeasureEngine,
)


def test_equal_weight_contract():
    res = EqualWeightAllocator().allocate({"A": 0.3, "B": 0.3, "CASH": 0.4})
    res.validate_contract()
    assert abs(sum(res.weights.values()) - 1.0) < 1e-9
    assert res.shadow_only


def test_inverse_vol_contract_and_cap():
    res = InverseVolAllocator().allocate(
        {"A": 0.9, "B": 0.1}, AllocationConstraints(max_single_weight=0.5)
    )
    res.validate_contract()
    assert res.weights["A"] <= 0.5 + 1e-9


def test_variance_engine():
    eng = VarianceRiskMeasureEngine()
    cov = np.eye(2) * 0.04  # 单资产方差 0.04，零相关
    out = eng.compute({"A": 0.5, "B": 0.5}, covariance=cov)
    # 组合方差 = 0.5^2*0.04 + 0.5^2*0.04 = 0.02；波动率 = sqrt(0.02)
    assert abs(out["variance"] - 0.02) < 1e-9
    assert abs(out["volatility"] - math.sqrt(0.02)) < 1e-6


def test_risk_contribution():
    dec = RiskContributionAnalyzerImpl()
    cov = np.array([[0.04, 0.0], [0.0, 0.04]])
    contrib = dec.decompose({"A": 0.5, "B": 0.5}, covariance=cov)
    assert abs(sum(contrib.values()) - 1.0) < 1e-9


def test_cluster_and_eval():
    groups = StatisticalClusterAnalyzer().cluster(
        {"A": [0.1, 0.2, 0.3], "B": [0.3, 0.2, 0.1]}
    )
    assert set(groups.keys()) == {"positive_correlation", "negative_correlation"}
    res = EqualWeightAllocator().allocate({"A": 0.5, "B": 0.5})
    rep = PortfolioEvaluationEngineImpl().evaluate(res)
    assert rep.metrics["sum_weights"] == 1.0
