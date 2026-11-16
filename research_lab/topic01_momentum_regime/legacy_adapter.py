"""Topic-01 Legacy Champion 冻结与适配器。

诚实声明（Phase-1.5 Workstream A 已修正，见 legacy_freeze/）：
- 真实 V4.2 / V4.5.1 Legacy 源码**已定位并只读冻结**（scoped SHA256）。
- ref-mtr-v1 的数学结构已对齐到真实 Legacy 源：
  **V4.5.1 `quant/fund_features.py::compute_fund_nav_features`**（与 V4.6 生产
  `quant/features.py::compute_signal_features` 逐字一致），而非 V4.2。
- V4.2 是**独立的早期代理ETF引擎**（周期 5/20/60/120、趋势除以 vol、成交量软惩罚、
  MA120+吊灯状态），与 V4.5.1 职责不同，禁止揉成单一虚构策略。
- provenance 由 RECONSTRUCTED_FROM_V46_DOCS 升级为 FROZEN_LEGACY。
- External Evidence (Provenance) = NOT_APPLICABLE；Hypothesis External Evidence 见 evidence/。

LegacyCheetahAdapter 仅做：冻结参考配置 + 提供可复现的权重生成 + 回归自检。
禁止修改任何旧策略源码（OVERRIDE §七 / Topic-01 §四）。
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from .reference_strategy import ReferenceConfig, generate_target_weights

_HERE = os.path.dirname(os.path.abspath(__file__))

LEGACY_CHAMPION_BASELINE: dict[str, Any] = {
    "strategy_id": "ref-mtr-v1",
    "display_name": "Momentum-Trend-Regime Reference (faithful to V4.5.1 fund-NAV branch = V4.6 production)",
    "source_path": "research_lab/topic01_momentum_regime/reference_strategy.py",
    "source_provenance": "FROZEN_LEGACY",
    "provenance_source": "Quant_V4.5.1_fixed/quant/fund_features.py::compute_fund_nav_features (EQUIVALENT to V4.6 quant/features.py::compute_signal_features)",
    "frozen_baselines": {
        "momentum_signal_source": "legacy_freeze/FROZEN_V451_FUND_BASELINE.json",
        "adjacent_lineage": "legacy_freeze/FROZEN_V42_SIGNAL_BASELINE.json (independent earlier proxy-ETF engine, NOT the basis of ref-mtr-v1)",
    },
    "git_commit": "056b88f..8c9cb66 (V4.6 research milestone)",
    "parameter_version": "ref-mtr-v1-aligned-to-V4.5.1",
    "universe_version": "etf6 (510300/515000/512660/510880/518880/515030) [research proxy; real V4.5.1 uses fund NAV]",
    "data_version": "legacy_direct_etf_cache_seed 2025-12-15..2026-07-03 (BLOCKED_BY_EXTERNAL_DATA for >=5yr)",
    "execution_assumption": "T 日收盘信号 -> T+1 开盘成交（c54 不变量）",
    "cost_assumption": "conservative: 0.025% 佣金 + 0.2% 滑点 (PROVISIONAL)",
    "external_evidence": "NOT_APPLICABLE",
    "decision_status": "RESEARCH_ONLY",
    "note": "真实 Legacy 已冻结（scoped hash）。ref-mtr-v1 数学结构对齐 V4.5.1/V4.6（[20,60,120] 加权 0.10/0.35/0.55）。V4.2 为独立早期 lineage，禁止与 V4.5.1 揉合。不代表实盘历史表现。",
}


def frozen_reference_config() -> ReferenceConfig:
    """冻结的参考策略配置 —— 已对齐到真实 Legacy 源 V4.5.1 / V4.6 生产。

    horizons=[20,60,120], 权重 0.10/0.35/0.55（与 V4.5.1 compute_fund_nav_features
    及 V4.6 compute_signal_features 一致）。Topic-01 Phase-1 初版曾用 [10,20,60]
    等权（简化重建），本轮已校正以便加固数据重跑时忠实对齐真实 Legacy。
    """
    return ReferenceConfig(
        horizons=[20, 60, 120],
        horizon_weights={20: 0.10, 60: 0.35, 120: 0.55},
        use_absolute_momentum=True,
        use_cross_sectional=True,
        use_vol_scaling=True,
        vol_scaling_mode="downside",
        use_ma_alignment=True,
        use_fi=True,
        use_volume_confirmation=False,  # V4.6 未实现（V4.5.1 NAV 分支无成交量；V4.2 有 soft penalty）
        max_positions=3,
        max_single_weight=0.50,
    )


def champion_config_hash() -> str:
    cfg = frozen_reference_config()
    raw = json.dumps(cfg.__dict__, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def run_champion(frames: dict, pool: list[str], as_of: str) -> dict[str, float]:
    """LegacyCheetahAdapter：用冻结配置在 as_of 生成目标权重（只读，可复现）。"""
    return generate_target_weights(frames, pool, as_of, frozen_reference_config())


def regression_self_test(frames: dict, pool: list[str], as_of: str) -> bool:
    """回归自检：同输入两次调用产出完全一致（Topic-01 §13 测试6）。"""
    w1 = run_champion(frames, pool, as_of)
    w2 = run_champion(frames, pool, as_of)
    return w1 == w2


def write_baseline_audit(path: str | None = None) -> str:
    """写出 LEGACY_CHAMPION_BASELINE 元数据文件。"""
    path = path or os.path.join(_HERE, "LEGACY_CHAMPION_BASELINE.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(LEGACY_CHAMPION_BASELINE, fh, ensure_ascii=False, indent=2)
    return path
