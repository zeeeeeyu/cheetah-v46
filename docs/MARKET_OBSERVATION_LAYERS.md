# V4.6 三层市场观察（Market Observation Layers：L1 事实 / L2 指标 / L3 判断）

> 文档编号：ARCH-MKTOBS-001
> 版本：v1.0
> 生成日期：2026-08-01
> 对应需求：补充约束 Prompt 第四节「市场观察必须分三层，各层职责不可混淆」
> 关联文档：`docs/V46_REQUIREMENTS_TRACEABILITY.md`、`docs/FAST_SLOW_STATE_MODEL.md`、`docs/ORTHOGONAL_AXES.md`

---

## 1. 本文档的作用

本文档把「系统怎么看市场」拆成三层并规定各层的唯一职责：L1 只记录事实、L2 只由事实算指标、L3 只由指标合成判断。
它明确禁止两件事：**L3 跳过 L2 直接读 L1**，以及**任何一层数据缺失后被静默替换成默认值**——缺失必须沿层向上传播为「不可判断」。
现状盘点部分点名了 V4.6 最危险的一处 Fail-Open：`quant/sentinel/sentinel.py:97`，所有指标缺失时哨兵反而判为 GREEN（安全）。

---

## 2. 目标设计

### 2.1 三层职责

| 层 | 名称 | 唯一职责 | 允许做的事 | **禁止**做的事 |
|---|---|---|---|---|
| **L1** | 事实层 | 记录**原始观测**：行情 OHLCV、基金净值 NAV、成交额 | 落盘、去重、排序、字段标准化、计算内容哈希、记录来源与截止日 | 任何解释性计算（均线、动量、波动率）；任何缺失值填充（ffill/插值/默认值） |
| **L2** | 指标层 | 由 L1 计算**可解释的中间量**：动量、均线、波动率、回撤、宽度、利差 | 计算、命名、给出计算窗口、声明所依赖的 L1 批次 | 输出任何「状态」「等级」「结论」；对缺失的输入自行编造替代值 |
| **L3** | 判断层 | 由 L2 合成**状态判断**：`MarketRegime`、`SentinelLevel` | 阈值比较、多指标合成、迟滞、写证据链 | **直接读取 L1**；在层内临时计算指标；缺失时静默取默认状态 |

### 2.2 层间契约

| 契约 | 内容 |
|---|---|
| **C1 单向依赖** | L1 → L2 → L3。L3 的函数签名中**不得出现原始行情/净值容器**（DataFrame / Series / 文件路径）；只能接收 L2 产出的指标对象 |
| **C2 可追溯** | 每个 L2 指标必须携带 `source_refs`：它是由哪些 L1 记录（symbol + content_hash + as_of）算出来的 |
| **C3 可回答「凭什么」** | 每个 L3 判断必须能列出触发它的指标清单，每项含 `指标名 / 数值 / 阈值 / 方向`；只有中文散文理由视为不合格 |
| **C4 缺失向上传播** | 见 §2.3。任何一层的「不可用」必须以显式类型向上传递，不得被替换为数值默认值或中性状态 |
| **C5 层内无副作用** | L2、L3 均为纯计算；持久化只发生在 L1（落盘）与运行快照（记录） |

### 2.3 缺失传播规则（本设计的核心）

三层各自的「不可用」表达与传播方式：

| 起点 | L1 表现 | 传播到 L2 | 传播到 L3 | 最终对交易的影响 |
|---|---|---|---|---|
| 文件不存在 / 读取失败 | `UNAVAILABLE` | 依赖它的指标 = `UNAVAILABLE`（**不是 0、不是 NaN、不是均值**） | 该标的从判断中**显式剔除并记名**；若为基准标的则整体判断 = `UNKNOWN` | 冻结相关标的交易 |
| 数据陈旧超阈值 | `STALE(n days)` | 指标标记 `stale=True` 并携带陈旧天数 | 判断降级为 `UNKNOWN`（不允许用陈旧数据出信号） | 冻结 |
| 历史长度不足 | `INSUFFICIENT_HISTORY` | 需要该窗口的指标 = `UNAVAILABLE` | 若关键指标不可用 → `UNKNOWN` | 冻结 |
| 质量闸门不通过（负价、OHLC 矛盾、跳变） | `QUARANTINED` | 指标 = `UNAVAILABLE` | 该标的隔离；基准隔离则 `UNKNOWN` | 隔离：不新增、不据异常数据卖出 |
| 指标全部不可用 | — | 全 `UNAVAILABLE` | **快状态 = `DATA_ERROR`；慢状态 = `UNKNOWN`** | **全局冻结交易，转人工** |

