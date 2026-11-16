"""V4.6 资金权限管理。"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# V4.6 资金权限管理。CapitalPermission 统一来自 quant.domain.enums
from quant.domain.enums import CapitalPermission
from dataclasses import dataclass, field
from typing import Any

DEFAULT_PERMISSIONS: dict[str, CapitalPermission] = {
    "CORE_FUND": CapitalPermission.REAL, "TACTICAL_ETF": CapitalPermission.REAL_LIMITED,
    "EXPERIMENTAL": CapitalPermission.PAPER, "004813_WATCH": CapitalPermission.OBSERVATION_ONLY,
}

@dataclass
class StrategyPermission:
    strategy_id: str; permission: CapitalPermission; max_risk_budget_pct: float = 0.0
    max_positions: int = 4; max_single_weight: float = 0.4; note: str = ""
    def to_dict(self) -> dict[str, Any]:
        return {"strategy_id": self.strategy_id, "permission": self.permission.value, "max_risk_budget_pct": self.max_risk_budget_pct, "max_positions": self.max_positions, "max_single_weight": self.max_single_weight, "note": self.note}
    def is_real(self) -> bool: return self.permission in (CapitalPermission.REAL, CapitalPermission.REAL_LIMITED)
    def can_generate_orders(self) -> bool: return self.permission in (CapitalPermission.REAL, CapitalPermission.REAL_LIMITED)

class PermissionManager:
    def __init__(self, defaults: dict[str, CapitalPermission] | None = None):
        self._permissions: dict[str, StrategyPermission] = {}
        for sid, perm in (defaults or DEFAULT_PERMISSIONS).items(): self.set_default(sid, perm)
    def set_default(self, sid: str, perm: CapitalPermission) -> StrategyPermission:
        bp = self._default_budget(perm)
        sp = StrategyPermission(sid, perm, max_risk_budget_pct=bp, max_positions=self._default_positions(perm), max_single_weight=self._default_single_weight(perm), note=f"默认: {perm.value}")
        self._permissions[sid] = sp; return sp
    def get(self, sid: str) -> StrategyPermission | None: return self._permissions.get(sid)
    def get_or_default(self, sid: str) -> StrategyPermission:
        if sid not in self._permissions: self.set_default(sid, DEFAULT_PERMISSIONS.get(sid, CapitalPermission.PAPER))
        return self._permissions[sid]
    def all_permissions(self) -> dict[str, StrategyPermission]: return dict(self._permissions)
    def to_dict(self) -> dict[str, Any]: return {k: v.to_dict() for k, v in self._permissions.items()}
    @staticmethod
    def _default_budget(p): return {"REAL": 0.70, "REAL_LIMITED": 0.25, "PAPER": 0.0, "DISABLED": 0.0, "OBSERVATION_ONLY": 0.0}.get(p.value if hasattr(p, 'value') else p, 0.0)
    @staticmethod
    def _default_positions(p): return {"REAL": 4, "REAL_LIMITED": 3, "PAPER": 6, "DISABLED": 0, "OBSERVATION_ONLY": 0}.get(p.value if hasattr(p, 'value') else p, 0)
    @staticmethod
    def _default_single_weight(p): return {"REAL": 0.40, "REAL_LIMITED": 0.25, "PAPER": 0.50, "DISABLED": 0.0, "OBSERVATION_ONLY": 0.0}.get(p.value if hasattr(p, 'value') else p, 0)
