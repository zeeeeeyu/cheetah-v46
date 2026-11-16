from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .features import average_true_range, downside_volatility
from .models import Regime, RegimeResult


def core_regime(frame: pd.DataFrame, config: dict) -> RegimeResult:
    close = frame["close"].astype(float)
    ma120 = float(close.rolling(120).mean().iloc[-1])
    atr = average_true_range(frame, int(config["atr_window"]))
    high20 = float(frame["high"].rolling(20).max().iloc[-1])
    chandelier = high20 - float(config["chandelier_multiple"]) * atr
    price = float(close.iloc[-1])
    r20 = float(close.iloc[-1] / close.iloc[-20] - 1)
    r60 = float(close.iloc[-1] / close.iloc[-60] - 1)
    dvol = downside_volatility(close, 60)

    metrics = {
        "price": price,
        "ma120": ma120,
        "chandelier_exit": float(chandelier),
        "r20": r20,
        "r60": r60,
        "downside_vol_60": dvol,
    }
    if price > ma120 and price > chandelier and r60 > 0:
        return RegimeResult(Regime.RISK_ON, ["价格高于 MA120 与吊灯线，且 60 日趋势为正"], metrics)
    if price < ma120 and price < chandelier and r20 < 0:
        return RegimeResult(Regime.RISK_OFF, ["价格低于 MA120 与吊灯线，且 20 日趋势为负"], metrics)
    return RegimeResult(Regime.NEUTRAL, ["趋势与风险条件未形成一致进攻或防守信号"], metrics)


def hmm_shadow(frame: pd.DataFrame, min_history: int) -> dict[str, Any]:
    """Optional V4.4 shadow model. Never changes production regime by itself."""
    if len(frame) < min_history:
        return {"status": "UNAVAILABLE", "reason": f"历史不足：{len(frame)} < {min_history}"}
    try:
        from hmmlearn.hmm import GaussianHMM  # type: ignore
    except ImportError:
        return {"status": "UNAVAILABLE", "reason": "hmmlearn 未安装；核心规则状态仍可运行"}

    ret = frame["close"].pct_change(fill_method=None).dropna()
    vol = ret.rolling(20).std().dropna()
    feature = pd.DataFrame({"ret": ret, "vol": vol}).dropna()
    if len(feature) < min_history - 20:
        return {"status": "UNAVAILABLE", "reason": "HMM 可用特征样本不足"}
    x = feature.to_numpy(dtype=float)
    x = (x - x.mean(axis=0)) / np.where(x.std(axis=0) == 0, 1, x.std(axis=0))
    try:
        model = GaussianHMM(n_components=3, covariance_type="full", n_iter=200, random_state=7)
        model.fit(x)
        probabilities = model.predict_proba(x)[-1]
        # State ranking uses in-sample average raw return; only a descriptive shadow signal.
        states = model.predict(x)
        state_returns = {state: float(feature.iloc[states == state]["ret"].mean()) for state in range(3)}
        ordered = [key for key, _ in sorted(state_returns.items(), key=lambda item: item[1])]
        labels = {ordered[0]: "RISK_OFF", ordered[1]: "NEUTRAL", ordered[2]: "RISK_ON"}
        current_state = int(np.argmax(probabilities))
        return {
            "status": "OK",
            "label": labels[current_state],
            "probability": float(probabilities[current_state]),
            "state_return_means": state_returns,
            "note": "影子模型，仅用于记录分歧，不直接改变交易决策。",
        }
    except Exception as exc:
        return {"status": "UNAVAILABLE", "reason": f"HMM 训练失败：{exc}"}
