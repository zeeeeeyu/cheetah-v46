"""V4.6 预测领域模型 —— 模型目标账（三本账之一）的上游输入。

ForecastBundle 是研究/学习型模型（如 Kronos）的一次预测快照。

严格边界：
- 它只描述"模型认为未来会怎样"，不含任何交易、订单或持仓语义。
- 它是不可变（frozen）、只读对象；模型本身默认冻结（is_frozen_model=True）。
- 它携带可信级别 TrustLevel（L0-L6）；生产默认只消费 >= L4 的预测。
- 它不能直接变成 TargetPortfolio / DecisionProposal / ActualPosition，
  必须经过策略层显式转换，且转换过程受可信级别门禁约束。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .enums import DataStatus, TrustLevel


@dataclass(frozen=True)
class ForecastBundle:
    """研究/学习型模型的预测输出（不可变、只读）。

    这是"三本账"链条最上游的对象，代表一次模型推理结果。
    它与真实资金完全隔离：即便预测再乐观，也无法绕过策略层、
    决策仲裁层、执行层与对账层去影响真实持仓。
    """
    bundle_id: str
    """预测批次唯一键（含模型、版本、as_of，用于幂等与血缘追踪）。"""

    model_id: str
    """模型标识（如 KRONOS）。"""

    model_version: str
    """模型版本（冻结版本号）。"""

    as_of: str
    """预测所依据的市场数据时间戳（market_as_of）。"""

    horizon: str
    """预测视野，如 SHORT / MEDIUM / LONG。"""

    predictions: dict[str, float] = field(default_factory=dict)
    """标的 -> 预测分值/预期收益。纯数值，不含仓位含义。"""

    trust_level: TrustLevel = TrustLevel.L0_UNVERIFIED
    """可信级别（L0-L6）。默认最低级，除非显式提升。"""

    is_frozen_model: bool = True
    """模型是否处于冻结状态。冻结原则要求生产模型必须冻结。"""

    data_status: DataStatus = DataStatus.PASS
    """上游数据质量状态。数据不达标时预测不可被消费。"""

    reasons: tuple[str, ...] = field(default_factory=tuple)
    """模型输出的理由/解释，用于审计。"""

    metadata: dict[str, Any] = field(default_factory=dict)
    """附加元数据（特征快照 id、置信度分布等）。"""

    @property
    def is_production_eligible(self) -> bool:
        """是否可被生产决策消费。

        需同时满足：可信级别达标、模型已冻结、数据质量通过。
        """
        return (
            self.trust_level.is_production_eligible
            and self.is_frozen_model
            and self.data_status == DataStatus.PASS
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "bundle_id": self.bundle_id,
            "model_id": self.model_id,
            "model_version": self.model_version,
            "as_of": self.as_of,
            "horizon": self.horizon,
            "predictions": dict(self.predictions),
            "trust_level": int(self.trust_level.value),
            "is_frozen_model": self.is_frozen_model,
            "data_status": self.data_status.value,
            "reasons": list(self.reasons),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ForecastBundle:
        kwargs = dict(data)
        if "trust_level" in kwargs and not isinstance(kwargs["trust_level"], TrustLevel):
            kwargs["trust_level"] = TrustLevel(int(kwargs["trust_level"]))
        if "data_status" in kwargs and isinstance(kwargs["data_status"], str):
            kwargs["data_status"] = DataStatus(kwargs["data_status"])
        if "predictions" in kwargs and kwargs["predictions"] is not None:
            kwargs["predictions"] = dict(kwargs["predictions"])
        if "reasons" in kwargs and isinstance(kwargs["reasons"], list):
            kwargs["reasons"] = tuple(kwargs["reasons"])
        if "metadata" in kwargs and kwargs["metadata"] is not None:
            kwargs["metadata"] = dict(kwargs["metadata"])
        return cls(**kwargs)
