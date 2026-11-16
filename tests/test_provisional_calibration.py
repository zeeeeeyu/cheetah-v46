from quant.provisional_calibration import (
    is_calibration_gated,
    propose_calibration,
)


def test_propose_maintains_provisional():
    props = propose_calibration()
    assert len(props) == 4
    assert all(p.confidence == "LOW" for p in props)
    assert all(p.candidate == p.current for p in props)


def test_propose_with_candidate_still_low():
    props = propose_calibration({"minimum_history_days": 252})
    mh = next(p for p in props if p.key == "minimum_history_days")
    assert mh.candidate == 252
    assert mh.confidence == "LOW"


def test_gate_ready():
    assert is_calibration_gated()
