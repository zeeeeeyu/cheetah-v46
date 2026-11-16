"""V4.6 账户模式管理。"""
from __future__ import annotations
from dataclasses import dataclass, field; from enum import Enum; from typing import Any

class AccountMode(str, Enum): MICRO = "MICRO"; RETAIL = "RETAIL"; ADVANCED_RETAIL = "ADVANCED_RETAIL"

@dataclass
class AccountModeConstraints:
    max_total_positions: int = 4; max_tactical_positions: int = 2; max_core_positions: int = 3
    max_single_weight: float = 0.40; max_cluster_weight: float = 0.35; min_cash_floor: float = 0.05
    min_trade_amount_cny: float = 100.0; max_trade_cost_ratio: float = 0.01
    require_manual_approval: bool = True; auto_execution: bool = False; note: str = ""
    def to_dict(self) -> dict[str, Any]: return {k: v for k, v in self.__dict__.items()}

DEFAULT_CONSTRAINTS = {
    AccountMode.MICRO: AccountModeConstraints(note="微型账户"),
    AccountMode.RETAIL: AccountModeConstraints(max_total_positions=6, max_tactical_positions=3, max_core_positions=4, max_single_weight=0.35, max_cluster_weight=0.30, min_cash_floor=0.03, min_trade_amount_cny=500.0, note="散户账户"),
    AccountMode.ADVANCED_RETAIL: AccountModeConstraints(max_total_positions=8, max_tactical_positions=4, max_core_positions=5, max_single_weight=0.30, max_cluster_weight=0.25, min_cash_floor=0.02, min_trade_amount_cny=1000.0, note="高级散户"),
}

class AccountModeManager:
    def __init__(self, mode: AccountMode = AccountMode.MICRO):
        self._mode = mode
        self._upgrade_conditions = {"capital": False, "running_days": False, "oos_pass": False, "data_quality": False, "dd_ok": False, "cost_ok": False}
    @property
    def mode(self) -> AccountMode: return self._mode
    @property
    def constraints(self) -> AccountModeConstraints: return DEFAULT_CONSTRAINTS.get(self._mode, DEFAULT_CONSTRAINTS[AccountMode.MICRO])
    def can_upgrade_to(self, target: AccountMode) -> tuple[bool, list[str]]:
        if self._mode == AccountMode.ADVANCED_RETAIL: return False, ["最高模式"]
        if target == self._mode: return False, [f"已经是 {target.value}"]
        missing = [k for k, v in self._upgrade_conditions.items() if not v]
        return (False, [f"条件未满足: {', '.join(missing)}"]) if missing else (True, ["可以升级"])
    def set_condition(self, c: str, met: bool):
        if c in self._upgrade_conditions: self._upgrade_conditions[c] = met
    def to_dict(self) -> dict[str, Any]: return {"mode": self._mode.value, "constraints": self.constraints.to_dict(), "conditions": dict(self._upgrade_conditions)}
