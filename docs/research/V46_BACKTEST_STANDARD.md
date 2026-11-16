# 猎豹 V4.6 — 回测标准

## 统一 BacktestResult

| 字段 | 说明 |
|------|------|
| strategy_id | 策略标识 |
| strategy_version | 策略版本 |
| code_version | 代码版本（V4.6） |
| dataset_version | 数据集版本 |
| config_hash | 配置哈希 |
| period_start/end | 回测区间 |
| total_return | 总收益 |
| annual_return | 年化收益 |
| max_drawdown | 最大回撤（负值） |
| drawdown_duration_days | 回撤持续天数 |
| sharpe / sortino / calmar | 风险调整收益 |
| total_turnover / total_cost / trade_count | 交易统计 |
| regime_metrics | 市场状态分解 |

## ETF 回测规则

- T 日收盘信号 → T+1 开盘执行
- 数量按 100 份取整
- 最低佣金 5 元
- 滑点 + 买卖价差
- 信号过期检查

## 基金回测规则

- T 日信号 → T+2 日以当日净值定价（未知价）
- 申购/赎回确认延迟
- 持有期费率
- 暂停申购/限购检查

## 禁止行为

- ❌ 使用随机拆分时间序列
- ❌ 使用未来净值
- ❌ 省略成本
- ❌ 只展示最优参数
- ❌ 自动将最佳回测投入实盘
- ❌ 修改真实账户状态
