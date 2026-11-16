"""V4.6 × Riskfolio-Lib 统一适配接口骨架（Wave 1 接口骨架，不含 Riskfolio-Lib 依赖）。

状态：DEFERRED_BY_SCOPE 的「接口层」先行。本模块仅定义抽象契约与 ``AllocationResult``
标准对象，**不引入** cvxpy / clarabel / vectorbt / MOSEK 等重依赖。Wave 3 再提供具体
Riskfolio-Lib 实现（``RiskfolioHRPAllocator`` 等）。

设计约束（见 ``docs/RISKFOLIO_INTEGRATION.md`` §4）：
- 所有实现必须返回 ``AllocationResult``，且不得直接写三本账（模型目标 / 决策计划 / 实际持仓）。
- ``AllocationResult.shadow_only`` 在 V4.6 首版恒为 ``True``（§4.2 / §6）。
- 本骨架只锁定 API 契约，便于 Wave 3 实现时不再反复改接口。
- 当前生产 Champion（逆下行波动率，位于 ``quant/portfolio.py``）**不属于**本接口，
  它是一个独立的、不依赖任何外部优化库的实现，未来可作为对比基准而非被本接口替换。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Any


class AllocationContractViolation(RuntimeError):
    """``AllocationResult`` 协议违约时抛出（V4.6 首版硬性约束）。"""


# ── AllocationResult 标准输出契约（§4.2，23 个字段）─────────────────────


@dataclass
class AllocationResult:
    """适配接口的唯一输出契约；所有实现类必须填充以下字段，缺字段视为协议违约。"""

    # 必填核心字段
    weights: dict[str, float]
    optimization_objective: str
    risk_measure: str | None
    solver: str
    solver_version: str
    solver_status: str
    is_optimal: bool
    is_approximate: bool
    solve_time_ms: int

    # 可空 / 带默认值字段
    risk_contribution: dict[str, float] | None = None
    statistical_clusters: dict[str, list[str]] | None = None
    constraint_violations: list[str] = field(default_factory=list)
    input_data_version: str = ""
    estimation_window: tuple[date, date] | None = None
    covariance_method: str = ""
    expected_return_method: str | None = None
    parameter_version: str = ""
    riskfolio_lib_version: str | None = None
    cvxpy_version: str | None = None
    fallback_level: int = 0
    fallback_reason: str | None = None
    diagnostics: dict[str, str] = field(default_factory=dict)
    shadow_only: bool = True

    def validate_contract(self) -> None:
        """协议自检：V4.6 首版 shadow_only 必须恒为 True；weights 必须含 CASH 且和为 1.0。"""
        if not self.shadow_only:
            raise AllocationContractViolation(
                "V4.6 首版 AllocationResult.shadow_only 必须恒为 True"
            )
        if not self.weights:
            raise AllocationContractViolation("weights 不得为空")
        if "CASH" not in self.weights:
            raise AllocationContractViolation("weights 必须包含 CASH 标的")
        total = sum(self.weights.values())
        if abs(total - 1.0) > 1e-6:
            raise AllocationContractViolation(
                f"weights 之和应为 1.0，实际 {total:.6f}"
            )


@dataclass
class AllocationConstraints:
    """组合优化约束（§3.1 A-9：换手 / 跟踪误差 /  box 约束等）。"""

    max_single_weight: float | None = None
    max_total_turnover: float | None = None
    tracking_error_bound: float | None = None
    box_bounds: dict[str, tuple[float, float]] | None = None
    additional: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvaluationReport:
    """``PortfolioEvaluationEngine`` 输出的可测试评价对象（§3.1 A-10），不只是图表。"""

    allocation: AllocationResult
    metrics: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


# ── 五个统一适配接口（抽象基类；Wave 3 提供 Riskfolio-Lib 实现）────────────


class PortfolioAllocator(ABC):
    """输入目标 + 约束，输出 ``AllocationResult``。禁止直接写三本账。"""

    @abstractmethod
    def allocate(
        self,
        target_weights: dict[str, float],
        constraints: AllocationConstraints | None = None,
    ) -> AllocationResult:
        ...


class RiskMeasureEngine(ABC):
    """计算尾部 / 回撤类风险度量（§3.1 A-6：CVaR / CDaR / 下行回撤等）。"""

    @abstractmethod
    def compute(
        self,
        weights: dict[str, float],
        measure: str,
        covariance: Any = None,
        returns: Any = None,
    ) -> float | dict[str, float]:
        ...


class RiskContributionAnalyzer(ABC):
    """分解组合风险到资产 / 因子 / 簇（§3.1 A-7）。"""

    @abstractmethod
    def decompose(
        self,
        weights: dict[str, float],
        covariance: Any = None,
    ) -> dict[str, float]:
        ...


class RiskClusterAnalyzer(ABC):
    """输出统计聚类簇（第三类风险簇输入，§5；**不得**与经济簇混用）。"""

    @abstractmethod
    def cluster(
        self,
        returns: dict[str, list[float]],
    ) -> dict[str, list[str]]:
        ...


class PortfolioEvaluationEngine(ABC):
    """产出可测试的评价对象（§3.1 A-10）。"""

    @abstractmethod
    def evaluate(
        self,
        allocation: AllocationResult,
        ground_truth: Any = None,
    ) -> EvaluationReport:
        ...
