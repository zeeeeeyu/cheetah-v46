# 研究模型治理（V46_RESEARCH_MODEL_GOVERNANCE）

> 模块：`quant/research_models/`　测试：`tests/test_research_models.py`（16）

学习型模型（如 Kronos）是**研究资产**，不是**生产决策器**。本模块为它们提供
唯一受控落点：一个默认关闭、逐级门禁的注册表，保证任何模型在经过完整治理
之前绝无可能影响真实决策。

## 1. 可信级别 L0–L6（复用 `domain.enums.TrustLevel`）

| 级别 | 含义 | 生产可消费 |
|---|---|---|
| L0_UNVERIFIED | 原始/未验证 | ❌ |
| L1_SANITY | 通过基本合理性检查 | ❌ |
| L2_BACKTEST | 通过离线回测 | ❌ |
| L3_PAPER | 通过纸面验证 | ❌ |
| **L4_SHADOW** | 影子跟踪（并行观察不出资金） | ✅（门槛） |
| L5_LIMITED | 小额实盘验证 | ✅ |
| L6_PROVEN | 长期验证可信 | ✅ |

生产门槛：`is_production_eligible = 启用(enabled) 且 冻结(frozen) 且 级别 ≥ L4`。

## 2. 受控接口

- `ForecastRequest`：一次预测请求（symbols/as_of/horizon/environment），
  只描述"预测什么"，不含仓位含义。
- `LearnedMarketModel`（Protocol）：暴露 `model_id / model_version / is_frozen`
  与 `predict(request) -> ForecastBundle`。
- `ForecastBundle`（在 `domain/forecasts.py`）：只读预测输出，携带 TrustLevel，
  必须经策略层显式转换才能变成目标组合，且转换受级别门禁约束。

## 3. 注册表治理（ModelRegistry）

- **默认关闭**：新注册的模型 `is_enabled=False`、`trust_level=L0`、`is_frozen=True`。
- `promote` 只升级、`demote` 只降级，`enable/disable`（kill-switch）随时可用。
- 每次变更产生新的不可变 `ModelRegistryEntry` 并追加 `audit_trail`，全程留痕。
- `require_production(model_id)`：拿不到"启用+冻结+≥L4"即 Fail-Closed。

## 4. Kronos 十条冻结原则

1. **默认冻结**：Kronos 在注册表中以 `frozen=True` 存在，版本 `0.0.0-frozen`。
2. **默认禁用**：`enabled=False`，`is_production_eligible` 恒为 False。
3. **默认 L0**：未经治理提升前处于最低可信级别。
4. **未接入**：`KronosModel.predict` 一律抛 `ModelNotConnectedError`（Fail-Closed）。
5. **逐级晋升**：接入需依次通过 L2 回测 → L3 纸面 → L4 影子 → L5 小额 → L6 长期。
6. **显式启用**：达到 ≥L4 后仍需在注册表中显式 `enable` 才能进生产。
7. **只读输出**：任何输出只能是 `ForecastBundle`，不得携带仓位/订单语义。
8. **策略层转换**：预测不得直接变持仓，必须经策略层显式、受门禁的转换。
9. **可一键停用**：任何异常可用 `disable`（kill-switch）立即摘除生产资格。
10. **全程审计**：注册、晋升、启停均写入 `audit_trail`，可复盘可追责。

```python
from quant.research_models import default_registry, KRONOS_MODEL_ID

reg = default_registry()
reg.get(KRONOS_MODEL_ID).is_production_eligible   # → False（冻结+禁用+L0）
reg.production_eligible()                          # → ()（当前无任何模型可生产）
```
