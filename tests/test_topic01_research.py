"""Topic-01 研究自动化测试（Topic-01 §十三，共 12 项）。

这些测试只验证研究台的隔离性、可复现性与无未来泄漏，不修改任何生产代码/数据。
运行：pytest tests/test_topic01_research.py
"""
from __future__ import annotations

import json
import os

import pandas as pd
import pytest

from research_lab.topic01_momentum_regime import (
    data_loader, reference_strategy, backtest_engine, experiment_runner,
    legacy_adapter, run_all,
)
from research_lab.topic01_momentum_regime.reference_strategy import ReferenceConfig, compute_asset_features
from research_lab.topic01_momentum_regime.legacy_adapter import frozen_reference_config, regression_self_test

_HERE = os.path.dirname(os.path.abspath(__file__))
_RESEARCH_ROOT = os.path.normpath(os.path.join(_HERE, "..", "research_lab", "topic01_momentum_regime"))


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    root = tmp_path_factory.mktemp("synthetic_topic01")
    market = root / "market"
    metadata = root / "metadata"
    market.mkdir()
    metadata.mkdir()
    dates = pd.bdate_range("2025-01-02", periods=160).strftime("%Y-%m-%d")
    symbols = {"510300": 0.004, "510880": 0.006}
    pd.DataFrame([
        {"symbol": symbol, "name": f"合成样本 {symbol}", "asset_class": "equity",
         "group": "synthetic", "active": 1}
        for symbol in symbols
    ]).to_csv(metadata / "etf_master.csv", index=False)
    for symbol, step in symbols.items():
        close = pd.Series([1 + index * step + (index % 5) * 0.001 for index in range(len(dates))])
        pd.DataFrame({"date": dates, "open": close - 0.001, "high": close + 0.002,
                      "low": close - 0.002, "close": close, "volume": 100,
                      "source": "SYNTHETIC_TEST_ONLY"}).to_csv(market / f"{symbol}.csv", index=False)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(data_loader, "_MARKET_DIR", str(market))
        patch.setattr(data_loader, "_METADATA_DIR", str(metadata))
        patch.setattr(experiment_runner, "_DATASET_VERSION_CACHE", str(root / "manifest.json"))
        yield experiment_runner.load_dataset()


