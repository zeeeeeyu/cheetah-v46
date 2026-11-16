# 猎豹 V4.6 — Walk-forward 标准

## 窗口划分

```
训练集 (252日) → 验证集 (0日) → 测试集 (126日)
              → 步进 126 日 →
```

每个窗口：
- 训练集：参数优化
- 测试集：参数冻结验证
- 测试窗口参数不得在测试期间调整

## 结果记录

每个 fold 保存：
- train_start, train_end
- test_start, test_end
- train_result（含 metrics）
- test_result（含 metrics）
- parameters_frozen（冻结时的参数快照）
- status（PENDING / RUNNING / COMPLETED / FAILED）

## 稳定性评估

- average_test_metric: 所有测试窗口的平均值
- std_test_metric: 所有测试窗口的标准差
- stability_score: 1.0 - std / |mean|，衡量跨窗口稳定性

## 参数实验注册表

- 保存所有实验（包括失败结果）
- 每实验包含：参数、结果、成本假设、代码版本、数据版本
- 不使用随机数伪造结果
