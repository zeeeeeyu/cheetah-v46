"""数据身份治理 测试。

覆盖：
- 资产主数据加载与代理信号识别（signal_symbol != trade_symbol）
- 数据血缘登记（来源/截止/哈希/质量）
- 代理隔离三条硬约束（Fail-Closed）：
  1. 直连数据不得伪装（origin=DIRECT 但 source_symbol!=symbol → 拒绝）
  2. 生产只吃直连且合格
  3. 代理/派生/合成永不进生产
- 血缘冲突检测（同标的不同内容 → 报错）与幂等
"""

from __future__ import annotations

import pytest

from quant.config import project_path
from quant.data_identity import (
    AssetMaster,
    AssetMasterError,
    DataIdentityError,
    DataIdentityRegistry,
    DataLineageRecord,
    DataOrigin,
    content_fingerprint,
)
from quant.domain.enums import DataStatus


# ── 资产主数据 ────────────────────────────────────────────────────────
class TestAssetMaster:
    def test_load_real_asset_master(self):
        master = AssetMaster.from_json(project_path("config", "assets", "asset_master.json"))
        assert len(master) == 13
        rec = master.get("005658")
        assert rec.name == "华夏沪深300ETF联接C"
        assert rec.is_open_end_fund  # ETF_LINKED_FUND

    def test_unknown_symbol_raises(self):
        master = AssetMaster.from_json(project_path("config", "assets", "asset_master.json"))
        with pytest.raises(AssetMasterError):
            master.get("999999")

    def test_duplicate_symbol_rejected(self):
        from quant.data_identity import AssetRecord

        with pytest.raises(AssetMasterError):
            AssetMaster([
                AssetRecord("A", "a", "ETF", "A", "A"),
                AssetRecord("A", "a2", "ETF", "A", "A"),
            ])

    def test_proxy_signal_detection(self):
        from quant.data_identity import AssetRecord

        direct = AssetRecord("510300", "沪深300ETF", "ETF", "510300", "510300")
        proxy = AssetRecord("F001", "某场外基金", "ACTIVE_FUND", "F001", "000300")
        assert direct.uses_proxy_signal is False
        assert proxy.uses_proxy_signal is True


# ── 内容指纹 ─────────────────────────────────────────────────────────
class TestFingerprint:
    def test_same_content_same_hash(self):
        assert content_fingerprint("hello") == content_fingerprint("hello")

    def test_different_content_different_hash(self):
        assert content_fingerprint("hello") != content_fingerprint("world")

    def test_dataframe_fingerprint(self):
        pd = pytest.importorskip("pandas")
        a = pd.DataFrame({"close": [1.0, 2.0, 3.0]})
        b = pd.DataFrame({"close": [1.0, 2.0, 3.0]})
        c = pd.DataFrame({"close": [1.0, 2.0, 9.0]})
        assert content_fingerprint(a) == content_fingerprint(b)
        assert content_fingerprint(a) != content_fingerprint(c)


# ── 代理隔离硬约束 ────────────────────────────────────────────────────
class TestProxyIsolation:
    def test_direct_record_must_match_source_symbol(self):
        # 硬约束1：DIRECT 但 source_symbol != symbol → 拒绝登记（代理伪装）
        with pytest.raises(DataIdentityError):
            DataLineageRecord(
                symbol="510300",
                source="local_csv",
                source_symbol="000300",  # 用指数冒充 ETF 直连
                origin=DataOrigin.DIRECT,
                content_hash="deadbeef",
                as_of="2026-07-31",
            )

    def test_direct_matching_is_production_eligible(self):
        rec = DataLineageRecord(
            symbol="510300",
            source="akshare_direct_etf",
            source_symbol="510300",
            origin=DataOrigin.DIRECT,
            content_hash="abc",
            as_of="2026-07-31",
            quality=DataStatus.PASS,
        )
        assert rec.is_production_eligible is True

    def test_proxy_never_production_eligible(self):
        # 硬约束3：代理数据显式登记允许，但永不可进生产
        rec = DataLineageRecord(
            symbol="F001",
            source="index_proxy",
            source_symbol="000300",
            origin=DataOrigin.PROXY,
            content_hash="abc",
            as_of="2026-07-31",
            quality=DataStatus.PASS,
        )
        assert rec.is_production_eligible is False

    def test_degraded_quality_not_production_eligible(self):
        # 硬约束2：即使直连，质量非 PASS 也不可进生产
        rec = DataLineageRecord(
            symbol="510300",
            source="local_csv",
            source_symbol="510300",
            origin=DataOrigin.DIRECT,
            content_hash="abc",
            as_of="2026-07-31",
            quality=DataStatus.DEGRADED,
        )
        assert rec.is_production_eligible is False

    def test_synthetic_and_derived_blocked(self):
        for origin in (DataOrigin.SYNTHETIC, DataOrigin.DERIVED):
            rec = DataLineageRecord(
                symbol="X", source="s", source_symbol="X",
                origin=origin, content_hash="h", as_of="2026-07-31",
            )
            assert rec.is_production_eligible is False


# ── 血缘登记表 ────────────────────────────────────────────────────────
class TestRegistry:
    def _direct(self, symbol="510300", content_hash="abc", quality=DataStatus.PASS):
        return DataLineageRecord(
            symbol=symbol, source="local_csv", source_symbol=symbol,
            origin=DataOrigin.DIRECT, content_hash=content_hash,
            as_of="2026-07-31", quality=quality, row_count=100,
        )

    def test_register_and_get(self):
        reg = DataIdentityRegistry()
        reg.register(self._direct())
        assert reg.get("510300").row_count == 100
        assert reg.is_production_eligible("510300") is True

    def test_idempotent_same_hash(self):
        reg = DataIdentityRegistry()
        reg.register(self._direct(content_hash="same"))
        reg.register(self._direct(content_hash="same"))  # 幂等，不报错
        assert len(reg) == 1

    def test_conflict_different_hash_raises(self):
        reg = DataIdentityRegistry()
        reg.register(self._direct(content_hash="v1"))
        with pytest.raises(DataIdentityError):
            reg.register(self._direct(content_hash="v2"))  # 偷换数据 → 拒绝

    def test_require_production_fail_closed(self):
        reg = DataIdentityRegistry()
        reg.register(self._direct(quality=DataStatus.DEGRADED))
        with pytest.raises(DataIdentityError):
            reg.require_production("510300")

    def test_require_production_unregistered_symbol(self):
        reg = DataIdentityRegistry()
        with pytest.raises(DataIdentityError):
            reg.require_production("NOPE")

    def test_proxy_records_listing_and_manifest(self):
        reg = DataIdentityRegistry()
        reg.register(self._direct())
        reg.register(DataLineageRecord(
            symbol="F001", source="proxy", source_symbol="000300",
            origin=DataOrigin.PROXY, content_hash="p", as_of="2026-07-31",
        ))
        assert len(reg.proxy_records()) == 1
        manifest = reg.to_manifest()
        assert len(manifest) == 2
        assert all("is_production_eligible" in row for row in manifest)