三条硬性禁令：

1. **禁止 0 代替缺失。** 缺失不是「零风险」，也不是「零动量」。
2. **禁止中性代替缺失。** `NEUTRAL` / `RANGE_NEUTRAL` / `GREEN` 都是**有信息的判断**，不能承载「没有信息」。
3. **禁止跳过缺失项继续算。** 若从聚合中静默剔除缺失项，聚合结果会看起来完全正常，缺失就变成了不可见故障。必须要么显式记名剔除，要么整体判为不可判断。

### 2.4 判断层的证据格式（C3 的落地形态）

L3 每次输出必须附带如下结构（字段名可调，语义不可缺）：

| 字段 | 含义 |
|---|---|
| `verdict` | 判断结果（regime / level） |
| `contributing_indicators[]` | 每项：`name` / `value` / `threshold` / `direction`（higher_is_worse 或 lower_is_worse）/ `implied_score` |
| `unavailable_indicators[]` | 每项：`name` / `reason`（UNAVAILABLE / STALE / INSUFFICIENT_HISTORY / QUARANTINED） |
| `coverage` | 可用指标数 / 应有指标数 |
| `l1_refs[]` | 所依赖 L1 批次的 `symbol + content_hash + as_of` |
| `decided_by` | 规则版本 |

**`coverage` 是防 Fail-Open 的关键字段**：它让「我基于 4 个指标判 GREEN」与「我基于 0 个指标判 GREEN」在数据结构上不再等价。

---

## 3. 当前实现现状（诚实盘点）

### 3.1 L1 事实层

| 能力项 | 现状 | 证据（文件:行号） | 状态标签 |
|---|---|---|---|
| ETF 行情读取与标准化 | 存在。列名归一、类型转换、按日期去重排序 | `quant/datahub.py:29-35`（`load`）、`:44-77`（`standardize_frame`） | `TESTED_AND_WIRED` |
| 基金净值读取 | 存在 | `quant/fund_datahub.py:200`（`fund_nav_quality_gate`）；生产调用 `quant/fund_pipeline.py:159-180` | `TESTED_AND_WIRED` |
| L1 质量闸门（ETF 路径） | 函数完整：字段缺失、代理伪装、重复日期、非单调、空值、非正价、OHLC 矛盾、负成交、陈旧、历史不足、单日跳变 | `quant/datahub.py:87-153`，其中 `:109-110` 拒绝 `source_symbol != symbol`，`:149-152` 命中关键词即 `BLOCK` | `TESTED_AND_WIRED`（仅在 `quant/pipeline.py:41` 路径） |
| **战术 ETF 生产链路调用质量闸门** | **未调用**。`quality_gate` 被 import 但全文件零次调用；取数只 catch `FileNotFoundError`/`ValueError`，其余异常与「数据烂但能读」一律放行 | `quant/strategies/tactical_etf/engine.py:15`（import）；全文件无 `quality_gate(` 调用；取数循环 `:69-75` | `NOT_FOUND` |
| 带质量闸门的 ETF 管线是否可用 | 存疑：`quant/pipeline.py:102` 在 `:110` 定义之前引用 `actionable_turnover`，该分支必然 `UnboundLocalError` | `quant/pipeline.py:102`（使用）与 `:110`（定义） | `BLOCKED` |
| 内容哈希 / 数据指纹 | 函数存在（`content_fingerprint`），但生产落盘时不计算 | `quant/data_identity/lineage.py:59-73`；生产 `quant/datahub.py:37-41`（`save`）不产出哈希 | `TESTED_BUT_NO_PROD_CALLER` |
| 数据身份登记（血缘账本） | 完整实现：DIRECT 不可伪装、PROXY 永不进生产、指纹冲突拒绝、`require_production` Fail-Closed | `quant/data_identity/lineage.py:76-140`、`:152-168`、`:180-188` | `TESTED_BUT_NO_PROD_CALLER` |
| **生产运行是否登记 L1 血缘** | **否**。全生产链路唯一的 `register_input` 只登记了 `as_of` 这个参数，没有任何一条数据的身份或指纹 | `app/common.py:72` `snap.register_input("as_of", "parameter", "as_of", str(as_of), as_of=str(as_of))` | `NOT_FOUND` |
| 多市场日历隔离 | 缺失。所有可用序列被强行截断到最慢者的日期 | `quant/fund_pipeline.py:83-84` `common_date = min(latest_dates)`，截断在 `:86-87` | `NOT_FOUND` |
| L1 层记录「不可用」的类型化表达 | 部分：`CheckResult(status, reasons, metrics)` 存在，但 `DataStatus` 只有 PASS/WARNING/BLOCK 三值，无法区分 STALE / INSUFFICIENT_HISTORY / QUARANTINED | `quant/models.py:8-11`（生产用三值）；`quant/domain/enums.py:36-41`（四值版本 PASS/DEGRADED/QUARANTINED/BLOCKED，但生产不用） | `PARTIAL` |

