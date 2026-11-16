"""V4.6 研究/学习型模型注册与受控接口。

对外统一入口：ForecastRequest + LearnedMarketModel 接口 + ModelRegistry
（L0-L6 门禁、默认冻结、默认关闭）+ Kronos 占位（禁用）。
"""

from __future__ import annotations

from .base import ForecastRequest, LearnedMarketModel, ModelNotConnectedError
from .kronos import (
    KRONOS_MODEL_ID,
    KronosModel,
    default_registry,
    register_kronos,
)
from .registry import ModelRegistry, ModelRegistryEntry, ModelRegistryError

__all__ = [
    "ForecastRequest",
    "LearnedMarketModel",
    "ModelNotConnectedError",
    "ModelRegistry",
    "ModelRegistryEntry",
    "ModelRegistryError",
    "KronosModel",
    "KRONOS_MODEL_ID",
    "register_kronos",
    "default_registry",
]
