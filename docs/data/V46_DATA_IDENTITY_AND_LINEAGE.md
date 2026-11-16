# 数据身份治理与血缘（V46_DATA_IDENTITY_AND_LINEAGE）

> 模块：`quant/data_identity/`　测试：`tests/test_data_identity.py`（18）

量化系统最隐蔽的风险之一是**代理数据伪装**：用"另一个标的"（如指数）的数据
冒充"这个标的"（如场外基金/ETF）的直连数据，导致回测与实盘系统性失真。
本模块把"每一条数据的身份、来源、截止、哈希、质量、直连或代理"登记为
一等治理对象，并对代理施加**机器约束**。

## 1. 资产主数据（Asset Master）

- 来源：`config/assets/asset_master.json`（当前 13 标的）。
- `AssetRecord` 关键字段：`symbol / trade_symbol / signal_symbol / asset_type /
  permission / strategy_eligible / execution_eligible`。
- **代理信号识别**：`uses_proxy_signal = (signal_symbol != trade_symbol)`。
  即"用别的标的产生信号、却在本标的交易"，显式登记备查。

## 2. 数据来源性质（DataOrigin）

| 值 | 含义 | 可进生产 |
|---|---|---|
| `DIRECT` | 直连标的自身真实行情/净值 | 可能（需合格） |
| `PROXY` | 代理数据（别的标的替代） | **永不** |
| `DERIVED` | 由其他数据派生计算 | **永不** |
| `SYNTHETIC` | 人工合成/模拟 | **永不** |

## 3. 代理隔离三条硬约束（Fail-Closed）

1. **直连即真身**：`origin == DIRECT` 时，`source_symbol` 必须等于 `symbol`，
   否则 `DataLineageRecord.__post_init__` 直接抛 `DataIdentityError`——
   代理数据不得伪装成直连数据。
2. **生产只吃直连且合格**：`is_production_eligible` 需
   `origin == DIRECT` 且 `quality == PASS` 且 `source_symbol == symbol`。
3. **代理必须显式**：PROXY/DERIVED/SYNTHETIC 允许登记（供研究），但
   `is_production_eligible` 恒为 False，从物理上无法进入真实决策路径。

## 4. 血缘登记表（DataIdentityRegistry）

- `register`：同一 symbol 重复登记时，指纹一致视为幂等；指纹冲突（同标的
  却不同内容）抛错——杜绝"偷偷换数据"。
- `require_production(symbol)`：生产守卫，拿不到"直连且合格"即 Fail-Closed。
- `content_fingerprint(payload)`：为 bytes/str/DataFrame 计算稳定 sha256 指纹。
- `to_manifest()`：导出血缘清单，可直接写入运行快照 `manifest.json`。

```python
from quant.data_identity import (
    DataIdentityRegistry, DataLineageRecord, DataOrigin, content_fingerprint,
)

reg = DataIdentityRegistry()
reg.register(DataLineageRecord(
    symbol="510300", source="akshare_direct_etf", source_symbol="510300",
    origin=DataOrigin.DIRECT, content_hash=content_fingerprint(df),
    as_of="2026-07-31",
))
reg.require_production("510300")  # 不合格即抛错
```

## 5. 与既有 `datahub.quality_gate` 的关系

`datahub.quality_gate` 已在数据加载时校验 `source_symbol == symbol`。本模块把
该原则**上升为一等治理层**：不仅在加载时检查，还把每条数据的身份持久登记进
运行快照，形成可审计的血缘账本，并对代理数据施加不可绕过的生产禁入约束。