### 3.2 L2 指标层

| 能力项 | 现状 | 证据（文件:行号） | 状态标签 |
|---|---|---|---|
| 趋势/动量/质量指标计算 | 存在：r20/r60/r120、ma20/60/120、下行波动、分形度、正收益日占比、60 日回撤、趋势质量、综合评分 | `quant/features.py:35-85` | `TESTED_AND_WIRED` |
| 历史长度前置校验 | 存在：不足即抛 `ValueError`，不用短窗口硬算 | `quant/features.py:41-42` | `TESTED_AND_WIRED` |
| 市场宽度指标 | 模块存在（上涨比例、MA20/MA60 以上比例、创新高比例、行业扩散） | `quant/regimes/breadth.py:1-12`（模块说明）、`:23-30`（`BreadthSnapshot`） | `TESTED_BUT_NO_PROD_CALLER` |
| **宽度指标 → 判断层的接线** | **不存在**。`compute_breadth` 的调用方只有 `tests/test_features_regime.py:84,87`；`RegimeEnsemble` 不调用它，而是等外部把 12 个浮点数塞进 `EnsembleInput`，而**全仓库没有任何生产代码构造过 `EnsembleInput`** | `quant/regimes/regime_ensemble.py:27-40`（入参定义）、`:89`（`compute` 只收已算好的值）；`EnsembleInput(` 的构造点仅见于 `tests/test_features_regime.py:96-98,107-108,114` | `NOT_FOUND` |
| 指标携带 `source_refs`（C2 可追溯） | 不存在。`compute_signal_features` 返回裸 `dict[str, float]`，无来源信息 | `quant/features.py:71-85`（返回值只有数值） | `NOT_FOUND` |
| 指标携带计算窗口元信息 | 不存在。窗口来自 settings，但不随结果输出 | `quant/features.py:37-40`（读入窗口）、`:71-85`（输出不含窗口） | `NOT_FOUND` |
| **L2 缺失的类型化表达** | **不存在，且被静默替换**：①`fragmentation` 样本不足返回 `NaN`（`:9-10`），随后在 `:61` 被 `if not np.isnan(fi) else 0.0` 悄悄换成 `0.0`——`0.0` 是「分形度最低＝趋势最干净」的**最优值**，缺失被翻译成了利好；②`downside_volatility` 样本不足返回 `NaN`（`:18-19`），进入 `:68` 的除法后污染 `score` | `quant/features.py:9-10`、`:16-19`、`:61`、`:68` | `NOT_FOUND` |
| **L2 缺失标的被静默丢弃** | `score_trend` 在三种情况返回 `None`（空表 / 历史不足 / 计算异常），`rank_universe` 直接 `continue`，既不记名也不计数——该标的从此在结果中**不存在**，与「从未被考察过」不可区分 | `quant/strategies/tactical_etf/ranking.py:74-75`、`:83-84`、`:86-89`（三处返回 None）；`:130-133`（`continue`，无记录） | `NOT_FOUND` |
| 指标结果落盘 | 基金链路落盘（`Signal` 含各指标）；战术 ETF 链路只落 Top5 | `quant/models.py:41-60`（`Signal`）；`quant/strategies/tactical_etf/trend.py:37`（`ranked[:5]`） | `PARTIAL` |

