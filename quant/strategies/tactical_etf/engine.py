"""V4.6 战术 ETF 主引擎。

聚合三个子策略，输出 StrategyDecision。
不直接修改账户持仓。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from quant.config import load_json
from quant.data_health import DataHealth, classify as classify_health, worst as worst_health
from quant.datahub import DataRepository, quality_gate, staleness_calendar_days
from quant.domain.enums import Environment as DomainEnv, MarketRegime
from quant.domain.signals import StrategyDecision as DomainStrategyDecision
from quant.models import Regime
from quant.regime import core_regime
from quant.regimes.state_synthesis import SynthesizedState, synthesize
from quant.sentinel.levels import SentinelLevel
from quant.universe import Instrument, validate_catalog

from .eligibility import (
    INITIAL_RESEARCH_UNIVERSE, classify_universe, EligibilityCriteria,
)
from .trend import (
    run_tactical_trend, run_tactical_breakout, run_tactical_mean_reversion,
    TacticalSubStrategyResult,
)
from .ranking import RankedETF


@dataclass
class TacticalETFResult:
    """战术 ETF 引擎完整输出。"""
    as_of_date: str
    regime_label: str
    trend_result: TacticalSubStrategyResult | None = None
    breakout_result: TacticalSubStrategyResult | None = None
    meanrev_result: TacticalSubStrategyResult | None = None
    selected_etfs: list[str] = field(default_factory=list)
    target_weights: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    # ── c50/c52 接线新增字段（只增不改，保持既有消费方兼容）──────────────
    data_health: dict[str, str] = field(default_factory=dict)
    """逐标的数据健康四档（c50）。键为 symbol，值为 DataHealth.value。"""
    worst_data_health: str = DataHealth.NORMAL.value
    """本次运行所有标的中最差的一档（c50 聚合）。"""
    data_as_of: str | None = None
    """本次实际使用的最新数据日期（数据时钟，供 run_daily 做三时钟校验 / c51）。"""
    quarantined_symbols: list[str] = field(default_factory=list)
    """因数据健康不达标被隔离、不参与配权的标的（c50）。"""
    synthesized_state: dict[str, Any] = field(default_factory=dict)
    """快慢状态合成结果（c52）：{fast, slow, severity, basis, is_risk_off}。"""

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of_date": self.as_of_date,
            "regime_label": self.regime_label,
            "selected_etfs": list(self.selected_etfs),
            "target_weights": dict(self.target_weights),
            "reasons": list(self.reasons),
            "data_health": dict(self.data_health),
            "worst_data_health": self.worst_data_health,
            "data_as_of": self.data_as_of,
            "quarantined_symbols": list(self.quarantined_symbols),
            "synthesized_state": dict(self.synthesized_state),
        }


# ── c50：数据健康分级接入生产链 ────────────────────────────────────────

def assess_data_health(
    frames: dict[str, pd.DataFrame],
    settings: dict[str, Any],
    as_of: pd.Timestamp,
) -> dict[str, DataHealth]:
    """对每只标的做四档健康分类（c50 / REQ-DATA-03）。

    此前 ETF 分支只有布尔式「有 / 没有」判断：``repository.load`` 失败写入空
    DataFrame，其余一律视为可用。后果是**陈旧数据被当正常数据喂给策略**——
    基金分支早有两级隔离，ETF 分支一直没跟上。

    新鲜度阈值刻意复用 ``data.max_staleness_trading_days``，与
    ``resolve_market_regime`` / ``quality_gate`` 保持同一口径，避免再长出
    第三套陈旧度标准。必需列复用 ``data.require_ohlcv`` 开关。
    """
    max_staleness = int(settings["data"]["max_staleness_trading_days"])
    require_columns = (
        ["date", "open", "high", "low", "close", "volume"]
        if bool(settings["data"].get("require_ohlcv", True))
        else ["date", "close"]
    )
    return {
        symbol: classify_health(
            frame,
            as_of=as_of,
            freshness_window_days=max_staleness,
            require_columns=require_columns,
        )
        for symbol, frame in frames.items()
    }


def _latest_data_date(frames: dict[str, pd.DataFrame]) -> str | None:
    """本次实际使用的最新数据日期，即**数据时钟**（供 c51 三时钟校验）。

    取所有标的中最新的一根 bar 的日期。全部为空则返回 ``None``，此时
    ``check_three_clocks`` 会以「有决策但无数据时钟」判 Fail-Closed。
    """
    latest: pd.Timestamp | None = None
    for frame in frames.values():
        if frame is None or frame.empty or "date" not in getattr(frame, "columns", []):
            continue
        try:
            candidate = pd.to_datetime(frame["date"]).max()
        except Exception:  # noqa: BLE001 - 脏数据不应拖垮时钟推导
            continue
        if pd.isna(candidate):
            continue
        if latest is None or candidate > latest:
            latest = candidate
    return str(latest.date()) if latest is not None else None


def _sentinel_from_health(health: DataHealth) -> SentinelLevel:
    """把数据健康四档映射到哨兵等级（慢轴输入）。

    映射刻意保守：``FULLY_MISSING`` → ``DATA_ERROR``（哨兵看不清市场），
    ``PARTIALLY_MISSING`` → ``RED``，``STALE`` → ``ORANGE``（已达 is_risk_off 门槛）。
    """
    return {
        DataHealth.NORMAL: SentinelLevel.GREEN,
        DataHealth.STALE: SentinelLevel.ORANGE,
        DataHealth.PARTIALLY_MISSING: SentinelLevel.RED,
        DataHealth.FULLY_MISSING: SentinelLevel.DATA_ERROR,
    }[health]


_LEGACY_TO_DOMAIN_REGIME = {
    Regime.RISK_ON: MarketRegime.TREND_RISK_ON,
    Regime.NEUTRAL: MarketRegime.RANGE_NEUTRAL,
    Regime.RISK_OFF: MarketRegime.STRESS_RISK_OFF,
}


def synthesize_production_state(
    regime: Regime,
    benchmark_health: DataHealth,
) -> SynthesizedState:
    """把快轴（Regime）与慢轴（由基准数据健康导出的 SentinelLevel）合成（c52）。

    此前 ``synthesize()`` 生产零调用方，两轴在生产中各自为政。此处是它的第一个
    真实调用点：合成结果一旦 ``is_risk_off``，引擎必须强制降到 ``Regime.RISK_OFF``，
    「取更保守者」才算真正影响了下单决策。
    """
    return synthesize(
        _LEGACY_TO_DOMAIN_REGIME.get(regime, MarketRegime.UNKNOWN),
        _sentinel_from_health(benchmark_health),
    )


def resolve_market_regime(
    frames: dict[str, pd.DataFrame],
    settings: dict[str, Any],
    as_of: pd.Timestamp,
) -> tuple[Regime, list[str]]:
    """判定市场状态，数据不可信时 Fail-Closed 转 RISK_OFF。

    这是 GAP-2 / REQ-DATA 的根因修复点。此前的实现把「基准行情缺失」等同于
    `Regime.NEUTRAL`——而 NEUTRAL 是会放行正常仓位的状态，等于数据丢了反而照常
    下单。基准是全局状态判定的唯一依据，它不可信时任何仓位结论都不成立，因此
    只能退到 RISK_OFF，而不是退到「中性」。

    本函数刻意不提供任何绕过参数（bypass / skip / enforce / force）：数据闸门
    一旦可关闭，就等于没有闸门。

    Returns:
        (regime, gate_reasons)。gate_reasons 非空即表示本次是被数据闸门强制
        降级的，调用方必须把原因透出到决策输出中。
    """
    benchmark_symbol = str(settings["benchmark_symbol"])
    benchmark = frames.get(benchmark_symbol)

    # 缺陷 A：缺失 / 空 DataFrame 都算数据不可信。注意 frames 里存在一个空
    # DataFrame 与 key 根本不存在，二者语义相同，必须同样 Fail-Closed。
    if benchmark is None or benchmark.empty:
        return Regime.RISK_OFF, [
            f"基准行情缺失（{benchmark_symbol}）→ Fail-Closed 转 RISK_OFF"
        ]

    # 缺陷 B：max_staleness_trading_days 此前只存在于配置文件，生产路径从不读取。
    # 复用 datahub 的陈旧度原语，保证与 quality_gate 的口径完全一致。
    max_staleness = int(settings["data"]["max_staleness_trading_days"])
    stale_days = staleness_calendar_days(benchmark, as_of)
    if stale_days is None:
        return Regime.RISK_OFF, [
            f"基准行情无法判定数据日期（{benchmark_symbol}）→ Fail-Closed 转 RISK_OFF"
        ]
    if stale_days > max_staleness:
        return Regime.RISK_OFF, [
            f"基准行情陈旧 {stale_days} 天，超过阈值 {max_staleness} 天"
            f"（{benchmark_symbol}，运行日 {as_of.date()}）→ Fail-Closed 转 RISK_OFF"
        ]

    regime_result = core_regime(benchmark, settings["regime"])
    return regime_result.regime, []


def build_eligibility_criteria(settings: dict[str, Any]) -> EligibilityCriteria:
    """把配置真正贯通到全部 7 项合格性判据。

    缺陷 C：此前只传了 min_history_days 与 min_median_turnover_20d 两项，
    其余 5 项永远吃 dataclass 默认值，配置对它们完全失效。

    ⚠ 已知配置分叉（未冻结参数，缺乏回测依据，改动需人工决策——此处只让分叉
    可见，绝不擅自改数）：
      * `config/settings.json:minimum_history_days` = 126，而
        `EligibilityCriteria.min_history_days` 默认 252，实际生效 126。
      * `config/settings.json:data.min_median_turnover_20d` = 1e8（1 亿），而
        dataclass 默认 1e7（1000 万），实际生效 1 亿。

    其余 5 项在 settings 中若无对应键，则沿用 dataclass 默认值，不在此硬编码
    任何新数字。查找顺序：settings["eligibility"] → settings["data"]。
    """
    defaults = EligibilityCriteria()
    sources = (
        dict(settings.get("eligibility") or {}),
        dict(settings.get("data") or {}),
    )

    def pick(key: str, fallback: float) -> float:
        for source in sources:
            if source.get(key) is not None:
                return float(source[key])
        return fallback

    return EligibilityCriteria(
        min_history_days=int(settings["minimum_history_days"]),
        min_median_turnover_20d=float(settings["data"]["min_median_turnover_20d"]),
        min_fund_size=pick("min_fund_size", defaults.min_fund_size),
        max_bid_ask_spread=pick("max_bid_ask_spread", defaults.max_bid_ask_spread),
        max_premium_discount=pick("max_premium_discount", defaults.max_premium_discount),
        max_lot_amount=pick("max_lot_amount", defaults.max_lot_amount),
        min_data_completeness=pick("min_data_completeness", defaults.min_data_completeness),
    )


def run_tactical_etf(
    as_of_date: str | None = None,
    pool: list[str] | None = None,
    account_mode: str = "MICRO",
) -> TacticalETFResult:
    """执行完整战术 ETF 策略流程。"""
    settings = load_json("config/settings.json")
    raw_instruments = load_json("config/instruments.json")
    instruments = [Instrument.from_dict(item) for item in raw_instruments]
    validate_catalog(instruments)

    # 1. Load data
    as_of = pd.Timestamp(as_of_date).normalize() if as_of_date else pd.Timestamp.today().normalize()
    repository = DataRepository()
    frames: dict[str, pd.DataFrame] = {}
    for inst in instruments:
        try:
            frame = repository.load(inst.symbol)
            frame = frame[frame["date"] <= as_of].copy()
            frames[inst.symbol] = frame
        except (FileNotFoundError, ValueError):
            frames[inst.symbol] = pd.DataFrame()

    # 1b. 数据健康四档分级（c50）——在任何决策之前完成，结果贯穿后续所有步骤。
    health_map = assess_data_health(frames, settings, as_of)
    benchmark_symbol = str(settings["benchmark_symbol"])
    benchmark_health = health_map.get(benchmark_symbol, DataHealth.FULLY_MISSING)
    aggregate_health = worst_health(health_map.values()) if health_map else DataHealth.FULLY_MISSING
    data_as_of = _latest_data_date(frames)

    # 2. Market regime（数据闸门 Fail-Closed，见 resolve_market_regime）
    current_regime, gate_reasons = resolve_market_regime(frames, settings, as_of)

    # 2b. 快慢状态合成（c52）——取更保守者，合成结果可反向压制快轴。
    state = synthesize_production_state(current_regime, benchmark_health)
    if state.is_risk_off and current_regime is not Regime.RISK_OFF:
        gate_reasons.append(
            f"快慢状态合成取更保守者（快轴 {state.regime.value} / 慢轴 {state.sentinel.name}，"
            f"依据 {state.basis}）→ 强制降为 RISK_OFF"
        )
        current_regime = Regime.RISK_OFF

    # 3. Classify universe (use research pool or configured instruments)
    research_pool = {inst.symbol: inst.name for inst in instruments} if pool is None else {sym: sym for sym in pool}
    criteria = build_eligibility_criteria(settings)
    research_list, eligible_list, live_list = classify_universe(
        research_pool, frames, criteria, account_mode,
    )

    # 3b. 数据健康隔离（c50）——非 NORMAL 的标的一律不得进入配权池。
    # 这是本次接线真正改变生产行为的地方：此前 STALE / PARTIALLY_MISSING 的标的
    # 会被当作正常数据参与打分与配权。
    quarantined = sorted(
        symbol for symbol, h in health_map.items() if h is not DataHealth.NORMAL
    )
    if quarantined:
        blocked = set(quarantined)
        # 三层全部过滤：run_tactical_trend 的入参是
        # ``live_list or eligible_list or research_list``，只过滤前两层会在
        # 两层都空时经 research_list 把被隔离标的重新放回来。
        research_list = [s for s in research_list if s not in blocked]
        eligible_list = [s for s in eligible_list if s not in blocked]
        live_list = [s for s in live_list if s not in blocked]
        gate_reasons.append(
            f"数据健康隔离：{len(quarantined)} 只标的非 NORMAL 被移出配权池"
            f"（最差档 {aggregate_health.value}）"
        )

    # 4. Run TACTICAL_TREND (primary)
    trend_result = run_tactical_trend(
        frames, live_list or eligible_list or research_list,
        settings=settings.get("signals", {}),
        regime=current_regime,
        max_positions=int(settings["portfolio"]["max_selected_assets"]),
        max_single_weight=float(settings["portfolio"]["max_single_weight"]),
        cash_symbol=str(settings["portfolio"]["cash_symbol"]),
    )

    # 5. Run BREAKOUT and MEAN_REVERSION (interfaces only)
    breakout_result = run_tactical_breakout(frames, live_list, regime=current_regime)
    meanrev_result = run_tactical_mean_reversion(frames, live_list, regime=current_regime)

    # 6. Aggregate
    target_weights = trend_result.target_weights
    # 数据闸门原因排在最前：读报告的人必须第一眼就看到本次是被数据降级的，
    # 而不是把 RISK_OFF 误读为市场本身转弱。
    reasons = [
        *gate_reasons,
        f"市场状态: {current_regime.value}",
        f"研究池: {len(research_list)}, 合格池: {len(eligible_list)}, 实盘池: {len(live_list)}",
        f"趋势策略: {trend_result.reason}",
    ]
    if trend_result.ranked_etfs:
        top5 = [f"{r.symbol}({r.score:.2f})" for r in trend_result.ranked_etfs[:5]]
        reasons.append(f"Top5 排名: {', '.join(top5)}")

    return TacticalETFResult(
        as_of_date=str(as_of.date()),
        regime_label=current_regime.value,
        trend_result=trend_result,
        breakout_result=breakout_result,
        meanrev_result=meanrev_result,
        selected_etfs=trend_result.selected_symbols,
        target_weights=target_weights,
        reasons=reasons,
        data_health={s: h.value for s, h in health_map.items()},
        worst_data_health=aggregate_health.value,
        data_as_of=data_as_of,
        quarantined_symbols=quarantined,
        synthesized_state={
            "fast": state.regime.value,
            "slow": state.sentinel.name,
            "severity": state.severity,
            "basis": state.basis,
            "is_risk_off": state.is_risk_off,
        },
    )
