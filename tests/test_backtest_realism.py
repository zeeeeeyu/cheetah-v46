import math

import numpy as np
import pandas as pd

from quant.research.backtest import backtest_unknown_price


def _series(prices, start="2026-01-01"):
    idx = pd.date_range(start, periods=len(prices), freq="D")
    return pd.Series(prices, index=idx)


def test_unknown_price_uses_future_price_not_signal_day():
    # 信号日当天价=100，次日价=200。fee=0 时，信号日权益 = price[0]/price[1] = 0.5，
    # 精确证明成交用的是「次日价(200)」而非「信号日价(100)」（否则权益应为 1.0）。
    price = _series([100.0, 200.0] + [200.0] * 8)
    signals = _series([0.0] * 10)
    signals.iloc[0] = 1.0
    eq = backtest_unknown_price(signals, price, fee_rate=0.0, confirmation_lag=1, initial_capital=1.0)
    assert abs(eq.iloc[0] - 0.5) < 1e-9


def test_fee_reduces_return():
    price = _series(list(np.linspace(100, 110, 10)))
    signals = _series([0.0] * 10)
    signals.iloc[0] = 1.0
    eq_no_fee = backtest_unknown_price(signals, price, fee_rate=0.0, confirmation_lag=1)
    eq_fee = backtest_unknown_price(signals, price, fee_rate=0.01, confirmation_lag=1)
    assert eq_fee.iloc[-1] < eq_no_fee.iloc[-1]


def test_confirmation_lag_delays_entry():
    # 第0天价100，第1天价100，第2天价200
    price = _series([100.0, 100.0, 200.0] + [200.0] * 7)
    signals = _series([0.0] * 10)
    signals.iloc[0] = 1.0
    eq_lag1 = backtest_unknown_price(signals, price, confirmation_lag=1, fee_rate=0.0, initial_capital=1.0)
    eq_lag2 = backtest_unknown_price(signals, price, confirmation_lag=2, fee_rate=0.0, initial_capital=1.0)
    # lag1 以第1天价(100)成交 → 信号日权益=100/100=1.0；lag2 以第2天价(200)成交 → 100/200=0.5
    assert abs(eq_lag1.iloc[0] - 1.0) < 1e-9
    assert abs(eq_lag2.iloc[0] - 0.5) < 1e-9
    # 延迟越大，成交价越高，终值越低
    assert eq_lag2.iloc[-1] < eq_lag1.iloc[-1]