### 3.3 L3 判断层

| 能力项 | 现状 | 证据（文件:行号） | 状态标签 |
|---|---|---|---|
| 慢状态判断（生产，基金链路） | 存在：MA120 + r20/r60 三分支 | `quant/fund_regime.py:12-33` | `TESTED_AND_WIRED` |
| 慢状态判断（生产，ETF 链路） | 存在：MA120 + 吊灯线 + r20/r60 | `quant/regime.py:12-35` | `TESTED_AND_WIRED` |
| 慢状态判断（多维集成版） | 存在：5 维加权打分 + 切换保护 | `quant/regimes/regime_ensemble.py:89-158` | `TESTED_BUT_NO_PROD_CALLER` |
| 快状态判断（哨兵） | 存在：4 指标各自映射 0-3 级后取最高，再与事件等级取最高 | `quant/sentinel/sentinel.py:71-106`；阈值 `:36-48`；映射 `:51-68` | `TESTED_BUT_NO_PROD_CALLER` |
| **C1 禁止跳层** | **违反**。两条生产判断函数都直接接收 L1 原始容器并在函数体内临时算指标，没有独立的、可被引用的 L2 产物 | `quant/strategies/tactical_etf/engine.py:79` `core_regime(benchmark, ...)` 传入的是原始 DataFrame；`quant/regime.py:14-21` 在 L3 函数体内算 MA120/ATR/r20/r60/dvol。基金侧同构：`quant/fund_pipeline.py:196` `fund_nav_regime(benchmark)`；`quant/fund_regime.py:21-27` 内部算指标 | `NOT_FOUND` |
| **L3 Fail-Open：指标全缺判 GREEN** | **严重缺陷**。四个指标字段全为 `None` 时 `scores` 为空列表，`indicator_level` 取 `0`；若同时无已确认事件，则 `raw = max(0, 0) = 0` → `SentinelLevel.GREEN`。即「什么都看不见」被判为「一切安全」，且 `reasons` 为空列表——连一条说明都不会留下 | `quant/sentinel/sentinel.py:97` `indicator_level = max(scores) if scores else 0`；下游 `:105` `raw = max(indicator_level, event_level)`；空 reasons 见 `:73-95`（四个 `if ... is not None` 全部跳过） | `BLOCKED` |
| L3 Fail-Open：慢状态无输入判中性 | 同类缺陷，四处：取数失败→NEUTRAL、基准不可用→NEUTRAL（且 metrics 为空 `{}`）、历史不足→NEUTRAL、集成器各维度无输入→0.5→RANGE_NEUTRAL | `quant/strategies/tactical_etf/engine.py:74-80`；`quant/fund_pipeline.py:192-194`；`quant/fund_regime.py:19-20`；`quant/regimes/regime_ensemble.py:170,182,193,198` | `NOT_FOUND` |
| C3 判断证据（指标名 + 数值） | 部分：基金链路 `RegimeResult` 带 `reasons` + `metrics` 并随决策日志落盘 | `quant/models.py:64-76`；落盘 `quant/fund_pipeline.py:375,410,440` | `PARTIAL` |
| C3 判断证据（阈值 + 方向 + coverage） | 不存在。`metrics` 只有裸数值，无阈值、无方向、无「用了几个指标」 | `quant/fund_regime.py:28`（metrics 无阈值）；`quant/regime.py:23-30`（同） | `NOT_FOUND` |
| 战术 ETF 链路的判断证据 | **几乎为零**：输出对象只保留 `regime_label` 字符串，指标全部丢弃；运行快照 metrics 也只有这个标签 | `quant/strategies/tactical_etf/engine.py:44-51`（`to_dict` 无 metrics）；`app/run_tactical_etf.py:16` `metrics={"regime": result.regime_label}` | `NOT_FOUND` |
| `unavailable_indicators[]` 清单 | 不存在。缺失指标不会出现在任何输出里 | `quant/sentinel/sentinel.py:73-95`（`is not None` 才追加，缺失静默跳过）；`quant/strategies/tactical_etf/ranking.py:130-133` | `NOT_FOUND` |
| L3 判断能否表达「不可判断」 | 不能。`SentinelLevel` 无 `DATA_ERROR`（`levels.py:26-31`）；`MarketRegime.UNKNOWN` 为不可达输出（见 `docs/FAST_SLOW_STATE_MODEL.md` §3.1） | `quant/sentinel/levels.py:26-31`；`quant/regimes/regime_ensemble.py:117-128,200-233` | `NOT_FOUND` |

