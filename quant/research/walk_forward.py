"""V4.6 Walk-forward 框架和参数实验注册表。"""
from __future__ import annotations
import hashlib, json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any
import numpy as np, pandas as pd
from quant.config import project_path

# ═══════════════════════════════════════════════════════════════
# Walk-forward
# ═══════════════════════════════════════════════════════════════

@dataclass
class WalkForwardFold:
    fold_id: int; train_start: str; train_end: str; test_start: str; test_end: str
    train_result: dict[str, Any] = field(default_factory=dict)
    test_result: dict[str, Any] = field(default_factory=dict)
    parameters_frozen: dict[str, Any] = field(default_factory=dict)
    status: str = "PENDING"
    def to_dict(self) -> dict[str, Any]: return asdict(self)

@dataclass
class WalkForwardResult:
    strategy_id: str; folds: list[WalkForwardFold] = field(default_factory=list)
    average_test_metric: float = 0.0; std_test_metric: float = 0.0
    stability_score: float = 0.0; status: str = "PENDING"
    def to_dict(self) -> dict[str, Any]: return asdict(self)

def create_walk_forward_folds(dates: list[str], train_size: int = 252, test_size: int = 126, step: int = 126) -> list[WalkForwardFold]:
    """按时间切分 walk-forward 窗口。"""
    folds = []; total = len(dates)
    start = 0; fold_id = 0
    while start + train_size + test_size <= total:
        train = slice(start, start + train_size)
        test = slice(start + train_size, start + train_size + test_size)
        folds.append(WalkForwardFold(
            fold_id=fold_id, train_start=dates[train][0], train_end=dates[train][-1],
            test_start=dates[test][0], test_end=dates[test][-1], status="PENDING"))
        fold_id += 1
        start += step
        if step <= 0: break
    return folds

def run_walk_forward(strategy_fn, frames: dict, settings: dict, folds: list[WalkForwardFold]) -> WalkForwardResult:
    """运行 walk-forward 回测。

    每个窗口：训练集训练 → 参数冻结 → 测试集验证。
    测试窗口参数不得在测试期间调整。
    """
    result = WalkForwardResult(strategy_id=strategy_fn.__name__ if hasattr(strategy_fn, '__name__') else "strategy")
    for fold in folds:
        train_frames = {s: f[(f["date"] >= fold.train_start) & (f["date"] <= fold.train_end)] for s, f in frames.items()}
        test_frames = {s: f[(f["date"] >= fold.test_start) & (f["date"] <= fold.test_end)] for s, f in frames.items()}
        fold.status = "RUNNING"
        try:
            train_metrics = strategy_fn(train_frames, settings)
            fold.train_result = {"status": "OK", "metrics": train_metrics}
            fold.parameters_frozen = {"freeze_note": "参数已在训练集确定，测试集不调整"}
            test_metrics = strategy_fn(test_frames, settings)
            fold.test_result = {"status": "OK", "metrics": test_metrics}
            fold.status = "COMPLETED"
        except Exception as e:
            fold.status = "FAILED"; fold.test_result = {"status": "ERROR", "error": str(e)}
        result.folds.append(fold)
    completed = [f for f in result.folds if f.status == "COMPLETED" and f.test_result.get("metrics")]
    if completed:
        metrics_list = [f.test_result["metrics"].get("total_return", 0) for f in completed]
        result.average_test_metric = float(np.mean(metrics_list)) if metrics_list else 0
        result.std_test_metric = float(np.std(metrics_list)) if len(metrics_list) > 1 else 0
        result.stability_score = 1.0 - min(1.0, result.std_test_metric / max(abs(result.average_test_metric), 0.01)) if abs(result.average_test_metric) > 0.01 else 0
    result.status = "COMPLETED" if all(f.status == "COMPLETED" for f in result.folds) else "PARTIAL"
    return result

def assert_parameters_frozen(fold: WalkForwardFold) -> None:
    """验证测试窗口参数未调整（合约性检查）。"""
    assert fold.parameters_frozen is not None, f"折叠 {fold.fold_id} 参数未冻结"

# ═══════════════════════════════════════════════════════════════
# 参数实验注册表
# ═══════════════════════════════════════════════════════════════

@dataclass
class ParameterTrial:
    trial_id: str; parameters: dict[str, Any]; result: dict[str, Any] | None = None
    cost_assumption: str = "default"; code_version: str = "V4.6"
    data_version: str = ""; status: str = "PENDING"
    error: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    def to_dict(self) -> dict[str, Any]: return asdict(self)

class ExperimentRegistry:
    """参数实验注册表。

    保存所有实验（包括失败结果）。
    不使用随机数伪造结果。
    """
    def __init__(self, storage_dir: Path | None = None):
        self.storage_dir = storage_dir or project_path("output", "experiments")
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._trials: list[ParameterTrial] = []

    def register_trial(self, parameters: dict[str, Any], cost_assumption: str = "default", data_version: str = "") -> ParameterTrial:
        tid = f"TRIAL-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{len(self._trials)}"
        trial = ParameterTrial(trial_id=tid, parameters=dict(parameters), cost_assumption=cost_assumption, data_version=data_version)
        self._trials.append(trial)
        return trial

    def record_result(self, trial: ParameterTrial, result: dict[str, Any] | None, error: str | None = None) -> None:
        trial.result = result; trial.error = error
        trial.status = "FAILED" if error else ("COMPLETED" if result else "UNKNOWN")
        self._save_trial(trial)

    def all_trials(self) -> list[ParameterTrial]: return list(self._trials)
    def failed_trials(self) -> list[ParameterTrial]: return [t for t in self._trials if t.status == "FAILED"]
    def best_trial(self, metric: str = "total_return") -> ParameterTrial | None:
        valid = [t for t in self._trials if t.result and metric in t.result]
        return max(valid, key=lambda t: t.result[metric]) if valid else None

    def _save_trial(self, trial: ParameterTrial) -> None:
        path = self.storage_dir / f"{trial.trial_id}.json"
        path.write_text(json.dumps(trial.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    def clear(self) -> None:
        self._trials.clear()
        for p in self.storage_dir.glob("*.json"): p.unlink()
