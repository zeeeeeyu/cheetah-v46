"""研究模型注册 测试。

覆盖：
- 默认关闭 / 默认冻结 / 默认 L0（Fail-Closed）
- 生产门槛：启用 + 冻结 + >=L4 才能进生产
- promote 只升 / demote 只降 / enable / disable(kill-switch)
- Kronos 占位：注册即禁用，predict Fail-Closed
- LearnedMarketModel 接口一致性
"""

from __future__ import annotations

import pytest

from quant.domain.enums import TrustLevel
from quant.research_models import (
    KRONOS_MODEL_ID,
    ForecastRequest,
    KronosModel,
    LearnedMarketModel,
    ModelNotConnectedError,
    ModelRegistry,
    ModelRegistryError,
    default_registry,
)


class TestDefaultClosed:
    def test_new_registration_is_disabled_frozen_l0(self):
        reg = ModelRegistry()
        entry = reg.register("M1", "1.0.0", description="test")
        assert entry.is_enabled is False
        assert entry.is_frozen is True
        assert entry.trust_level == TrustLevel.L0_UNVERIFIED
        assert entry.is_production_eligible is False

    def test_duplicate_registration_rejected(self):
        reg = ModelRegistry()
        reg.register("M1", "1.0.0")
        with pytest.raises(ModelRegistryError):
            reg.register("M1", "1.0.1")

    def test_get_unregistered_raises(self):
        reg = ModelRegistry()
        with pytest.raises(ModelRegistryError):
            reg.get("NOPE")


class TestProductionGate:
    def test_enabled_but_low_trust_not_eligible(self):
        reg = ModelRegistry()
        entry = reg.register("M1", "1.0.0")
        entry = entry.enable(actor="alice")
        entry = entry.promote(TrustLevel.L3_PAPER, actor="alice")
        reg.update(entry)
        assert entry.is_production_eligible is False  # L3 < L4

    def test_l4_frozen_enabled_is_eligible(self):
        reg = ModelRegistry()
        entry = reg.register("M1", "1.0.0")
        entry = entry.promote(TrustLevel.L4_SHADOW, actor="alice", note="影子达标")
        entry = entry.enable(actor="alice")
        reg.update(entry)
        assert entry.is_production_eligible is True
        assert reg.is_production_eligible("M1") is True
        assert len(reg.production_eligible()) == 1

    def test_high_trust_but_disabled_not_eligible(self):
        reg = ModelRegistry()
        entry = reg.register("M1", "1.0.0")
        entry = entry.promote(TrustLevel.L6_PROVEN, actor="alice")
        # 未 enable
        reg.update(entry)
        assert entry.is_production_eligible is False

    def test_require_production_fail_closed(self):
        reg = ModelRegistry()
        reg.register("M1", "1.0.0")
        with pytest.raises(ModelRegistryError):
            reg.require_production("M1")

    def test_kill_switch_disables_eligible_model(self):
        reg = ModelRegistry()
        entry = reg.register("M1", "1.0.0")
        entry = entry.promote(TrustLevel.L5_LIMITED, actor="alice").enable(actor="alice")
        reg.update(entry)
        assert entry.is_production_eligible is True
        entry = entry.disable(actor="risk", reason="异常波动，紧急停用")
        reg.update(entry)
        assert entry.is_production_eligible is False
        assert "异常波动" in entry.disabled_reason


class TestPromoteDemoteGuards:
    def test_promote_cannot_downgrade(self):
        reg = ModelRegistry()
        entry = reg.register("M1", "1.0.0").promote(TrustLevel.L4_SHADOW, actor="a")
        with pytest.raises(ModelRegistryError):
            entry.promote(TrustLevel.L2_BACKTEST, actor="a")

    def test_demote_cannot_upgrade(self):
        reg = ModelRegistry()
        entry = reg.register("M1", "1.0.0").promote(TrustLevel.L4_SHADOW, actor="a")
        with pytest.raises(ModelRegistryError):
            entry.demote(TrustLevel.L6_PROVEN, actor="a")

    def test_audit_trail_accumulates(self):
        reg = ModelRegistry()
        entry = reg.register("M1", "1.0.0")
        entry = entry.promote(TrustLevel.L2_BACKTEST, actor="a", note="回测通过")
        entry = entry.promote(TrustLevel.L4_SHADOW, actor="b", note="影子通过")
        entry = entry.enable(actor="b")
        assert len(entry.audit_trail) >= 4  # registered + 2 promote + enable


class TestKronosPlaceholder:
    def test_default_registry_has_disabled_kronos(self):
        reg = default_registry()
        entry = reg.get(KRONOS_MODEL_ID)
        assert entry.is_enabled is False
        assert entry.is_frozen is True
        assert entry.is_production_eligible is False
        assert len(reg.production_eligible()) == 0

    def test_kronos_predict_fail_closed(self):
        model = KronosModel()
        assert model.model_id == KRONOS_MODEL_ID
        assert model.is_frozen is True
        req = ForecastRequest(request_id="r1", symbols=("510300",), as_of="2026-07-31")
        with pytest.raises(ModelNotConnectedError):
            model.predict(req)

    def test_kronos_satisfies_learned_model_protocol(self):
        model = KronosModel()
        assert isinstance(model, LearnedMarketModel)


class TestSerialization:
    def test_entry_to_dict_and_manifest(self):
        reg = default_registry()
        manifest = reg.to_manifest()
        assert len(manifest) == 1
        row = manifest[0]
        assert row["model_id"] == KRONOS_MODEL_ID
        assert row["is_production_eligible"] is False
        assert row["trust_level_name"] == "L0_UNVERIFIED"

    def test_forecast_request_to_dict(self):
        req = ForecastRequest(
            request_id="r1", symbols=("510300", "510500"), as_of="2026-07-31",
            horizon="SHORT", environment="PAPER",
        )
        data = req.to_dict()
        assert data["symbols"] == ["510300", "510500"]
        assert data["horizon"] == "SHORT"
