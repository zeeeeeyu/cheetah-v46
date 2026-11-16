# T01 数据集合法性（FROZEN 研究数据集）验证协议

> 用途：规定一个原始快照（raw snapshot）如何成为**可供研究回测消费的、合法的 FROZEN 研究数据集**。
> 本轮（Phase-1.5，无 >=5 年数据）**不产出任何 FROZEN 数据集**，本文件仅为准入门槛的预登记。

## 1. 唯一允许的数据加载器

研究回测**只能**通过 `LocalSnapshotProvider` 读取数据。禁止在研究中直接调用生产实时接口、外部抓取、或任何绕过本地快照的路径。所有被消费数据的来源、截止时间（as_of）、内容指纹都必须在加载时登记。

## 2. 准入闸门（Gate）

一个数据集要成为 FROZEN 研究数据集，必须**同时满足**以下三项：

1. **血缘记录存在（lineage present）**：每个文件/每张表都有对应的 `DataLineageRecord`（来源 channel、source_symbol、origin、content_hash、as_of、quality）。缺血缘即拒绝消费。
2. **校验状态通过**：`validation_status ∈ {FROZEN, OK, VALID}`。其他状态（如 UNKNOWN / QUARANTINED / BLOCKED / DEGRADED）一律不得进入研究回测。
3. **sha256 校验通过**：加载时重新计算内容哈希，必须与血缘记录中的 `content_hash`（sha256）逐字节一致；不一致即 Fail-Closed 拒绝。

三项任一项不满足 → 数据集视为**未冻结（unfrozen）**，不得使用。

## 3. Point-in-Time 失败即关（Fail-Closed）

- 任何一行 `date > as_of` 的数据都**不得使用**。
- 含未来日期的行必须被**显式拒绝**（reject），而不是静默丢弃或截断。
- 派生特征只能基于 `as_of` 及之前的信息计算，禁止引用任何 `as_of` 之后的行（见 `data_loader.window_as_of`）。

## 4. 子周期 / Walk-forward 预注册纪律

- 子周期切分方法与 walk-forward 窗口参数**必须在看到任何回测结果之前**登记（见 `subperiod_protocol.json` / `walk_forward_protocol.json`）。
- **禁止结果导向切段**：不得根据已看到的收益挑出"最好/最差"的时段或 fold 作为正式方案。
- 切分规则一旦注册即冻结；变更须重新走预注册流程。

## 5. 合法的 FROZEN 数据集 vs 原始快照（raw snapshot）

| 维度 | 原始快照（raw snapshot） | FROZEN 研究数据集 |
|---|---|---|
| 血缘记录 | 可有可无，常缺失 | **必须存在且完整** |
| `validation_status` | 可能为 `UNKNOWN` | 必须 ∈ `{FROZEN, OK, VALID}` |
| sha256 | 未必登记 | **必须登记并通过校验** |
| `adjustment` | 可为 `UNKNOWN` | 必须明确（前复权/后复权/不复权之一） |
| 可否用于研究回测 | 否 | 是 |

**关键红线**：原始快照即使 `adjustment = UNKNOWN`，也**不得被静默升级（silently upgrade）为 FROZEN**。未经验证闸门（§2）与 PIT 校验（§3）的快照，永远只是原始快照，不得冒充冻结数据集进入回测。

## 6. 本轮声明

**本轮（Phase-1.5）不产出任何 FROZEN 数据集。** 当前样本仅约 133 个交易日（2025-12-15~2026-07-03），且为代理/重建数据，正在等待外部 >=5 年数据到位。在拿到合规外部数据、完成血缘登记、通过校验闸门与 PIT 校验之前，任何研究回测均不得使用未冻结数据。
