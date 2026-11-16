"""V4.6 研究/学习型模型的受控接口。

设计哲学（研究 / 生产隔离）
==========================
Kronos 这类学习型模型是"研究资产"，不是"生产决策器"。它们的输出必须：
- 以只读的 ``ForecastBundle`` 形态存在（不含任何仓位/订单语义）；
- 携带可信级别 ``TrustLevel``（L0-L6），默认最低；
- 默认冻结（frozen）、默认关闭（disabled），除非经过显式治理提升；
- 只有 >= L4 且冻结且启用时，才允许被生产决策消费。

本模块只定义"接口与请求对象"，不实现任何具体模型推理——真正的模型
（如 Kronos）在 V4.6 阶段保持**未接入**，仅在注册表中占位（禁用）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from quant.domain.forecasts import ForecastBundle

__all__ = ["ForecastRequest", "LearnedMarketModel", "ModelNotConnectedError"]


class ModelNotConnectedError(RuntimeError):
    """研究模型尚未接入（占位）。任何调用都应 Fail-Closed。"""


@dataclass(frozen=True)
class ForecastRequest:
    """向研究模型请求一次预测（不可变）。

    它只描述"要预测什么"，不携带资金/仓位含义；模型据此产出 ForecastBundle。
    """

    request_id: str
    symbols: tuple[str, ...]
    as_of: str  # 市场数据截止时间（market_as_of）
    horizon: str = "MEDIUM"  # SHORT / MEDIUM / LONG
    environment: str = "BACKTEST"
    features_ref: str | None = None  # 特征快照的血缘引用
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "symbols": list(self.symbols),
            "as_of": self.as_of,
            "horizon": self.horizon,
            "environment": self.environment,
            "features_ref": self.features_ref,
            "params": dict(self.params),
        }


@runtime_checkable
class LearnedMarketModel(Protocol):
    """学习型市场模型的最小接口。

    实现者必须暴露稳定的身份（model_id/version）、冻结状态，以及一个
    产出只读 ForecastBundle 的 ``predict``。生产链路只信任冻结模型。
    """

    @property
    def model_id(self) -> str: ...

    @property
    def model_version(self) -> str: ...

    @property
    def is_frozen(self) -> bool: ...

    def predict(self, request: ForecastRequest) -> ForecastBundle: ...