### 3.4 缺陷聚焦：`quant/sentinel/sentinel.py:97` 为什么是最危险的一条

```python
# quant/sentinel/sentinel.py:97
indicator_level = max(scores) if scores else 0
```

| 维度 | 说明 |
|---|---|
| **触发条件** | `SentinelReading` 的四个指标字段（`drawdown`、`volatility_percentile`、`breadth_above_ma50`、`cross_asset_stress`，定义于 `:29-33`）全部为 `None`。而这四个字段的默认值**就是 `None`**，即构造一个只带 `as_of` 的读数就会触发 |
| **后果** | 等级判为 `GREEN`(0) → `allows_new_entries` 为 True（`levels.py:38-40`）→ 系统在完全没有市场观测的情况下允许开仓 |
| **为什么比「判错方向」更糟** | 判 RED 判错了，至少有 `reasons` 可复盘；这里 `reasons` 是**空列表**（`:73-95` 四个分支全部跳过，`:99-103` 事件分支也不追加），审计时看到的是一条「GREEN，无任何理由」的记录，与真正健康的 GREEN **完全无法区分** |
| **迟滞机制帮不上忙** | 非对称迟滞（`:157-180`）只防止「从高级别过快降级」。若系统一开始就是 GREEN，数据消失后仍是 GREEN，等级从未变化，迟滞逻辑根本不会被触发 |
| **验收条件反而固化了它** | `app/v46_acceptance.py:172-175` 只断言「恰有 4 级」，`:178-189` 只测试有读数时的迟滞。没有任何一条验收条件构造过「空读数」场景 |
| **当前未爆的唯一原因** | 哨兵零生产调用方（见 `docs/FAST_SLOW_STATE_MODEL.md` §3.2）。**这意味着 GAP-M1 必须在哨兵接入生产之前修复，否则接入动作本身就是把一个 Fail-Open 缺陷引入生产** |

同一模式在慢状态侧重复了四次（§3.3「L3 Fail-Open：慢状态无输入判中性」），其中 `quant/fund_pipeline.py:192-194` 尤其值得注意：它构造的 `RegimeResult` 的 `metrics` 是空字典 `{}`，即**判断落盘了、证据是空的**——正是 C3 想禁止的形态。

---

## 4. 差距与落地计划

