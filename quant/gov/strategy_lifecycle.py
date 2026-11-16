"""V4.6 策略生命周期和模型状态。StrategyLifecycle/ModelStatus 统一来自 quant.domain.enums。"""
from __future__ import annotations
from quant.domain.enums import StrategyLifecycle, ModelStatus, CapitalPermission
from dataclasses import dataclass, field; from datetime import datetime; from typing import Any

@dataclass
class StrategyRecord:
    strategy_id: str; name: str; version: str = "0.0.0"
    lifecycle: StrategyLifecycle = StrategyLifecycle.IDEA; model_status: ModelStatus = ModelStatus.MODEL_OK
    paper_start_date: str | None = None; live_start_date: str | None = None; last_review_date: str | None = None
    notes: list[str] = field(default_factory=list); created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    def promote(self, target: StrategyLifecycle) -> tuple[bool, str]:
        if not self.lifecycle.can_promote_to(target): return False, f"不能从 {self.lifecycle.value} 到 {target.value}"
        if target in (StrategyLifecycle.REAL_LIMITED, StrategyLifecycle.REAL) and not self.model_status.can_promote_to_live(): return False, f"模型状态 {self.model_status.value} 不允许实盘"
        self.lifecycle = target
        if target == StrategyLifecycle.PAPER: self.paper_start_date = datetime.now().strftime("%Y-%m-%d")
        if target == StrategyLifecycle.REAL: self.live_start_date = datetime.now().strftime("%Y-%m-%d")
        return True, f"晋升到 {target.value}"
    def set_model_status(self, status: ModelStatus, reason: str) -> None:
        self.model_status = status; self.notes.append(f"[{datetime.now().strftime('%Y-%m-%d')}] {status.value}: {reason}")
        if status == ModelStatus.MODEL_REVIEW_REQUIRED: self.last_review_date = datetime.now().strftime("%Y-%m-%d")

class StrategyRegistry:
    def __init__(self): self._strategies: dict[str, StrategyRecord] = {}
    def register(self, sid: str, name: str, version: str = "0.0.0") -> StrategyRecord:
        sr = StrategyRecord(strategy_id=sid, name=name, version=version); self._strategies[sid] = sr; return sr
    def get(self, sid: str) -> StrategyRecord | None: return self._strategies.get(sid)
    def all(self) -> list[StrategyRecord]: return list(self._strategies.values())
