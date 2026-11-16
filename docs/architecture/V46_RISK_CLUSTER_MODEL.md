# 猎豹 V4.6 — 风险簇模型设计

## 核心概念

一个资产可以属于多个风险簇。风险簇暴露按资产权重全额计入每个关联簇。
多个资产如果共享相同的风险簇，暴露会叠加。

### 暴露计算规则

```
风险簇暴露 = Σ(该簇所有资产的权重)
```

每个资产的权重全额计入其关联的每个风险簇（不做比例拆分）。

### 风险簇列表

| 簇 ID | 名称 | 上限 | 质量 |
|-------|------|------|------|
| LARGE_CAP_CORE | 大盘核心 | 60% | HIGH |
| SMALL_MID_GROWTH | 中小成长 | 40% | MODERATE |
| DIVIDEND_VALUE | 红利价值 | 40% | HIGH |
| ADVANCED_MANUFACTURING | 高端制造 | 35% | MODERATE |
| MILITARY_DEFENSE | 军工国防 | 30% | MODERATE |
| NEW_ENERGY | 新能源 | 30% | MODERATE |
| SEMICONDUCTOR | 半导体 | 25% | MODERATE |
| CONSUMPTION | 消费 | 40% | ESTIMATED |
| HEALTHCARE | 医疗健康 | 30% | ESTIMATED |
| FINANCIAL | 金融 | 40% | ESTIMATED |
| GOLD_SAFE_HAVEN | 黄金避险 | 20% | HIGH |
| BOND_DEFENSIVE | 债券防御 | 80% | HIGH |
| CROSS_BORDER_EQUITY | 跨境权益 | 20% | ESTIMATED |
| COMMODITY | 大宗商品 | 20% | ESTIMATED |
| CASH_DEFENSIVE | 现金防御 | 100% | HIGH |

### 质量等级

| 等级 | 含义 |
|------|------|
| HIGH | 暴露计算可靠（如宽基指数基金） |
| MODERATE | 基于主题/名称判断（中等可信度） |
| ESTIMATED | 估算值，未穿透持仓 |
| UNKNOWN | 数据不足，需要人工补充 |

## 风险诊断

风险簇分析只做风险诊断，不生成买卖指令。
报告中的 breached 标记仅说明暴露超过建议上限，不代表需要立即调整。
