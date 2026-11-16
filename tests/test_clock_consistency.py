from datetime import datetime

from quant.gov.clock_consistency import check_three_clocks


def test_ok():
    assert check_three_clocks(
        data_ts=datetime(2026, 1, 5),
        decision_ts=datetime(2026, 1, 10),
        execution_ts=datetime(2026, 1, 12),
    ).ok


def test_decision_before_data_fails():
    assert not check_three_clocks(
        data_ts=datetime(2026, 1, 10), decision_ts=datetime(2026, 1, 5)
    ).ok


def test_execution_before_decision_fails():
    assert not check_three_clocks(
        data_ts=datetime(2026, 1, 1),
        decision_ts=datetime(2026, 1, 10),
        execution_ts=datetime(2026, 1, 8),
    ).ok


def test_no_decision_ok():
    assert check_three_clocks(data_ts=datetime(2026, 1, 1), decision_ts=None).ok


def test_no_data_with_decision_fails():
    assert not check_three_clocks(data_ts=None, decision_ts=datetime(2026, 1, 10)).ok
