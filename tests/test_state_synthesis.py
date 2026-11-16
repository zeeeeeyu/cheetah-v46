from quant.domain.enums import MarketRegime
from quant.sentinel.levels import SentinelLevel
from quant.regimes.state_synthesis import synthesize


def test_sentinel_overrides_regime():
    s = synthesize(MarketRegime.TREND_RISK_ON, SentinelLevel.ORANGE)
    assert s.basis == "sentinel"
    assert s.is_risk_off


def test_data_error_pulls_risk_off():
    s = synthesize(MarketRegime.TREND_RISK_ON, SentinelLevel.DATA_ERROR)
    assert s.is_risk_off
    assert s.severity == 4


def test_regime_can_be_stricter():
    s = synthesize(MarketRegime.STRESS_RISK_OFF, SentinelLevel.GREEN)
    assert s.basis == "regime"
    assert s.is_risk_off


def test_both_green():
    s = synthesize(MarketRegime.RANGE_NEUTRAL, SentinelLevel.GREEN)
    assert not s.is_risk_off