| 编号 | 差距 | 归属轴 | 优先级 | 落地动作 |
|---|---|---|---|---|
| GAP-M1 | `sentinel.py:97` 指标全缺判 GREEN（Fail-Open） | ENGINEERING_AXIS | **P0** | `scores` 为空 → 返回 `DATA_ERROR`；同时把 `coverage`（可用指标数/应有数）写入返回值，低于阈值也判 `DATA_ERROR` |
| GAP-M2 | 慢状态四处静默落中性 | ENGINEERING_AXIS | **P0** | 统一改为 `UNKNOWN`：`tactical_etf/engine.py:74-80`、`fund_pipeline.py:192-194`、`fund_regime.py:19-20`、`regime_ensemble.py:170,182,193,198` |
| GAP-M3 | `features.py:61` 把 NaN 分形度换成 `0.0`（最优值） | ENGINEERING_AXIS | **P0** | 改为：`fi` 为 NaN 时整个 `trend_quality` 与 `score` 标记为不可用，而非贡献一个最优分量 |
| GAP-M4 | 战术 ETF 生产链路完全没有 L1 质量闸门 | ENGINEERING_AXIS | **P0** | 在 `tactical_etf/engine.py:69-75` 的取数循环内调用已 import 但未使用的 `quality_gate`（`:15`），并把 `CheckResult` 随决策落盘 |
| GAP-M5 | 生产运行不登记任何 L1 血缘（只登记 `as_of` 参数） | ENGINEERING_AXIS | **P0** | 每个被消费的 symbol 构造 `DataLineageRecord` 并 `register_input`，落入运行快照 manifest（现状 `app/common.py:72`；可用能力 `quant/data_identity/lineage.py:197-199` `to_manifest`） |
| GAP-M6 | L2 指标缺失的标的被静默丢弃，不可与「未考察」区分 | ENGINEERING_AXIS | **P1** | `rank_universe` 增加 `excluded[]` 输出，每项含 symbol + 排除原因（现状 `ranking.py:130-133` 直接 `continue`） |
| GAP-M7 | L3 直接读 L1（跳层），无独立 L2 产物 | ENGINEERING_AXIS | **P1** | 抽出 `MarketIndicators` 值对象作为 L2 边界；`core_regime` / `fund_nav_regime` 改为只接收该对象，禁止再接收 DataFrame |
| GAP-M8 | L2 指标不携带 `source_refs`（C2 不可追溯） | ENGINEERING_AXIS | **P1** | `MarketIndicators` 强制携带所依赖 L1 记录的 `symbol + content_hash + as_of` |
| GAP-M9 | 宽度模块与判断层未接线，`EnsembleInput` 无任何生产构造点 | ENGINEERING_AXIS | **P1** | 增加 L2 装配器：`compute_breadth` → `EnsembleInput` → `RegimeEnsemble.compute`（现状构造点仅见于测试） |
| GAP-M10 | L3 输出缺 `contributing_indicators` / `unavailable_indicators` / `coverage` | ENGINEERING_AXIS | **P1** | 按 §2.4 定义证据结构；`coverage` 为必填，缺失即拒绝输出判断 |
| GAP-M11 | 战术 ETF 判断证据只有一个字符串标签 | ENGINEERING_AXIS | **P1** | `TacticalETFResult.to_dict`（`engine.py:44-51`）补 metrics 与证据；快照 metrics 同步（`app/run_tactical_etf.py:16`） |
| GAP-M12 | 生产 `DataStatus` 只有三值，无法区分陈旧/隔离/历史不足 | ENGINEERING_AXIS | **P2** | 生产迁移到 `quant/domain/enums.py:36-41` 的四值版本，并补 `STALE`/`INSUFFICIENT_HISTORY` 细分原因字段 |
| GAP-M13 | 多市场日历被对齐到最慢者，L1 层污染全部下游 | ENGINEERING_AXIS | **P2** | 按日历分组（CN / HK / QDII）分别对齐，禁止跨组取 `min`（现状 `fund_pipeline.py:83-84`） |
| GAP-M14 | 带质量闸门的 `quant/pipeline.py` 存在必然抛错的死分支 | ENGINEERING_AXIS | **P2** | 修复 `:102` 先用后定义的 `actionable_turnover`（定义在 `:110`），并补该路径的冒烟测试 |
| GAP-M15 | 各层无独立的层级单测（层职责靠约定而非机器约束） | ENGINEERING_AXIS | **P3** | 增加静态检查：L3 模块的函数签名中禁止出现 `pd.DataFrame` / `pd.Series` / `Path` |
| GAP-M16 | 无「观测覆盖度」时间序列，无法发现指标逐步失效 | ENGINEERING_AXIS | **P4** | 把每次运行的 `coverage` 写入快照 metrics，形成可监控趋势 |

---

## 5. 验收方式

### 5.1 L1 事实层

| 编号 | 验收条件（给定…当…则…） |
|---|---|
| AC-MKTOBS-01 | **给定** 一份 `source_symbol` 与 `symbol` 不一致的行情文件，**当** 进入任一生产取数路径（含战术 ETF 链路），**则** 必须被拒绝并标记为代理伪装；能进入指标计算即 FAIL |
| AC-MKTOBS-02 | **给定** 一次生产运行，**当** 读取运行快照 manifest 的 `inputs`，**则** 每个被消费的 symbol 都必须有一条含 `content_hash` 与 `as_of` 的记录；只有 `as_of` 参数一条即 FAIL（直接针对 `app/common.py:72`） |
| AC-MKTOBS-03 | **给定** 战术 ETF 取数循环，**当** 某标的数据存在但质量闸门不通过，**则** 该标的必须被标记为 `QUARANTINED` 且不进入排名；当前放行行为即 FAIL |
| AC-MKTOBS-04 | **给定** CN 与 QDII 两组基金且 QDII 净值滞后 3 天，**当** 对齐信号日期，**则** CN 组不得被截断到 QDII 的日期；两组必须各自对齐并各自声明信号日 |
| AC-MKTOBS-05 | **给定** 任一 L1 记录，**当** 检查其字段，**则** 不得存在任何形式的缺失值填充（ffill / 插值 / 默认值）；缺失日期必须缺失，不得被补齐 |

