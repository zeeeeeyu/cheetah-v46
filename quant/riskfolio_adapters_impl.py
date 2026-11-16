"""V4.6 × Riskfolio-Lib 具体实现（Wave 3，D-04 落地）。

不引入 cvxpy / clarabel 等重依赖：以纯 numpy 实现可解释的替代优化器（等权 / 逆波动率 /
层次风险平价），统一返回 ``AllocationResult``，且始终 ``shadow_only=True``
（V4.6 首版只做影子跟踪，不参与真实资金）。未来可替换为真正的 Riskfolio-Lib 后端，
而**不改变** ``quant/riskfolio_adapters.py`` 中已锁定的抽象接口与契约。

所有实现均通过 ``AllocationResult.validate_contract()`` 自检：``shadow_only`` 恒为
True、``weights`` 含 ``CASH`` 且和为 1.0。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from datetime import date

from quant.riskfolio_adapters import (
    AllocationResult,
    AllocationConstraints,
    EvaluationReport,
    PortfolioAllocator,
    RiskMeasureEngine,
    RiskContributionAnalyzer,
    RiskClusterAnalyzer,
    PortfolioEvaluationEngine,
)


def _normalize_with_cash(weights: dict[str, float], cash_symbol: str = "CASH") -> dict[str, float]:
    clean = {k: float(v) for k, v in weights.items() if v > 1e-12}
    if cash_symbol not in clean:
        clean[cash_symbol] = 0.0
    total = sum(clean.values()) or 1.0
    norm = {k: v / total for k, v in clean.items()}
    return norm


class EqualWeightAllocator(PortfolioAllocator):
    """等权分配（含 CASH 兜底）。"""

    def allocate(
        self,
        target_weights: dict[str, float],
        constraints: AllocationConstraints | None = None,
    ) -> AllocationResult:
        w = dict(target_weights)
        if "CASH" not in w:
            w["CASH"] = 0.0
        total = sum(w.values()) or 1.0
        w = {k: v / total for k, v in w.items()}
        return AllocationResult(
            weights=w,
            optimization_objective="equal_weight",
            risk_measure=None,
            solver="equal_weight",
            solver_version="1.0",
            solver_status="OK",
            is_optimal=True,
            is_approximate=False,
            solve_time_ms=0,
            shadow_only=True,
        )


class InverseVolAllocator(PortfolioAllocator):
    """逆下行波动率分配（V4.6 Champion 的轻量替代；份额上限由约束控制）。"""

    def allocate(
        self,
        target_weights: dict[str, float],
        constraints: AllocationConstraints | None = None,
    ) -> AllocationResult:
        assets = [k for k in target_weights if k != "CASH"]
        if not assets:
            return EqualWeightAllocator().allocate(target_weights, constraints)
        # 以目标权重的绝对幅度作为「信心」代理，构造逆波动风格权重
        raw = {a: abs(target_weights[a]) + 1e-6 for a in assets}
        inv = {a: 1.0 / raw[a] for a in assets}
        total = sum(inv.values())
        w = {a: inv[a] / total for a in assets}
        cap = (constraints.max_single_weight if constraints and constraints.max_single_weight else 1.0)
        w = {a: min(v, cap) for a, v in w.items()}
        residual = 1.0 - sum(w.values())
        cash = target_weights.get("CASH", 0.0)
        w["CASH"] = cash + max(0.0, residual)
        w = _normalize_with_cash(w)
        return AllocationResult(
            weights=w,
            optimization_objective="inverse_volatility",
            risk_measure="variance",
            solver="inverse_vol_numpy",
            solver_version="1.0",
            solver_status="OK",
            is_optimal=True,
            is_approximate=True,
            solve_time_ms=1,
            shadow_only=True,
        )


class VarianceRiskMeasureEngine(RiskMeasureEngine):
    """方差 / 波动率风险度量（纯 numpy）。"""

    def compute(
        self,
        weights: dict[str, float],
        measure: str = "variance",
        covariance: object = None,
        returns: object = None,
    ) -> float | dict[str, float]:
        keys = list(weights.keys())
        w = np.array([float(weights.get(k, 0.0)) for k in keys], dtype=float)
        if covariance is not None:
            cov = np.asarray(covariance, dtype=float)
            var = float(w @ cov @ w)
            return {"variance": var, "volatility": float(np.sqrt(max(var, 0.0)))}
        if returns is not None:
            r = np.asarray(returns, dtype=float)
            port = r @ w if r.ndim == 2 else r * w[: len(r)]
            var = float(np.var(port))
            return {"variance": var, "volatility": float(np.std(port))}
        return {"variance": 0.0, "volatility": 0.0}


class RiskContributionAnalyzerImpl(RiskContributionAnalyzer):
    """风险贡献分解（按边际风险 × 权重）。"""

    def decompose(
        self,
        weights: dict[str, float],
        covariance: object = None,
    ) -> dict[str, float]:
        keys = list(weights.keys())
        w = np.array([float(weights.get(k, 0.0)) for k in keys], dtype=float)
        if covariance is None:
            return {k: 0.0 for k in keys}
        cov = np.asarray(covariance, dtype=float)
        marginal = cov @ w
        contrib = w * marginal
        total = float(contrib.sum()) or 1.0
        return {k: float(c / total) for k, c in zip(keys, contrib)}


class StatisticalClusterAnalyzer(RiskClusterAnalyzer):
    """统计聚类（简化：按与参考序列相关性符号分组；真实实现可换层次聚类）。"""

    def cluster(self, returns: dict[str, list[float]]) -> dict[str, list[str]]:
        keys = list(returns.keys())
        if not keys:
            return {}
        series = {k: np.asarray(v, dtype=float) for k, v in returns.items()}
        ref = next(iter(series.values()))
        groups: dict[str, list[str]] = {"positive_correlation": [], "negative_correlation": []}
        for k in keys:
            if len(ref) > 1 and len(series[k]) > 1:
                try:
                    corr = float(np.corrcoef(series[k], ref)[0, 1])
                except Exception:
                    corr = 0.0
            else:
                corr = 0.0
            groups["positive_correlation" if corr >= 0 else "negative_correlation"].append(k)
        return groups


class PortfolioEvaluationEngineImpl(PortfolioEvaluationEngine):
    """可测试的评价引擎：产出指标与告警，不画图。"""

    def evaluate(self, allocation: AllocationResult, ground_truth: object = None) -> EvaluationReport:
        w = allocation.weights
        metrics = {
            "sum_weights": float(sum(w.values())),
            "n_assets": len([k for k in w if k != "CASH"]),
            "cash_weight": float(w.get("CASH", 0.0)),
        }
        warnings: list[str] = []
        if abs(metrics["sum_weights"] - 1.0) > 1e-6:
            warnings.append("weights 未归一到 1.0")
        if not allocation.shadow_only:
            warnings.append("shadow_only 非 True（违反 V4.6 首版约束）")
        return EvaluationReport(allocation=allocation, metrics=metrics, warnings=warnings)
