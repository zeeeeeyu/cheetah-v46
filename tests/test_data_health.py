import pandas as pd

from quant.data_health import DataHealth, classify, worst


def _frame(n=30, start_val=100.0, drift=1.0):
    idx = pd.date_range("2026-01-01", periods=n, freq="D")
    return pd.DataFrame(
        {"date": idx, "close": [start_val + drift * i for i in range(n)], "volume": [1e6] * n}
    )


def test_full_missing():
    assert classify(None) == DataHealth.FULLY_MISSING
    assert classify(pd.DataFrame()) == DataHealth.FULLY_MISSING


def test_normal():
    assert classify(_frame(), as_of="2026-01-31") == DataHealth.NORMAL


def test_stale():
    assert classify(_frame(), as_of="2026-03-01") == DataHealth.STALE


def test_partial_missing_columns():
    f = _frame().drop(columns=["volume"])
    assert classify(f, require_columns=["close", "volume"]) == DataHealth.PARTIALLY_MISSING


def test_partial_completeness():
    f = _frame()
    f.loc[f.index[::2], "close"] = None
    assert classify(f, min_completeness=0.9) == DataHealth.PARTIALLY_MISSING


def test_worst():
    assert worst([DataHealth.NORMAL, DataHealth.STALE]) == DataHealth.STALE
    assert worst([DataHealth.FULLY_MISSING]) == DataHealth.FULLY_MISSING
