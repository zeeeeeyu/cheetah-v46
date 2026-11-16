from datetime import datetime, timedelta

from quant.gov.confirmation_budget import ConfirmationBudget


def test_basic_consume():
    b = ConfirmationBudget(window_seconds=100, max_count=2, max_amount=1000.0)
    assert b.can_confirm(500)
    assert b.consume(500)
    assert b.consume(500)
    assert not b.consume(100)  # 次数耗尽
    rc, ra = b.remaining()
    assert rc == 0 and ra == 0


def test_amount_exhaustion():
    b = ConfirmationBudget(window_seconds=100, max_count=10, max_amount=300.0)
    assert b.consume(200)
    assert not b.consume(200)  # 额度仅剩 100
    assert b.consume(100)


def test_window_prunes():
    now = datetime(2026, 1, 1, 0, 0, 0)
    b = ConfirmationBudget(window_seconds=60, max_count=1, max_amount=1000.0)
    assert b.consume(10, now=now)
    assert not b.can_confirm(10, now=now)  # 窗口内次数耗尽
    later = now + timedelta(seconds=61)
    assert b.can_confirm(10, now=later)  # 已滑出窗口
