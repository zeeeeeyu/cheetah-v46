"""Topic-01 Phase-1 实验编排：T01-01 / T01-02 / T01-03 / T01-04 / T01-10。

Phase 1 停止门（OVERRIDE 对齐补丁 §7）：只执行以上 5 组，完成后停止，
生成 T01_PHASE1_REVIEW_PACKET.md 等待人工复核。不自动进入第二阶段。

所有实验：
- 数据：quant_v4_6/data/market 真实 ETF（只读）
- 时间语义：T+1 / PIT（复用 c54 不变量）
- 成本：conservative（PROVISIONAL）
- 三维证据框架：每项输出 External/Internal/Decision（见报告）
"""
from __future__ import annotations

import csv
import json
import os
from typing import Any

from .experiment_runner import load_dataset, run_experiment
from .legacy_adapter import (
    LEGACY_CHAMPION_BASELINE, frozen_reference_config, write_baseline_audit,
)
from .reference_strategy import ReferenceConfig

_HERE = os.path.dirname(os.path.abspath(__file__))
MATRIX_PATH = os.path.join(_HERE, "T01_STRATEGY_ATTRIBUTION_MATRIX.csv")

BASE = frozen_reference_config()
BASELINE_ID = "ref-mtr-v1"


def _cfg(**overrides) -> ReferenceConfig:
    base = frozen_reference_config()
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


# ── 实验清单（Phase 1）──
def build_experiments() -> list[dict[str, Any]]:
    ex: list[dict[str, Any]] = []

    # T01-01 Absolute vs Cross-Sectional Momentum
    ex.append(dict(experiment_id="T01-01-A", hypothesis="仅绝对动量即可选股",
                   changed_component="cross_sectional=off", unchanged_components="absolute,vol,ma,fi",
                   config=_cfg(use_cross_sectional=False), notes="A: 仅 Absolute Momentum"))
    ex.append(dict(experiment_id="T01-01-B", hypothesis="仅横截面排名即可选股",
                   changed_component="absolute=off", unchanged_components="cross_sectional,vol,ma,fi",
                   config=_cfg(use_absolute_momentum=False), notes="B: 仅 Cross-Sectional Ranking"))
    ex.append(dict(experiment_id="T01-01-C", hypothesis="绝对+横截面组合产生增量",
                   changed_component="(baseline)", unchanged_components="all",
                   config=BASE, notes="C: Absolute + Cross-Sectional (champion)"))

    # T01-02 Multi-Horizon Attribution (legacy 5/20/60/120)
    for h in (5, 20, 60, 120):
        ex.append(dict(experiment_id=f"T01-02-only_{h}", hypothesis=f"仅 {h} 日动量窗口",
                       changed_component=f"horizons=[{h}]", unchanged_components="vol,ma,fi,abs,cs",
                       config=_cfg(horizons=[h]), notes=f"only_{h}"))
    for h in (5, 20, 60, 120):
        rem = [x for x in (5, 20, 60, 120) if x != h]
        ex.append(dict(experiment_id=f"T01-02-remove_{h}", hypothesis=f"移除 {h} 日窗口",
                       changed_component=f"horizons={rem}", unchanged_components="vol,ma,fi,abs,cs",
                       config=_cfg(horizons=rem), notes=f"remove_{h}"))
    ex.append(dict(experiment_id="T01-02-equal", hypothesis="多周期等权组合",
                   changed_component="horizon_weights=equal", unchanged_components="vol,ma,fi,abs,cs",
                   config=_cfg(horizons=[5, 20, 60, 120]), notes="equal_weight_multi_horizon"))
    ex.append(dict(experiment_id="T01-02-legacy", hypothesis="Legacy 多周期加权组合",
                   changed_component="horizon_weights=legacy", unchanged_components="vol,ma,fi,abs,cs",
                   config=_cfg(horizons=[5, 20, 60, 120], horizon_weights={5: 0.10, 20: 0.20, 60: 0.35, 120: 0.35}),
                   notes="legacy_weight_multi_horizon"))

    # T01-03 Volatility Scaling Attribution (最高优先级)
    ex.append(dict(experiment_id="T01-03-A", hypothesis="Legacy 逆下行波动加权贡献最大",
                   changed_component="vol=downside", unchanged_components="abs,cs,ma,fi,horizons",
                   config=_cfg(use_vol_scaling=True, vol_scaling_mode="downside"), notes="A: legacy inverse downside vol"))
    ex.append(dict(experiment_id="T01-03-B", hypothesis="等权不弱于波动率缩放",
                   changed_component="vol=off(equal)", unchanged_components="abs,cs,ma,fi,horizons",
                   config=_cfg(use_vol_scaling=False), notes="B: equal weight"))
    ex.append(dict(experiment_id="T01-03-C", hypothesis="无波动率缩放(原始打分)表现",
                   changed_component="vol=raw", unchanged_components="abs,cs,ma,fi,horizons",
                   config=_cfg(use_vol_scaling=True, vol_scaling_mode="raw"), notes="C: no vol scaling, raw score"))
    ex.append(dict(experiment_id="T01-03-D", hypothesis="逆全波动加权作为额外对照",
                   changed_component="vol=full", unchanged_components="abs,cs,ma,fi,horizons",
                   config=_cfg(use_vol_scaling=True, vol_scaling_mode="full"), notes="D: inverse full vol"))

    # T01-04 Trend Quality Attribution
    ex.append(dict(experiment_id="T01-04-base", hypothesis="趋势质量全开基线",
                   changed_component="(baseline)", unchanged_components="all",
                   config=BASE, notes="baseline all-on"))
    ex.append(dict(experiment_id="T01-04-off_FI", hypothesis="关闭 FI/fragmentation 后表现变化",
                   changed_component="fi=off", unchanged_components="abs,cs,vol,ma",
                   config=_cfg(use_fi=False), notes="disable FI"))
    ex.append(dict(experiment_id="T01-04-off_MA", hypothesis="关闭 MA alignment 后表现变化",
                   changed_component="ma_alignment=off", unchanged_components="abs,cs,vol,fi",
                   config=_cfg(use_ma_alignment=False), notes="disable MA alignment"))
    ex.append(dict(experiment_id="T01-04-off_volume", hypothesis="关闭 volume confirmation（应 NOT_IMPLEMENTED）",
                   changed_component="volume_confirmation=on", unchanged_components="abs,cs,vol,ma,fi",
                   config=_cfg(use_volume_confirmation=True), notes="V4.6 未实现，预期 FAILED 保留"))

    # T01-10 Multi-Speed vs Single-Speed
    ex.append(dict(experiment_id="T01-10-fast", hypothesis="快速度(短周期)稳定性",
                   changed_component="speed=fast", unchanged_components="vol,ma,fi,abs,cs",
                   config=_cfg(horizons=[5, 10]), notes="fast"))
    ex.append(dict(experiment_id="T01-10-medium", hypothesis="中速度稳定性",
                   changed_component="speed=medium", unchanged_components="vol,ma,fi,abs,cs",
                   config=_cfg(horizons=[20, 60]), notes="medium"))
    ex.append(dict(experiment_id="T01-10-slow", hypothesis="慢速度稳定性",
                   changed_component="speed=slow", unchanged_components="vol,ma,fi,abs,cs",
                   config=_cfg(horizons=[120]), notes="slow"))
    ex.append(dict(experiment_id="T01-10-multi", hypothesis="多速度组合优于单速度",
                   changed_component="speed=multi", unchanged_components="vol,ma,fi,abs,cs",
                   config=_cfg(horizons=[5, 20, 60, 120], horizon_weights={5: 0.10, 20: 0.20, 60: 0.35, 120: 0.35}),
                   notes="multi-speed"))

    return ex


