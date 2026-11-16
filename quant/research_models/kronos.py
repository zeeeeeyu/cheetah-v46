"""V4.6 Kronos 研究模型占位（未接入、默认禁用）。

Kronos 冻结原则（摘要）
======================
Kronos 是一个学习型市场模型。在 V4.6 阶段，它**仅在注册表中占位**，处于
冻结（frozen）+ 禁用（disabled）+ L0 状态，任何调用都 Fail-Closed 抛错。
这样做的目的：为未来接入学习型模型预留一个"受控落点"，同时保证在没有
经过完整治理（回测→纸面→影子→小额→长期验证，逐级提升到 >=L4）之前，
它绝无可能影响任何真实决策。

这是审计报告 P1「研究模型接口缺失 → Kronos 无受控落点」的补齐。
"""

from __future__ import annotations

from quant.domain.forecasts import ForecastBundle

from .base import ForecastRequest, ModelNotConnectedError
from .registry import ModelRegistry, ModelRegistryEntry

__all__ = ["KronosModel", "KRONOS_MODEL_ID", "register_kronos", "default_registry"]

KRONOS_MODEL_ID = "KRONOS"


class KronosModel:
    """Kronos 学习型模型占位实现（未接入）。

    实现了 LearnedMarketModel 接口的形状，但 predict 一律 Fail-Closed，
    因为真正的推理引擎在 V4.6 阶段尚未接入。
    """

    def __init__(self, version: str = "0.0.0-frozen") -> None:
        self._version = version

    @property
    def model_id(self) -> str:
        return KRONOS_MODEL_ID

    @property
    def model_version(self) -> str:
        return self._version

    @property
    def is_frozen(self) -> bool:
        return True

    def predict(self, request: ForecastRequest) -> ForecastBundle:  # noqa: ARG002
        raise ModelNotConnectedError(
            "Kronos 尚未接入（默认冻结+禁用）。接入需经完整治理逐级提升至 >=L4，"
            "并在注册表中显式启用。"
        )


def register_kronos(registry: ModelRegistry) -> ModelRegistryEntry:
    """把 Kronos 以"冻结+禁用+L0"的安全姿态登记进注册表。"""
    return registry.register(
        KRONOS_MODEL_ID,
        "0.0.0-frozen",
        description="Kronos 学习型市场模型（占位，未接入）。",
        disabled_reason="冻结原则：未经完整治理提升至 >=L4 前禁止启用。",
    )


def default_registry() -> ModelRegistry:
    """构造 V4.6 默认研究模型注册表（当前仅含冻结禁用的 Kronos）。"""
    registry = ModelRegistry()
    register_kronos(registry)
    return registry