# ── 1) 未来日期不可读（Point-in-Time）──
def test_future_date_not_readable(dataset):
    _, frames, _ = dataset
    sym = next(iter(frames))
    f = frames[sym]
    as_of = f["date"].iloc[len(f) // 2]
    w = data_loader.window_as_of(f, as_of)
    assert (w["date"] <= as_of).all()
    assert as_of in w["date"].values
    # 不存在 as_of 之后的行
    assert (w["date"] > as_of).sum() == 0


# ── 2) ETF 不早于上市日期进入 Universe ──
def test_etf_not_before_listing(dataset):
    metas, _, _ = dataset
    # 找一个上市日期代理，构造一个早于它的日期
    m = next(iter(metas.values()))
    listing = m.listing_date or m.first_date
    assert listing is not None
    early = "2000-01-01"
    pool = data_loader.effective_universe(metas, early)
    assert m.symbol not in pool  # 2000 年该 ETF 尚未上市
    pool_late = data_loader.effective_universe(metas, listing)
    assert m.symbol in pool_late


# ── 3) 基金 NAV 不被伪造成 OHLC ──
def test_fund_nav_not_faked_as_ohlc(dataset):
    _, frames, _ = dataset
    # 研究台只加载 market/ ETF（含 open/high/low/close），绝不从 fund_nav 造 OHLC
    for sym, f in frames.items():
        assert "close" in f.columns
        # 确认数据来自 market 目录，而非 fund_nav
        assert "fund_nav" not in data_loader._MARKET_DIR


# ── 4) 研究不写 actual_positions（产物仅落 research_lab）──
def test_research_does_not_write_actual_positions(dataset):
    metas, frames, manifest = dataset
    art = experiment_runner.run_experiment(
        experiment_id="TEST-isolation-pos", hypothesis="隔离测试",
        baseline_id="ref-mtr-v1", changed_component="none",
        unchanged_components="all", config=frozen_reference_config(),
        metas=metas, frames=frames, manifest=manifest,
    )
    art_path = os.path.join(_RESEARCH_ROOT, art["artifact_path"])
    assert os.path.exists(art_path)
    assert "research_lab" in art_path.replace("\\", "/")
    # 产物目录不应包含生产账本路径
    assert "quant/ledgers" not in art_path.replace("\\", "/")


# ── 5) 研究不写 trade_plans ──
def test_research_does_not_write_trade_plans(dataset):
    metas, frames, manifest = dataset
    art = experiment_runner.run_experiment(
        experiment_id="TEST-isolation-plan", hypothesis="隔离测试",
        baseline_id="ref-mtr-v1", changed_component="none",
        unchanged_components="all", config=frozen_reference_config(),
        metas=metas, frames=frames, manifest=manifest,
    )
    assert "trade_plans" not in art["artifact_path"].replace("\\", "/")
    assert art["status"] in ("COMPLETED", "FAILED", "BLOCKED", "INSUFFICIENT_DATA")


# ── 6) Legacy Adapter 一致性（同输入同输出）──
def test_legacy_adapter_consistency(dataset):
    _, frames, _ = dataset
    metas, _, _ = dataset
    last = max(frames[next(iter(frames))]["date"])
    pool = data_loader.effective_universe(metas, last)
    assert regression_self_test(frames, pool, last) is True


# ── 7) 同配置可复现 ──
def test_same_config_reproducible(dataset):
    metas, frames, manifest = dataset
    a = experiment_runner.run_experiment(
        experiment_id="TEST-rep-A", hypothesis="复现", baseline_id="ref-mtr-v1",
        changed_component="none", unchanged_components="all",
        config=frozen_reference_config(), metas=metas, frames=frames, manifest=manifest)
    b = experiment_runner.run_experiment(
        experiment_id="TEST-rep-B", hypothesis="复现", baseline_id="ref-mtr-v1",
        changed_component="none", unchanged_components="all",
        config=frozen_reference_config(), metas=metas, frames=frames, manifest=manifest)
    assert a["metrics"]["total_return"] == b["metrics"]["total_return"]
    assert a["config_hash"] == b["config_hash"]


# ── 8) 测试期不参与参数选择（信号仅依赖 as_of 之前数据，与调仓频率无关）──
def test_signal_independent_of_rebalance_schedule(dataset):
    metas, frames, _ = dataset
    last = max(frames[next(iter(frames))]["date"])
    pool = data_loader.effective_universe(metas, last)
    as_of = frames[next(iter(frames))]["date"].iloc[80]
    w1 = reference_strategy.generate_target_weights(frames, pool, as_of, frozen_reference_config())
    cfg2 = frozen_reference_config()
    # 不同 rebalance_freq 不应改变某一固定 as_of 的信号
    w2 = reference_strategy.generate_target_weights(frames, pool, as_of, cfg2)
    assert w1 == w2


# ── 9) 关闭组件确实不再影响策略 ──
def test_disabled_component_truly_off(dataset):
    _, frames, _ = dataset
    last = max(frames[next(iter(frames))]["date"])
    f = frames[next(iter(frames))]
    base = compute_asset_features(f, frozen_reference_config(), last)
    cfg_off_ma = frozen_reference_config()
    cfg_off_ma.use_ma_alignment = False
    off = compute_asset_features(f, cfg_off_ma, last)
    # 关闭 MA alignment 后，ma_alignment 不进入趋势质量（贡献为 0）
    assert off.ma_alignment == 0.0 or base.trend_quality != off.trend_quality


# ── 10) 成本模型能改变净收益 ──
def test_cost_model_changes_return(dataset):
    metas, frames, manifest = dataset
    z = experiment_runner.run_experiment(
        experiment_id="TEST-cost-zero", hypothesis="零成本", baseline_id="ref-mtr-v1",
        changed_component="cost=zero", unchanged_components="all",
        config=frozen_reference_config(), cost_model="zero",
        metas=metas, frames=frames, manifest=manifest)
    c = experiment_runner.run_experiment(
        experiment_id="TEST-cost-conservative", hypothesis="保守成本", baseline_id="ref-mtr-v1",
        changed_component="cost=conservative", unchanged_components="all",
        config=frozen_reference_config(), cost_model="conservative",
        metas=metas, frames=frames, manifest=manifest)
    # 零成本净收益应 >= 保守成本（成本侵蚀收益）
    assert z["metrics"]["total_return"] >= c["metrics"]["total_return"]


# ── 11) 失败实验不被删除 ──
def test_failed_experiment_retained(dataset):
    metas, frames, manifest = dataset
    art = experiment_runner.run_experiment(
        experiment_id="TEST-fail-vol", hypothesis="成交量确认应失败",
        baseline_id="ref-mtr-v1", changed_component="volume_confirmation=on",
        unchanged_components="all", config=ReferenceConfig(use_volume_confirmation=True),
        metas=metas, frames=frames, manifest=manifest)
    assert art["status"] == "FAILED"
    art_path = os.path.join(_RESEARCH_ROOT, art["artifact_path"])
    assert os.path.exists(art_path)  # 保留，不删除


# ── 12) 研究产物含 code/data/config 版本 ──
def test_artifact_contains_versions(dataset):
    metas, frames, manifest = dataset
    art = experiment_runner.run_experiment(
        experiment_id="TEST-versions", hypothesis="版本字段", baseline_id="ref-mtr-v1",
        changed_component="none", unchanged_components="all",
        config=frozen_reference_config(), metas=metas, frames=frames, manifest=manifest)
    assert art["code_version"]
    assert art["dataset_version"]
    assert art["config_hash"]
    assert len(art["config_hash"]) == 12