### 5.2 L2 指标层

| 编号 | 验收条件（给定…当…则…） |
|---|---|
| AC-MKTOBS-06 | **给定** 一个 L2 指标结果，**当** 读取其元信息，**则** 必须能取到 `source_refs`（symbol + content_hash + as_of）与计算窗口；缺任一项 FAIL |
| AC-MKTOBS-07 | **给定** 分形度因样本不足返回 NaN，**当** 计算 `trend_quality` 与 `score`，**则** 两者必须标记为不可用；把 NaN 替换为 `0.0` 参与加权即 FAIL（直接针对 `quant/features.py:61`） |
| AC-MKTOBS-08 | **给定** 资产池中有 3 个标的因数据不足无法计算指标，**当** 读取排名结果，**则** 必须能列出这 3 个标的及其排除原因；结果中只有剩余标的、无排除清单即 FAIL（直接针对 `quant/strategies/tactical_etf/ranking.py:130-133`） |
| AC-MKTOBS-09 | **给定** 任一 L2 函数，**当** 检查其返回值类型，**则** 不得包含任何状态/等级/结论字段（如 regime、level、eligible 之外的判断） |

### 5.3 L3 判断层

| 编号 | 验收条件（给定…当…则…） |
|---|---|
| AC-MKTOBS-10 | **给定** 一个所有指标字段均为 `None` 的 `SentinelReading` 且无已确认事件，**当** 调用 `compute_raw_level`，**则** 结果必须为 `DATA_ERROR` 且 `reasons` 非空；返回 `GREEN` 或返回空 `reasons` 即 FAIL（直接针对 `quant/sentinel/sentinel.py:97`） |
| AC-MKTOBS-11 | **给定** 基准数据不可用，**当** 生成慢状态判断，**则** 结果必须为 `UNKNOWN` 且证据中列出不可用原因；返回 `NEUTRAL` 且 `metrics` 为空字典即 FAIL（直接针对 `quant/fund_pipeline.py:192-194`） |
| AC-MKTOBS-12 | **给定** 任一 L3 判断函数，**当** 检查其函数签名，**则** 参数中不得出现 `pd.DataFrame`、`pd.Series` 或文件路径；出现即判定跳层，FAIL（直接针对 `quant/regime.py:12`、`quant/fund_regime.py:12`） |
| AC-MKTOBS-13 | **给定** 一次 L3 判断输出，**当** 读取其证据，**则** 必须能列出 `contributing_indicators`（含 name / value / threshold / direction）、`unavailable_indicators`（含 reason）与 `coverage` 三项；只有中文散文 `reasons` 即 FAIL |
| AC-MKTOBS-14 | **给定** 两次运行，一次 4 个指标全可用判 GREEN、一次 0 个指标可用，**当** 比较两次输出，**则** 两者在数据结构上必须可区分（至少 `coverage` 不同）；输出完全相同即 FAIL |
| AC-MKTOBS-15 | **给定** 一次战术 ETF 生产运行，**当** 读取运行快照，**则** 必须能取到本次判断所依据的指标数值与阈值；只有 `regime` 一个字符串标签即 FAIL（直接针对 `app/run_tactical_etf.py:16`） |
| AC-MKTOBS-16 | **给定** 指标可用比例低于配置阈值（例如 4 个中只有 1 个可用），**当** 生成判断，**则** 必须降级为「不可判断」，而不是用剩余 1 个指标给出正常结论 |

---

## 6. 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-08-01 | v1.0 | 初版。定义 L1/L2/L3 职责与 C1–C5 层间契约、缺失传播规则与三条硬性禁令、判断层证据格式（含 `coverage`）；完成三层现状盘点；专节剖析 `quant/sentinel/sentinel.py:97` Fail-Open 缺陷及其「reasons 为空导致不可区分」的审计后果；补记战术 ETF 链路 `quality_gate` 导入未调用、生产零 L1 血缘登记两处新发现；列出 GAP-M1..M16 与 AC-MKTOBS-01..16 |
