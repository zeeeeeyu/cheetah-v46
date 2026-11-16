# 市场哨兵与事件系统（V46_SCHEDULER_AND_SENTINEL）

> 模块：`quant/sentinel/`　测试：`tests/test_market_sentinel.py`（19）

市场哨兵是系统的"防御性风险闸门"：它把多路市场指标与已确认事件汇总成一个
**四级风险等级**，供上层决定"是否允许新开仓 / 是否进入 Risk-Off"。

## 1. 四级等级（IntEnum，防御优先）

| 等级 | 值 | 含义 | 允许新开仓 | Risk-Off |
|---|---|---|---|---|
| `GREEN` | 0 | 正常 | ✅ | ❌ |
| `YELLOW` | 1 | 警戒 | ✅ | ❌ |
| `ORANGE` | 2 | 高风险 | ❌ | ✅ |
| `RED` | 3 | 极端/危机 | ❌ | ✅ |

- `allows_new_entries`：等级 ≤ YELLOW。
- `is_risk_off`：等级 ≥ ORANGE。

## 2. 非对称迟滞（核心）

风险的代价不对称：**漏防的损失 >> 误防的机会成本**，因此：

- **升级即时**：`raw > current` 时立即跳到 raw（哪怕从 GREEN 一步到 RED），
  并重置降级计数——风险快进。
- **降级慢确认**：`raw < current` 时不立刻降，需连续
  `deescalation_confirmations`（默认 2）次确认，且**每次只降一级**——解除慢确认。
- **噪声免疫**：`raw == current`（压力持续）时重置降级计数，打断降级流程——
  单日噪声不翻级。

```python
from quant.sentinel import MarketSentinel, SentinelReading, SentinelLevel

s = MarketSentinel(initial=SentinelLevel.GREEN, deescalation_confirmations=2)
s.evaluate(SentinelReading(as_of="2026-08-01", drawdown=0.20))  # → RED（即时）
s.evaluate(SentinelReading(as_of="2026-08-02", drawdown=0.0))   # 仍 RED（第1次确认）
s.evaluate(SentinelReading(as_of="2026-08-03", drawdown=0.0))   # → ORANGE（每次只降一级）
```

## 3. 指标输入（SentinelReading）

回撤、波动率分位、宽度（MA50 上方占比）、下行波动、跨资产压力。各指标有
防御性阈值（如回撤 YELLOW 0.05 / ORANGE 0.10 / RED 0.18）；`compute_raw_level`
在"指标隐含等级"与"事件隐含等级"间取**最高**（防御优先）。

## 4. 事件系统（唯一键 + 去重 + 幂等）

- `SentinelEvent`：`unique_key` 优先取 `event_id`，否则由
  `event_type|event_time|source|title` 派生 sha1，保证同一事件稳定同键。
- `SentinelEventLog.ingest`：返回是否首次摄取（重复返回 False），天然去重。
- **仅已确认事件生效**：`review_status == "CONFIRMED"` 才有 `implied_level`；
  未确认事件（谣言/噪声）返回 0，不强制升级，避免被消息面误触发。
- 已确认黑天鹅（`BLACK_SWAN/SYSTEMIC_SHOCK/CIRCUIT_BREAKER` 或 `CRITICAL`）
  强制 RED；重复摄取幂等稳定，不会反复翻级。

## 5. 与 `quant.feat.event.Event` 的边界

二者职责分离、互不依赖：
- `feat.Event`：服务**事件驱动的 ETF 主题研究**（进攻性、找机会）。
- `SentinelEvent`：服务**防御性风险等级判定**（防守性、控风险）。
