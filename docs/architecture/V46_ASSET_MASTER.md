# 猎豹 V4.6 — 资产主数据设计

## 架构

### 配置文件

| 文件 | 内容 | 模式 |
|------|------|------|
| `config/assets/asset_master.json` | 统一资产目录 | 主数据 |
| `config/assets/fund_etf_relations.json` | 基金-ETF 研究级关系 | 关系映射 |
| `config/risk/risk_clusters.json` | 风险簇定义 | 配置 |

### 加载器

`quant/assets/__init__.py` 中的 `AssetMasterLoader` 负责加载和校验。

### 关系类型

| 类型 | 含义 | 示例 |
|------|------|------|
| `BENCHMARK_RELATION` | ETF 作为基金的基准参考 | 160216 → 518880 |
| `THEMATIC_REFERENCE` | 同一主题的不同表达 | 004813 → 515030 |
| `UNDERLYING_ETF` | ETF联接基金的底层资产 | 005658 → 510300 |
| `RESEARCH_PROXY` | 研究参考替代（非身份替代） | 旧代理映射转换 |
| `NO_DIRECT_RELATION` | 无直接关系 | 默认值 |

### 禁止类型

| 类型 | 原因 |
|------|------|
| `IDENTITY_PROXY` | 基金与ETF身份不可混用 |

### 资产目录 Schema

```json
{
  "symbol": "004813",                    // 6位代码，前导零保留
  "name": "中欧先进制造股票A",            // 名称
  "asset_type": "ACTIVE_FUND",           // ETF / ACTIVE_FUND / ETF_LINKED_FUND
  "exchange": null,                       // ETF必填
  "currency": "CNY",                     // 货币
  "lot_size": 1,                         // 最小交易单位
  "signal_symbol": "004813",             // 必须等于symbol
  "trade_symbol": "004813",              // 必须等于symbol
  "risk_clusters": ["ADVANCED_MANUFACTURING", "SMALL_MID_GROWTH"],
  "strategy_eligible": true,             // 策略候选资格
  "execution_eligible": true,            // 执行资格
  "permission": "OBSERVATION_ONLY"       // 资金权限
}
```

## 基金-ETF 关系表

```json
{
  "fund_symbol": "005658",
  "etf_symbol": "510300",
  "relation_type": "UNDERLYING_ETF",
  "correlation": 0.95,
  "notes": "沪深300ETF联接 → 沪深300ETF"
}
```

所有关系为研究级映射，不替代基金本身身份。