def main() -> None:
    print("=== Topic-01 Phase-1 实验编排 ===")
    write_baseline_audit()
    # 截断并重置注册表（安全删除不可用，故用截断而非删除，避免重复行）
    from .experiment_runner import REGISTRY_FIELDS
    reg_path = os.path.join(_HERE, "experiment_registry.csv")
    with open(reg_path, "w", newline="", encoding="utf-8") as fh:
        fh.write(",".join(REGISTRY_FIELDS) + "\n")
    metas, frames, manifest = load_dataset()
    dv = f"{manifest['global_first_date']}..{manifest['global_last_date']}:{manifest['trading_days']}d"
    print(f"数据: {dv}  ETF数: {manifest['asset_count']}")

    results = []
    for spec in build_experiments():
        art = run_experiment(
            experiment_id=spec["experiment_id"], hypothesis=spec["hypothesis"],
            baseline_id=BASELINE_ID, changed_component=spec["changed_component"],
            unchanged_components=spec["unchanged_components"], config=spec["config"],
            dataset_version=dv, notes=spec.get("notes", ""),
            metas=metas, frames=frames, manifest=manifest,
        )
        m = art.get("metrics", {})
        print(f"  {spec['experiment_id']:18s} status={art['status']:10s} "
              f"ret={m.get('total_return',0):.3%} sharpe={m.get('sharpe')} dd={m.get('max_drawdown',0):.3%} "
              f"turn={m.get('total_turnover',0):.2f}")
        results.append(art)

    # 写矩阵
    _write_matrix(results)
    print(f"\n矩阵已写入: {MATRIX_PATH}")
    print(f"实验总数: {len(results)}  状态分布: "
          f"{_status_counts(results)}")


def _status_counts(results: list[dict]) -> dict:
    from collections import Counter
    return dict(Counter(r["status"] for r in results))


def _write_matrix(results: list[dict[str, Any]]) -> None:
    cols = ["experiment", "changed_component", "CAGR", "total_return", "Sharpe",
            "Sortino", "MaxDD", "Calmar", "CVaR95", "turnover", "exposure",
            "trade_count", "cost_model", "test_period", "status"]
    with open(MATRIX_PATH, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in results:
            m = r.get("metrics", {})
            w.writerow([
                r["experiment_id"], r.get("changed_component", ""),
                _fmt(m.get("annual_return")), _fmt(m.get("total_return")),
                _fmt(m.get("sharpe")), _fmt(m.get("sortino")), _fmt(m.get("max_drawdown")),
                _fmt(m.get("calmar")), _fmt(m.get("cvar_95")), _fmt(m.get("total_turnover")),
                _fmt(m.get("avg_exposure")), m.get("trade_count", 0), r.get("cost_model", ""),
                f"{m.get('period_start','')}..{m.get('period_end','')}", r.get("status", ""),
            ])


def _fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)


if __name__ == "__main__":
    main()
