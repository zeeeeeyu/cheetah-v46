"""Topic-01 实验运行器：单变量消融执行 + 产物落盘 + 登记。

每个实验：
- 只读复用真实 ETF 数据（data/market）
- 用 ReferenceConfig 描述「本轮被改变/保持不变的组件」
- 跑 run_strategy_backtest（T+1 / PIT / 成本模型）
- 产物写入 results/{experiment_id}.json（含 code/data/config 版本）
- 登记到 experiment_registry.csv

失败实验保留（status=FAILED/BLOCKED），绝不删除（Topic-01 §13 测试11）。
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any

from .backtest_engine import run_strategy_backtest
from .data_loader import load_etf_master, load_all_etf, build_dataset_manifest, effective_universe
from .reference_strategy import ReferenceConfig

_HERE = os.path.dirname(os.path.abspath(__file__))
_RESULTS_DIR = os.path.join(_HERE, "results")
_REGISTRY = os.path.join(_HERE, "experiment_registry.csv")
_DATASET_VERSION_CACHE = os.path.join(_HERE, "datasets", "manifest.json")

CODE_VERSION = "ref-mtr-v1"


def _config_hash(config: ReferenceConfig) -> str:
    raw = json.dumps(config.__dict__, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _dataset_version() -> str:
    os.makedirs(os.path.dirname(_DATASET_VERSION_CACHE), exist_ok=True)
    if os.path.exists(_DATASET_VERSION_CACHE):
        with open(_DATASET_VERSION_CACHE, encoding="utf-8") as fh:
            man = json.load(fh)
        return f"{man.get('global_first_date','?')}..{man.get('global_last_date','?')}:{man.get('trading_days',0)}d"
    return "unknown"


def load_dataset() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    metas = load_etf_master()
    frames = load_all_etf(metas)
    manifest = build_dataset_manifest(metas, frames)
    os.makedirs(os.path.dirname(_DATASET_VERSION_CACHE), exist_ok=True)
    with open(_DATASET_VERSION_CACHE, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    return metas, frames, manifest


# 实验注册表字段（Topic-01 §五）
REGISTRY_FIELDS = [
    "experiment_id", "hypothesis", "baseline_id", "changed_component",
    "unchanged_components", "dataset_version", "universe_version", "train_period",
    "validation_period", "test_period", "cost_model", "execution_model", "random_seed",
    "code_version", "status", "started_at", "finished_at", "artifact_path", "notes",
]


def _append_registry(row: dict[str, Any]) -> None:
    write_header = not os.path.exists(_REGISTRY)
    os.makedirs(os.path.dirname(_REGISTRY), exist_ok=True)
    with open(_REGISTRY, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=REGISTRY_FIELDS)
        if write_header:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in REGISTRY_FIELDS})


def run_experiment(
    *,
    experiment_id: str,
    hypothesis: str,
    baseline_id: str,
    changed_component: str,
    unchanged_components: str,
    config: ReferenceConfig,
    rebalance_freq: int = 5,
    cost_model: str = "conservative",
    dataset_version: str = "",
    universe_version: str = "etf6",
    train_period: str = "",
    validation_period: str = "",
    test_period: str = "",
    random_seed: str = "fixed",
    notes: str = "",
    metas: dict | None = None,
    frames: dict | None = None,
    manifest: dict | None = None,
) -> dict[str, Any]:
    if metas is None or frames is None:
        metas, frames, manifest = load_dataset()
    if not dataset_version:
        dataset_version = _dataset_version()

    started = datetime.now(timezone.utc).isoformat()
    cfg_hash = _config_hash(config)
    # 用全局最后日期作为 as_of 上界计算池（整段回测）
    last_date = manifest.get("global_last_date")
    pool = effective_universe(metas, last_date) if last_date else sorted(frames.keys())
    os.makedirs(_RESULTS_DIR, exist_ok=True)
    art_path = os.path.join(_RESULTS_DIR, f"{experiment_id}.json")

    try:
        out = run_strategy_backtest(
            frames, pool, config,
            strategy_id=experiment_id, code_version=CODE_VERSION,
            dataset_version=dataset_version, cost_model=cost_model,
            rebalance_freq=rebalance_freq, config_hash=cfg_hash,
        )
    except NotImplementedError as exc:
        # 例如 volume_confirmation 未实现：保留为 FAILED，绝不删除（Topic-01 §13 测试11）
        finished = datetime.now(timezone.utc).isoformat()
        failed_artifact = {
            "experiment_id": experiment_id, "hypothesis": hypothesis,
            "baseline_id": baseline_id, "changed_component": changed_component,
            "code_version": CODE_VERSION, "dataset_version": dataset_version,
            "config_hash": cfg_hash, "config": config.__dict__, "cost_model": cost_model,
            "status": "FAILED", "artifact_path": os.path.relpath(art_path, _HERE),
            "error": f"NOT_IMPLEMENTED: {exc}",
            "warnings": [str(exc)], "started_at": started, "finished_at": finished,
        }
        with open(art_path, "w", encoding="utf-8") as fh:
            json.dump(failed_artifact, fh, ensure_ascii=False, indent=2)
        _append_registry({
            "experiment_id": experiment_id, "hypothesis": hypothesis, "baseline_id": baseline_id,
            "changed_component": changed_component, "unchanged_components": unchanged_components,
            "dataset_version": dataset_version, "universe_version": universe_version,
            "train_period": train_period, "validation_period": validation_period,
            "test_period": test_period, "cost_model": cost_model, "execution_model": "T+1_open_PIT",
            "random_seed": random_seed, "code_version": CODE_VERSION, "status": "FAILED",
            "started_at": started, "finished_at": finished,
            "artifact_path": os.path.relpath(art_path, _HERE),
            "notes": f"NOT_IMPLEMENTED: {exc}",
        })
        return failed_artifact

    finished = datetime.now(timezone.utc).isoformat()
    artifact = {
        "experiment_id": experiment_id,
        "hypothesis": hypothesis,
        "baseline_id": baseline_id,
        "changed_component": changed_component,
        "unchanged_components": unchanged_components,
        "code_version": CODE_VERSION,
        "dataset_version": dataset_version,
        "universe_version": universe_version,
        "config_hash": cfg_hash,
        "config": config.__dict__,
        "cost_model": cost_model,
        "status": out.status,
        "artifact_path": os.path.relpath(art_path, _HERE),
        "metrics": {
            "total_return": out.total_return,
            "annual_return": out.annual_return,
            "max_drawdown": out.max_drawdown,
            "sharpe": out.sharpe,
            "sortino": out.sortino,
            "calmar": out.calmar,
            "cvar_95": out.cvar_95,
            "total_turnover": out.total_turnover,
            "trade_count": out.trade_count,
            "avg_exposure": out.avg_exposure,
            "avg_cash": out.avg_cash,
            "period_start": out.period_start,
            "period_end": out.period_end,
        },
        "warnings": out.warnings,
        "started_at": started,
        "finished_at": finished,
    }
    os.makedirs(_RESULTS_DIR, exist_ok=True)
    art_path = os.path.join(_RESULTS_DIR, f"{experiment_id}.json")
    with open(art_path, "w", encoding="utf-8") as fh:
        json.dump(artifact, fh, ensure_ascii=False, indent=2)

    _append_registry({
        "experiment_id": experiment_id, "hypothesis": hypothesis, "baseline_id": baseline_id,
        "changed_component": changed_component, "unchanged_components": unchanged_components,
        "dataset_version": dataset_version, "universe_version": universe_version,
        "train_period": train_period, "validation_period": validation_period,
        "test_period": test_period, "cost_model": cost_model, "execution_model": "T+1_open_PIT",
        "random_seed": random_seed, "code_version": CODE_VERSION, "status": out.status,
        "started_at": started, "finished_at": finished,
        "artifact_path": os.path.relpath(art_path, _HERE), "notes": notes,
    })

    return artifact
