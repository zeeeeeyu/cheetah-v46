from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .features import downside_volatility
from .models import Regime, RegimeResult


def fund_nav_regime(frame: pd.DataFrame) -> RegimeResult:
    """Three-state regime based only on directly observable fund NAV history.

    ATR/chandelier logic is intentionally unavailable here because an open-end fund NAV series has
    no intraday high/low.  The result is deliberately less granular rather than pseudo-precise.
    """
    nav = pd.to_numeric(frame["nav"], errors="coerce").astype(float)
    if len(nav) < 120:
        return RegimeResult(Regime.NEUTRAL, ["基准基金累计净值历史不足 120 日；不强行判断进攻/防守状态"], {})
    ma120 = float(nav.rolling(120).mean().iloc[-1])
    price = float(nav.iloc[-1])
    r20 = float(nav.iloc[-1] / nav.iloc[-20] - 1.0)
    r60 = float(nav.iloc[-1] / nav.iloc[-60] - 1.0)
    dvol = downside_volatility(nav, 60)
    rolling_max = nav.tail(60).cummax()
    drawdown_60 = float((nav.tail(60) / rolling_max - 1.0).min())
    metrics = {"nav": price, "ma120": ma120, "r20": r20, "r60": r60, "downside_vol_60": dvol, "drawdown_60": drawdown_60}
    if price > ma120 and r60 > 0 and r20 >= 0:
        return RegimeResult(Regime.RISK_ON, ["基准基金累计净值高于 MA120，20/60 日趋势均未转弱"], metrics)
    if price < ma120 and r20 < 0 and r60 < 0:
        return RegimeResult(Regime.RISK_OFF, ["基准基金累计净值低于 MA120，20/60 日趋势均为负"], metrics)
    return RegimeResult(Regime.NEUTRAL, ["场外基金净值趋势未形成一致进攻或防守信号"], metrics)


def fund_hmm_shadow(frame: pd.DataFrame, min_history: int) -> dict[str, Any]:
    if len(frame) < min_history:
        return {"status": "UNAVAILABLE", "reason": f"历史不足：{len(frame)} < {min_history}"}
    try:
        from hmmlearn.hmm import GaussianHMM  # type: ignore
    except ImportError:
        return {"status": "UNAVAILABLE", "reason": "hmmlearn 未安装；规则型净值状态仍可运行"}
    ret = pd.to_numeric(frame["nav"], errors="coerce").pct_change(fill_method=None).dropna()
    vol = ret.rolling(20).std().dropna()
    feature = pd.DataFrame({"ret": ret, "vol": vol}).dropna()
    if len(feature) < min_history - 20:
        return {"status": "UNAVAILABLE", "reason": "HMM 可用净值特征不足"}
    x = feature.to_numpy(dtype=float)
    x = (x - x.mean(axis=0)) / np.where(x.std(axis=0) == 0, 1, x.std(axis=0))
    try:
        model = GaussianHMM(n_components=3, covariance_type="full", n_iter=200, random_state=7)
        model.fit(x)
        prob = model.predict_proba(x)[-1]
        states = model.predict(x)
        means = {state: float(feature.iloc[states == state]["ret"].mean()) for state in range(3)}
        ordered = [key for key, _ in sorted(means.items(), key=lambda item: item[1])]
        labels = {ordered[0]: "RISK_OFF", ordered[1]: "NEUTRAL", ordered[2]: "RISK_ON"}
        current = int(np.argmax(prob))
        return {"status": "OK", "label": labels[current], "probability": float(prob[current]), "state_return_means": means, "note": "场外基金净值 HMM 仅影子记录，不改变交易决策。"}
    except Exception as exc:
        return {"status": "UNAVAILABLE", "reason": f"HMM 训练失败：{exc}"}
