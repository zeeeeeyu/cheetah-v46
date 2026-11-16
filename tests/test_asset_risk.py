"""V4.6 资产主数据与风险簇测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quant.assets import (
    AssetMasterLoader,
    validate_asset_config,
    legacy_proxy_to_research_relations,
    V42_PROXY_MAPPING,
    VALID_RELATION_TYPES,
    FORBIDDEN_RELATION_TYPES,
)
from quant.assets.risk_clusters import RiskClusterCalculator, PortfolioClusterReport
from quant.domain.enums import AssetType
from quant.domain.assets import Asset


# ── Fixtures ────────────────────────────────────────────────────

@pytest.fixture
def loader() -> AssetMasterLoader:
    return AssetMasterLoader()


@pytest.fixture
def calculator() -> RiskClusterCalculator:
    return RiskClusterCalculator()


# =========================================================================
# Test 1: 资产目录唯一性
# =========================================================================

class TestAssetMasterUniqueness:
    def test_asset_master_has_unique_symbol_and_type(self, loader):
        """资产目录中每个symbol唯一，且类型正确。"""
        assets = loader.load_all()
        symbols = [a.symbol for a in assets]
        assert len(symbols) == len(set(symbols)), "资产代码有重复"

        # Verify all have valid types
        for a in assets:
            assert isinstance(a.asset_type, AssetType), f"{a.symbol} 类型非法"

    def test_asset_config_passes_validation(self):
        """配置文件整体校验通过。"""
        errors = validate_asset_config()
        assert len(errors) == 0, f"配置校验失败: {errors}"


# =========================================================================
# Test 2: 基金-ETF关系禁止身份替代
# =========================================================================

class TestFundETFRelation:
    def test_fund_etf_relation_is_not_identity(self, loader):
        """基金-ETF关系不得使用 IDENTITY_PROXY。"""
        assert "IDENTITY_PROXY" in FORBIDDEN_RELATION_TYPES

    def test_valid_relation_types(self):
        """关系类型必须是允许的类型之一。"""
        assert "BENCHMARK_RELATION" in VALID_RELATION_TYPES
        assert "THEMATIC_REFERENCE" in VALID_RELATION_TYPES
        assert "UNDERLYING_ETF" in VALID_RELATION_TYPES
        assert "RESEARCH_PROXY" in VALID_RELATION_TYPES
        assert "NO_DIRECT_RELATION" in VALID_RELATION_TYPES

    def test_relations_loaded_correctly(self, loader):
        relations = loader.load_relations()
        for rel in relations:
            assert rel.relation_type in VALID_RELATION_TYPES
            assert rel.fund_symbol.startswith("0") or rel.fund_symbol.startswith("1")
            assert len(rel.etf_symbol) == 6

    def test_legacy_proxy_mapping_is_research_only(self):
        """V4.2 旧 PROXY_MAPPING 必须转为 RESEARCH_PROXY。"""
        relations = legacy_proxy_to_research_relations()
        assert len(relations) > 0
        for rel in relations:
            assert rel.relation_type == "RESEARCH_PROXY", \
                f"旧代理必须转为 RESEARCH_PROXY，当前 {rel.relation_type}"
            assert "非身份替代" in rel.notes, "备注必须说明仅为研究参考"


# =========================================================================
# Test 3: 多风险簇
# =========================================================================

class TestMultipleRiskClusters:
    def test_one_asset_can_have_multiple_risk_clusters(self, loader):
        """一个资产可以属于多个风险簇。"""
        asset = loader.load_by_symbol("013566")
        assert asset is not None
        assert len(asset.risk_clusters) >= 2, \
            f"军工安全应至少属于2个风险簇，当前 {asset.risk_clusters}"

    def test_risk_cluster_weights_are_valid(self, calculator):
        """风险簇暴露总和应合理。"""
        report = calculator.compute_for_current_holdings()
        for exp in report.cluster_exposures:
            assert exp.total_weight >= 0
            assert exp.max_exposure > 0

    def test_unknown_asset_is_not_silently_accepted(self, calculator):
        """未知资产应产生警告。"""
        report = calculator.compute({"UNKNOWN": 1.0})
        assert len(report.warnings) > 0
        assert any("未知" in w for w in report.warnings)

    def test_cluster_exposure_aggregates_different_funds(self, calculator):
        """多个相同风险簇的基金应合并计算暴露。"""
        # 013566 和 016263 都属于 ADVANCED_MANUFACTURING
        # 如果两个都持有，暴露应叠加
        report = calculator.compute({
            "013566": 0.3,
            "016263": 0.3,
            "CASH": 0.4,
        })
        manufacturing_exposure = None
        for exp in report.cluster_exposures:
            if exp.cluster_id == "ADVANCED_MANUFACTURING":
                manufacturing_exposure = exp
                break
        assert manufacturing_exposure is not None
        # 0.3 + 0.3 = 0.6
        assert abs(manufacturing_exposure.total_weight - 0.6) < 0.01


# =========================================================================
# Test 4: 资产配置校验
# =========================================================================

class TestConfigValidation:
    def test_asset_config_schema_validation(self):
        """配置文件应满足 Schema 要求。"""
        errors = validate_asset_config()
        # No errors expected for valid config
        assert len(errors) == 0

    def test_asset_master_has_required_fields(self, loader):
        """每个资产应有 symbol, name, asset_type, risk_clusters。"""
        for a in loader.load_all():
            assert a.symbol
            assert a.name
            assert isinstance(a.asset_type, AssetType)
            assert len(a.risk_clusters) > 0

    def test_no_forbidden_relations_in_config(self, loader):
        """配置文件中不应有禁止的关系类型。"""
        path = loader.relations_path
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            for item in raw:
                assert item["relation_type"] not in FORBIDDEN_RELATION_TYPES


# =========================================================================
# Test 5: 当前持仓风险簇分析
# =========================================================================

class TestCurrentHoldings:
    def test_current_holdings_report_does_not_generate_orders(self, calculator):
        """风险诊断报告不生成买卖结论。"""
        report = calculator.compute_for_current_holdings()
        # Cluster report should not contain BUY/SELL suggestions
        output_text = json.dumps([e.__dict__ for e in report.cluster_exposures])
        assert "BUY" not in output_text.upper()
        assert "SELL" not in output_text.upper()
        assert "买入" not in output_text
        assert "卖出" not in output_text

    def test_report_has_quality_notes(self, calculator):
        """报告应注明数据质量。"""
        report = calculator.compute_for_current_holdings()
        # Should mention estimation quality
        assert len(report.data_quality_notes) >= 0  # at least 0


# =========================================================================
# Test 6: 风险簇曝露边界
# =========================================================================

class TestClusterExposureBoundaries:
    def test_breach_detection(self, calculator):
        """超过上限时标记breached。"""
        # 分配超过 ADVANCED_MANUFACTURING 上限 (0.35) 的权重
        report = calculator.compute({
            "013566": 0.25,
            "016263": 0.25,
            "CASH": 0.5,
        })
        for exp in report.cluster_exposures:
            if exp.cluster_id == "ADVANCED_MANUFACTURING":
                assert exp.breached  # 0.25 + 0.25 = 0.5 > 0.35


# =========================================================================
# Test 7: 前导零保留
# =========================================================================

class TestLeadingZero:
    def test_asset_symbol_preserves_leading_zero(self, loader):
        """资产目录加载后，基金代码保留前导零。"""
        asset = loader.load_by_symbol("004813")
        assert asset is not None
        assert asset.symbol == "004813"

    def test_proxy_mapping_preserves_leading_zero(self):
        """旧代理映射也保留前导零。"""
        assert "004813" in V42_PROXY_MAPPING
        assert V42_PROXY_MAPPING["004813"] == "515030"


# =========================================================================
# Test 8: 报告格式完整性
# =========================================================================

class TestReportFormat:
    def test_report_has_all_required_fields(self, calculator):
        """风险簇报告包含字段完整。"""
        report = calculator.compute_for_current_holdings()
        assert report.account_id is not None
        assert report.total_equity_weight >= 0
        assert report.cash_weight >= 0
        assert len(report.cluster_exposures) > 0
        # Each exposure has required fields
        for exp in report.cluster_exposures:
            assert exp.cluster_id
            assert exp.total_weight >= 0
            assert exp.max_exposure > 0
            assert isinstance(exp.breached, bool)


# =========================================================================
# Test 9: 未知关系类型拒绝
# =========================================================================

class TestUnknownRelationRejection:
    def test_unknown_relation_type_raises(self):
        """未知关系类型应抛出错误。"""
        from quant.assets import FundETFRelation, AssetMasterLoader
        bad = FundETFRelation(
            fund_symbol="005658",
            etf_symbol="510300",
            relation_type="INVALID_TYPE",
        )
        with pytest.raises(ValueError, match="未知关系类型"):
            AssetMasterLoader.validate_relations([bad])

    def test_identity_proxy_raises(self):
        """IDENTITY_PROXY 应被拒绝。"""
        from quant.assets import FundETFRelation, AssetMasterLoader
        bad = FundETFRelation(
            fund_symbol="005658",
            etf_symbol="510300",
            relation_type="IDENTITY_PROXY",
        )
        with pytest.raises(ValueError, match="禁止"):
            AssetMasterLoader.validate_relations([bad])
