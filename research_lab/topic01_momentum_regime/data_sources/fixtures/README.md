# Topic-01 数据层 MOCK 夹具（明确非真实行情）

> 这些 CSV / JSON **全部为人工构造的 MOCK 数据**，仅用于离线测试数据层的
> 校验 / 归一化 / 血缘 / 跨源逻辑。**它们不是任何真实市场行情，禁止用于回测。**

列约定（研究域规范 + 溯源元数据）：
`date,open,high,low,close,volume,amount,adjustment,source,retrieved_at,source_asset_id`

| # | 夹具 | 测试意图 |
|---|------|----------|
| 1 | `index/000300_mock.csv` | 正常指数：日期单调唯一、OHLC 合法、QFQ 显式声明。 |
| 2 | `market/510300_mock.csv` | 正常 ETF：结构同 1，作为“健康样本”基线。 |
| 3 | `market/510880_prelisting.csv` | 上市日守卫：`listing_date=2026-01-06`，但含 `2026-01-05`（早于上市）行 → validator 标记 `no_rows_before_listing_date`。 |
| 4 | `market/515000_dupdate.csv` | 重复日期：`2026-01-06` 出现两次 → validator 标记 `date_unique` 失败，状态 REJECTED。 |
| 5 | `market/512660_future.csv` | 未来日期：含 `2026-08-10`（> 2026-08-07）→ validator 标记 `FUTURE_DATE`，状态 REJECTED（fail-closed）。 |
| 6 | `market/515030_adj_qfq.csv` / `market/515030_adj_hfq.csv` | 复权冲突：同日期、不同 `adjustment`（QFQ vs HFQ）且价格不同 → `compare_sources` 标记 `adjustment_conflict=True`。 |
| 7 | `market/518880_primary.csv` / `market/518880_secondary.csv` | 主/次源价格不匹配：约 5% 偏差 → `compare_sources` 在给定阈值时返回 `DATA_SOURCE_MISMATCH`。 |
| 8 | （无文件，见下） | 缺失血缘：请求一个无任何血缘记录的符号（如 `999999`）→ `LocalSnapshotProvider` 抛 `LineageMissingError`。无对应 CSV，仅行为约定。 |
| 9 | `market/510050_hashmismatch.csv` + `lineage_mismatch.json` | 哈希不匹配：CSV 内容正常，但 `lineage_mismatch.json` 中 `sha256` 故意写错 → `LineageManifest.verify_sha256` 返回 False。 |
| 10 | （无文件，见下） | 远程 provider 禁用：`TushareProvider` 默认 `is_enabled()==False`，调用 `get_*` 抛 `NetworkAccessDisabledError`；`AkshareProvider` 同理且不得被提升为 PRIMARY。仅行为约定。 |

## 生成方式（不触网）

```bash
# 方式一：通过主 CLI
python ../fetch_phase15_data.py --build-fixtures

# 方式二：独立入口
python build_fixtures.py
```

生成后会覆盖上述 MOCK 文件；它们始终为人工构造，不含任何真实行情。
