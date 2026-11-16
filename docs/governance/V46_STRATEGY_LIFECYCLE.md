# 猎豹 V4.6 — 策略生命周期管理

## 生命周期

```
IDEA → RESEARCH → BACKTESTED → PAPER → REAL_LIMITED → REAL
                                      → SUSPENDED → RETIRED
```

| 阶段 | 含义 | 资金权限 |
|------|------|----------|
| IDEA | 想法阶段 | — |
| RESEARCH | 研究阶段 | 无 |
| BACKTESTED | 已完成可信回测 | 无 |
| PAPER | 模拟盘 | PAPER |
| REAL_LIMITED | 有限实盘 | REAL_LIMITED |
| REAL | 完整实盘 | REAL |
| SUSPENDED | 暂停 | 禁用 |
| RETIRED | 退役 | 禁用 |

## 模型状态

| 状态 | 含义 | 允许实盘 |
|------|------|----------|
| MODEL_OK | 正常 | ✅ |
| MODEL_WARNING | 警告 | ✅ |
| MODEL_REVIEW_REQUIRED | 需要人工复核 | ❌ |
| MODEL_SUSPENDED | 暂停 | ❌ |

## 晋升规则

- 策略不能自我晋升到实盘
- MODEL_REVIEW_REQUIRED 阻止向实盘晋升
- 每个阶段必须经过人工确认
