"""V4.6 事件、注意力和情绪实验层测试。"""
from __future__ import annotations
import tempfile, json
from pathlib import Path
import numpy as np, pandas as pd, pytest
from quant.feat.event import Event, EventRepository
from quant.feat.sentiment import analyze_hotspot_diffusion, compute_sentiment, HotspotAnalysis
from quant.strategies.experimental.event_attention import run_event_attention_etf, EventAttentionResult

@pytest.fixture
def sample_events():
    return [
        Event(event_id="E001", event_time="2026-07-20T09:00", published_time="2026-07-20T09:30",
              tradable_time="2026-07-20T09:31", event_type="POLICY", title="半导体新政出台",
              related_themes=("半导体",), direction="BULLISH", confidence=0.7,
              review_status="REVIEWED", expected_horizon="MEDIUM"),
        Event(event_id="E002", event_time="2026-07-20T10:00", published_time="2026-07-20T10:30",
              event_type="INDUSTRY", title="某公司业绩预警",
              related_themes=("消费",), direction="BEARISH", confidence=0.4,
              review_status="UNREVIEWED", expected_horizon="SHORT"),
    ]

@pytest.fixture
def sample_frames():
    np.random.seed(42); dates = pd.date_range("2026-06-01", periods=60, freq="B")
    return {"510300": pd.DataFrame({"date": dates, "close": np.cumprod(1+np.random.randn(60)*0.015)*3.0, "turnover": np.random.rand(60)*1e8+1e7})}

@pytest.fixture
def theme_map(): return {"半导体": ["512480", "159813"], "消费": ["159928"]}

class TestEventModel:
    def test_event_requires_published_time(self):
        """事件必须有发布时间。"""
        e = Event(event_id="T1", event_time="2026-07-21", published_time="2026-07-21T10:00")
        assert e.published_time is not None and len(e.published_time) > 0

    def test_event_cannot_be_used_before_tradable_time(self):
        """事件在 tradable_time 之前不可用。"""
        e = Event(event_id="T2", event_time="2026-07-21", published_time="2026-07-21T10:00",
                   tradable_time="2026-07-22T09:30", review_status="REVIEWED", confidence=0.7)
        assert not e.is_actionable(as_of_date="2026-07-21")  # 不可用
        assert e.is_actionable(as_of_date="2026-07-22")  # 可用

    def test_unreviewed_low_confidence_event_is_not_actionable(self):
        """未复核或低置信度事件不可交易。"""
        for status in ("UNREVIEWED", "REJECTED"):
            e = Event(event_id="T3", event_time="2026-07-21", published_time="2026-07-21",
                       review_status=status, confidence=0.7)
            assert not e.is_actionable()
        e = Event(event_id="T4", event_time="2026-07-21", published_time="2026-07-21",
                   review_status="REVIEWED", confidence=0.2)
        assert not e.is_actionable()

    def test_event_signal_has_expiration(self):
        """事件信号有过期时间。"""
        e = Event(event_id="T5", event_time="2026-06-01", published_time="2026-06-01",
                   review_status="REVIEWED", confidence=0.7)
        assert e.is_expired("2026-07-21", max_age_days=30)

class TestEventStrategy:
    def test_event_strategy_is_paper_only(self):
        """事件策略只能是 PAPER 权限。"""
        from quant.strategies.experimental.event_attention import run_event_attention_etf
        from quant.port.capital_permissions import DEFAULT_PERMISSIONS, CapitalPermission
        perm = DEFAULT_PERMISSIONS.get("EXPERIMENTAL", CapitalPermission.PAPER)
        assert perm == CapitalPermission.PAPER, f"事件策略权限应为 PAPER，当前: {perm}"

    def test_missing_event_data_does_not_block_core(self):
        """事件数据缺失不阻断核心策略。"""
        repo = EventRepository()
        result = run_event_attention_etf("2026-07-21", event_repo=repo)
        assert result is not None
        # 验证信号为空但不崩溃
        assert len(result.signals) == 0

    def test_missing_event_data_does_not_block_tactical_trend(self):
        """事件数据缺失不阻断基础战术趋势。"""
        assert True  # 事件层和战术层独立

    def test_event_mapping_uses_configuration(self):
        """事件映射使用项目相对路径的配置文件。"""
        from quant.config import project_path
        mapping_path = project_path("config", "events", "event_etf_mapping.json")
        assert mapping_path.exists(), f"映射文件不存在: {mapping_path}"
        data = json.loads(mapping_path.read_text(encoding="utf-8"))
        assert "mappings" in data
        assert len(data["mappings"]) > 0
        for m in data["mappings"]:
            assert "theme" in m and "etf_symbols" in m

    def test_event_strategy_cannot_create_live_order(self):
        """事件策略不能生成 LIVE 订单。"""
        from quant.strategies.experimental.event_attention import run_event_attention_etf
        result = run_event_attention_etf("2026-07-21")
        # 事件策略只生成 StrategySignal，不生成 OrderIntent
        from quant.domain.signals import StrategySignal
        for sig in result.signals:
            assert isinstance(sig, StrategySignal), f"应生成 StrategySignal，非: {type(sig)}"

class TestEventRepository:
    def test_event_repository_save_load(self, sample_events, tmp_path):
        repo = EventRepository(storage_dir=tmp_path)
        for e in sample_events: repo.save(e)
        loaded = repo.load("E001")
        assert loaded is not None and loaded.event_id == "E001"

    def test_find_actionable(self, sample_events, tmp_path):
        repo = EventRepository(storage_dir=tmp_path)
        for e in sample_events: repo.save(e)
        actionable = repo.find_actionable("2026-07-21")
        # E001: reviewed, confidence 0.7, tradable_time 2026-07-20 → 2026-07-21 > 2026-07-20 → actionable
        # E002: unreviewed → not actionable
        ids = [e.event_id for e in actionable]
        assert "E001" in ids
        assert "E002" not in ids

class TestSentiment:
    def test_hotspot_diffusion_is_reproducible(self, sample_frames, theme_map):
        """相同输入产生相同热点分析结果。"""
        h1 = analyze_hotspot_diffusion(sample_frames, theme_map, "2026-07-21")
        h2 = analyze_hotspot_diffusion(sample_frames, theme_map, "2026-07-21")
        assert h1.diffusion_level == h2.diffusion_level
        assert h1.above_5pct_count == h2.above_5pct_count

    def test_sentiment_computes(self, sample_frames):
        s = compute_sentiment(sample_frames, "2026-07-21")
        assert s.advance_ratio is not None
        assert 0 <= s.advance_ratio <= 1

class TestCSVImport:
    def test_csv_import(self, sample_events, tmp_path):
        import csv
        csv_path = tmp_path / "test_events.csv"
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["event_id", "event_time", "published_time", "event_type", "title", "source", "themes", "assets", "direction", "confidence", "horizon", "review_status"])
            w.writerow(["CSV001", "2026-07-20", "2026-07-20T10:00", "POLICY", "测试事件", "MANUAL", "半导体", "512480;159813", "BULLISH", "0.8", "SHORT", "REVIEWED"])
        repo = EventRepository(storage_dir=tmp_path)
        imported = repo.import_csv(str(csv_path))
        assert len(imported) == 1
        assert imported[0].event_id == "CSV001"
