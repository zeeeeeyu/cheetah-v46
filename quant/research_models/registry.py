"""V4.6 研究模型注册表 —— 学习型模型进入系统的唯一受控闸口。

治理不变量（Fail-Closed，默认关闭）
==================================
- **默认关闭**：新注册的模型 ``is_enabled=False``、``trust_level=L0``、``is_frozen=True``。
- **生产门槛**：``is_production_eligible`` 需同时满足
  启用（enabled）+ 冻结（frozen）+ 可信级别 >= L4_SHADOW。
- **提升留痕**：可信级别的提升/降级、启用/禁用都产生新的不可变条目
  （``ModelRegistryEntry`` 是 frozen），并记录理由，便于审计。
- **kill-switch**：任何时候都能一键禁用某个模型（返回禁用后的新条目）。

这是"研究模型接口缺失 → Kronos 等学习模型无受控落点"这一 P1 缺口的补齐。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from quant.domain.enums import TrustLevel

__all__ = [
    "ModelRegistryEntry",
    "ModelRegistry",
    "ModelRegistryError",
]


class ModelRegistryError(ValueError):
    """研究模型注册相关的硬错误。"""


@dataclass(frozen=True)
class ModelRegistryEntry:
    """注册表中的一条模型登记（不可变）。"""

    model_id: str
    model_version: str
    trust_level: TrustLevel = TrustLevel.L0_UNVERIFIED
    is_frozen: bool = True
    is_enabled: bool = False  # 默认关闭
    description: str = ""
    disabled_reason: str = ""
    audit_trail: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_production_eligible(self) -> bool:
        """是否可被生产决策消费：启用 + 冻结 + 可信级别达标（>=L4）。"""
        return (
            self.is_enabled
            and self.is_frozen
            and self.trust_level.is_production_eligible
        )

    def _with(self, note: str, **changes: Any) -> ModelRegistryEntry:
        trail = self.audit_trail + (note,)
        return replace(self, audit_trail=trail, **changes)

    def promote(self, level: TrustLevel, *, actor: str, note: str = "") -> ModelRegistryEntry:
        if int(level.value) < int(self.trust_level.value):
            raise ModelRegistryError(
                f"promote 只能升级：{self.trust_level.name} -> {level.name} 非法，"
                f"降级请用 demote。"
            )
        msg = f"promote {self.trust_level.name}->{level.name} by {actor} {note}".strip()
        return self._with(msg, trust_level=level)

    def demote(self, level: TrustLevel, *, actor: str, note: str = "") -> ModelRegistryEntry:
        if int(level.value) > int(self.trust_level.value):
            raise ModelRegistryError(
                f"demote 只能降级：{self.trust_level.name} -> {level.name} 非法。"
            )
        msg = f"demote {self.trust_level.name}->{level.name} by {actor} {note}".strip()
        return self._with(msg, trust_level=level)

    def enable(self, *, actor: str, note: str = "") -> ModelRegistryEntry:
        return self._with(f"enable by {actor} {note}".strip(),
                          is_enabled=True, disabled_reason="")

    def disable(self, *, actor: str, reason: str) -> ModelRegistryEntry:
        return self._with(f"disable by {actor}: {reason}".strip(),
                          is_enabled=False, disabled_reason=reason)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "model_version": self.model_version,
            "trust_level": int(self.trust_level.value),
            "trust_level_name": self.trust_level.name,
            "is_frozen": self.is_frozen,
            "is_enabled": self.is_enabled,
            "is_production_eligible": self.is_production_eligible,
            "description": self.description,
            "disabled_reason": self.disabled_reason,
            "audit_trail": list(self.audit_trail),
        }


class ModelRegistry:
    """研究模型注册表（受控闸口）。"""

    def __init__(self) -> None:
        self._entries: dict[str, ModelRegistryEntry] = {}

    def __len__(self) -> int:  # pragma: no cover - trivial
        return len(self._entries)

    def __contains__(self, model_id: str) -> bool:  # pragma: no cover
        return model_id in self._entries

    def register(
        self,
        model_id: str,
        model_version: str,
        *,
        description: str = "",
        disabled_reason: str = "",
    ) -> ModelRegistryEntry:
        """注册一个研究模型。默认关闭、L0、冻结（Fail-Closed）。"""
        if model_id in self._entries:
            raise ModelRegistryError(f"模型已注册：{model_id}")
        entry = ModelRegistryEntry(
            model_id=model_id,
            model_version=model_version,
            description=description,
            disabled_reason=disabled_reason or "默认关闭：研究模型需显式治理提升后方可启用。",
            audit_trail=(f"registered version={model_version}",),
        )
        self._entries[model_id] = entry
        return entry

    def get(self, model_id: str) -> ModelRegistryEntry:
        try:
            return self._entries[model_id]
        except KeyError as exc:
            raise ModelRegistryError(f"未注册的模型：{model_id}") from exc

    def update(self, entry: ModelRegistryEntry) -> ModelRegistryEntry:
        """用新的不可变条目替换旧条目（promote/enable 等操作后回写）。"""
        if entry.model_id not in self._entries:
            raise ModelRegistryError(f"未注册的模型：{entry.model_id}")
        self._entries[entry.model_id] = entry
        return entry

    def is_production_eligible(self, model_id: str) -> bool:
        entry = self._entries.get(model_id)
        return bool(entry and entry.is_production_eligible)

    def require_production(self, model_id: str) -> ModelRegistryEntry:
        """生产守卫：拿不到"启用+冻结+>=L4"的模型就 Fail-Closed。"""
        entry = self.get(model_id)
        if not entry.is_production_eligible:
            raise ModelRegistryError(
                f"模型不可进入生产：{model_id}（enabled={entry.is_enabled}, "
                f"frozen={entry.is_frozen}, trust={entry.trust_level.name}）。"
            )
        return entry

    def production_eligible(self) -> tuple[ModelRegistryEntry, ...]:
        return tuple(e for e in self._entries.values() if e.is_production_eligible)

    def all(self) -> tuple[ModelRegistryEntry, ...]:
        return tuple(self._entries.values())

    def to_manifest(self) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self._entries.values()]
