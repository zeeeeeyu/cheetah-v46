# 数据适用性与信号年龄（Data Fitness & Signal Age）

> 文档编号：ARCH-DATAFIT-001
> 版本：v1.0
> 生成日期：2026-08-01
> 对应需求：补充约束 Prompt 第十三节「数据来源分离」、第十四节「数据失效分级隔离」、第十五节「三时钟与信号年龄」
> 关联文档：`docs/V46_REQUIREMENTS_TRACEABILITY.md`、`docs/ARCHITECTURE_INFLUENCE_MATRIX.md`、`docs/CALENDAR_GROUPS.md`

---

## 0. 本文档的作用

本文档回答一个问题：**这批数据，现在还能不能用来做这个决策？**

它把这个问题拆成三个必须分别回答、且不能互相顶替的子问题：数据**是什么身份**（§1 来源分离）、数据**坏到什么程度**（§2 失效分级）、数据**有多旧**（§3 三时钟与信号年龄）。三者缺一，系统就会在「看起来正常」的状态下用错误的输入产出自信的结论。

本文档同时诚实盘点 V4.6 现状（§4）。核心结论是：**场外基金分支有真实可用的数据时效闸门，战术 ETF 分支一个都没有**——后者的质量闸门函数被 import 了却从未调用，导致 2026-07-03 的数据在 2026-08-01 仍在产出交易建议。

---

## 1. 目标设计 A：数据来源分离

### 1.1 四类数据身份

不同来源的数据可信度差异是数量级的，混用等于把最不可靠的输入伪装成事实。系统消费的每一条数据必须归属到且仅归属到下面一类：

| 层级 | 身份 | 定义 | 可信度 | 允许的用途 | 禁止的用途 |
|---|---|---|---|---|---|
| L1 | **账户事实数据**（`ACCOUNT_FACT`） | 由对账流程从券商/销售平台核对得来的持仓、份额、金额、确认记录 | **最高（唯一事实源）** | 作为一切权重、盈亏、风险敞口计算的分母；覆盖任何推算值 | — |
| L2 | **行情/净值数据**（`MARKET_OBSERVED`） | 外部数据源提供的价格、成交量、基金净值 | 高（但可能延迟、缺失、被供应商改写） | 计算指标、生成信号 | 不得用于确定持仓数量；不得反推已成交份额 |
| L3 | **派生指标数据**（`DERIVED`） | 由 L1/L2 计算得到的动量、波动率、回撤、相关性、市场状态 | 中（继承上游全部缺陷） | 驱动决策 | 不得脱离上游身份单独存在；上游降级时必须同步降级 |
| L4 | **研究/模型输出数据**（`RESEARCH_OUTPUT`） | 预测模型、影子策略、回测结果、外部研究信号 | **最低** | 研究、评估、影子对比 | **绝对禁止直接驱动交易决策或生成订单** |

### 1.2 每条数据必须能回答的三个问题

任何进入决策链的数据，都必须携带足以回答以下三问的元数据；答不出任一问的数据视为**未登记**，按 §2 的 `L3 完全失效` 处理。

| 问题 | 对应字段 | 说明 |
|---|---|---|
| **你从哪来？** | `source`（渠道）+ `source_symbol`（承载标的）+ `origin`（DIRECT / PROXY / DERIVED / SYNTHETIC） | `origin=DIRECT` 时必须满足 `source_symbol == symbol`，否则即为代理数据伪装成直连数据 |
| **什么时候来的？** | `as_of`（数据时钟）+ `retrieved_at`（摄取时钟） | 两者是不同的时间，不可互相顶替。详见 §3 |
| **基于哪批原始数据算的？** | `built_from`（上游记录的 `content_hash` 列表） | L3/L4 数据必须可追溯到具体的 L1/L2 输入批次，否则无法复盘 |

### 1.3 三条硬约束（Fail-Closed）

1. **直连即真身**：`origin == DIRECT` 且 `source_symbol != symbol` → 拒绝登记，不是警告。
2. **生产只吃直连且合格**：只有 `origin == DIRECT` 且质量为 `L0 正常` 的数据可进入生产决策链。
3. **降级传染**：L3 派生数据的质量等级 = 其所有上游输入中**最差**的那一级。派生计算不产生可信度，只消耗可信度。

---

## 2. 目标设计 B：数据失效分级隔离

### 2.1 四级失效分级

数据是否可用不是布尔量。用布尔量表达，必然导致「不是完全坏就当作完全好」。

| 等级 | 名称 | 判定条件 | 触发的隔离动作 |
|---|---|---|---|
| **L0** | 正常 | 信号年龄 ≤ 阈值、必需字段齐全、无异常跳变、历史长度充足 | 正常参与排名、配权、下单 |
| **L1** | **陈旧**（有数据但太老） | 信号年龄 > 该资产所属日历组的阈值（见 `docs/CALENDAR_GROUPS.md`），其余检查通过 | ①**禁止新增建仓**；②现有持仓**冻结**（不加不减）；③该标的不参与横向排名；④决策报告显式标注「基于 N 天前数据」 |
| **L2** | **部分缺失**（部分标的缺 / 部分窗口缺） | 近端信号窗口内存在空值，或 universe 中部分标的不可用 | ①缺失标的按 L3 处理并冻结持仓；②**其余标的继续正常决策**（不因一只坏数据阻塞全体）；③若缺失的是基准标的 → 升级为 L3 全局阻断 |
| **L3** | **完全失效** | 数据为空、必需字段缺失、`source_symbol` 不匹配、日期不可解析、时点穿越（数据日期晚于运行日期） | ①**全局阻断本次决策**（`DATA_BLOCKED`）；②不产出任何目标权重；③不生成任何订单；④现有持仓全部冻结；⑤写入运行快照并要求人工介入 |

### 2.2 Fail-Closed 铁律

> **缺数据绝不能静默降级成「中性」继续出信号。**

「中性」是一个**有信息量的结论**——它断言「市场既不强也不弱」。而「没有数据」是**零信息量**。把后者渲染成前者，是把无知伪装成判断，并且这个伪装会被下游当作有效输入继续计算，最终产出一个带具体权重数字的建议。

必须遵守的三条：

| 铁律 | 正确做法 | 错误做法（反模式） |
|---|---|---|
| **缺数据 ≠ 中性** | 返回 `UNKNOWN` 或抛出，由上层决定阻断 | `regime if regime else NEUTRAL` |
| **缺指标 ≠ 安全** | 无指标读数时风险等级取**最保守值**（最高警戒） | `max(scores) if scores else 0`（0 = 绿灯） |
| **异常 ≠ 忽略** | 捕获异常后必须转换为显式的失效等级并向上传播 | `except: frame = pd.DataFrame()` 后继续走正常流程 |

### 2.3 隔离动作的方向性约束

隔离的目的是**停止基于坏数据行动**，而不是**基于坏数据采取新行动**。因此：

- 数据失效时**冻结持仓**，而不是清仓。清仓也是一个交易决策，它同样需要可信数据支撑。
- 数据失效时**不得赎回**。「数据坏了所以先卖掉」在净值缺失期间等于用未知价格执行未经验证的判断。

---

## 3. 目标设计 C：三时钟与信号年龄

### 3.1 三个时钟的定义

系统内并存三个不同的时间，混淆它们是重大隐患。

| 时钟 | 定义 | 典型取值来源 | 混淆后的后果 |
|---|---|---|---|
| **数据时钟**（`data_clock`） | 这批数据对应的**市场日期**（收盘日 / 净值日） | 数据序列中的 `max(date)` | 把决策日当作数据日 → 报告声称「2026-08-01 的信号」，实际是 07-03 的数据 |
| **决策时钟**（`decision_clock`） | 本次运行**做出决策的时刻** | 运行入口的 `as_of` / 系统 wall clock | 把数据日当作决策日 → 无法计算信号年龄 |
| **执行时钟**（`execution_clock`） | 订单实际**成交/确认**的时刻 | 场外基金：T+1 未知价成交、T+1~T+2 确认份额、T+2 到账 | 假设决策时刻即成交时刻 → 回测系统性乐观，实盘持续滑价 |

补充一个次级时钟，不可与数据时钟混用：

- **摄取时钟**（`retrieved_at`）：数据被抓取/写入本地的时刻。它只回答「这份文件是什么时候下载的」，**不回答「数据本身有多新」**。一份 2026-07-03 抓取的、内容截止 2026-07-03 的文件，在 2026-08-01 读取时摄取时钟仍是 07-03，但它已经陈旧 29 天。

### 3.2 信号年龄

```
信号年龄 (signal_age) = 决策时钟 − 数据时钟
```

约束：

1. **必须显式计算**。不能靠「数据是刚读的所以应该是新的」这种隐含假设。
2. **必须显式设阈值**，且阈值**按日历组分别设定**（CN / HK / QDII 的净值披露节奏不同，见 `docs/CALENDAR_GROUPS.md`）。
3. **超龄必须拒绝出信号**，而不是打折使用。衰减函数（如指数衰减）只适用于「仍在有效期内的信号强度调整」，不能用来让一个过期信号「弱一点地」继续生效。
4. **信号年龄必须写入决策报告与运行快照**，与决策结论同级展示。

### 3.3 建议阈值

| 日历组 | 数据类型 | `max_signal_age` 建议值 | 依据 |
|---|---|---|---|
| CN | ETF 日线 | 1 个交易日 | T 日收盘即有数据，隔日未更新即异常 |
| CN | 场外基金净值 | 4 个日历日 | 与现行 `config/fund/settings.json:9` 一致 |
| QDII | 基金净值 | 6~8 个日历日 | 境外市场 + 披露滞后 1~2 个交易日；现行 QDII 单例配置为 6 |
| HK | 待定 | 待定 | 当前无在册资产，见 `docs/CALENDAR_GROUPS.md` §4.3 |

### 3.4 三时钟一致性校验

每次运行必须校验：

- `data_clock ≤ decision_clock`（否则为**时点穿越**，直接 L3 阻断）
- `decision_clock ≤ execution_clock`（否则为**回溯执行**，非法）
- `signal_age ≤ max_signal_age[calendar_group]`（否则按 §2 L1 陈旧隔离）

---

## 4. 当前实现现状（诚实盘点）

> 状态标签定义：`NOT_FOUND` 能力不存在 / `PARTIAL` 部分存在 / `IMPLEMENTED_UNTESTED` 有实现无测试 / `TESTED_BUT_NO_PROD_CALLER` 有实现有测试但生产零调用 / `TESTED_AND_WIRED` 已接入生产链 / `BLOCKED` 被其他缺陷阻塞

### 4.1 A 组：数据来源分离

| 能力项 | 现状 | 证据（文件:行号） | 状态 |
|---|---|---|---|
| 四类数据身份（L1~L4）建模 | 无统一身份分层。存在 `DataOrigin`（DIRECT/PROXY/DERIVED/SYNTHETIC），但它描述的是「是否代理」，不是「账户事实 / 行情 / 派生 / 研究」的可信度分层 | `quant/data_identity/lineage.py:46-52` | `PARTIAL` |
| 数据血缘记录对象 | `DataLineageRecord` 完整实现：含 `symbol` / `source` / `source_symbol` / `origin` / `content_hash` / `as_of` / `quality` / `retrieved_at`，且构造时强制「直连即真身」 | `quant/data_identity/lineage.py:76-125`；硬约束在 `:105-112` | `TESTED_BUT_NO_PROD_CALLER` |
| 生产可消费性判定 | `is_production_eligible` 与 `require_production()` 均已实现，语义正确（Fail-Closed） | `quant/data_identity/lineage.py:114-125`、`:180-188` | `TESTED_BUT_NO_PROD_CALLER` |
| **`data_identity` 整包生产调用方** | **零**。全仓库仅两处引用：验收脚本 `app/v46_acceptance.py:221,277` 与单元测试 `tests/test_data_identity.py:18`。`quant/` 下除包内自引用外无任何调用 | grep `data_identity\|DataIdentityRegistry\|DataLineageRecord\|DataOrigin` 全量结果 | `TESTED_BUT_NO_PROD_CALLER` |
| 数据文件级来源标记 | **已落地**。ETF 与基金 CSV 均含 `source` / `source_symbol` / `retrieved_at` 三列，且为质量闸门的必需字段 | `quant/datahub.py:15`、`quant/fund_datahub.py:18`；必需字段校验见 `quant/fund_datahub.py:227` | `TESTED_AND_WIRED` |
| 代理数据阻断（生产路径） | 基金路径与 ETF 路径均校验 `source_symbol` 与标的一致，不一致直接 BLOCK | `quant/fund_datahub.py:236-237`、`quant/datahub.py:109-110` | `TESTED_AND_WIRED`（基金路径）/ `BLOCKED`（ETF 路径，闸门未被调用，见 §4.3） |
| 派生数据溯源（`built_from`） | 不存在。`DataLineageRecord` 无 `built_from` 字段，派生指标（动量、波动率、regime）不记录其上游批次 | `quant/data_identity/lineage.py:76-103` 字段清单 | `NOT_FOUND` |
| 研究输出与决策链物理隔离 | 已落地：`quant/research_models/` 冻结，HMM 仅作影子（`hmm_shadow_only: true`） | `config/fund/settings.json:56`；`quant/fund_pipeline.py:197` 仅写入 `regime.hmm_shadow` 不参与决策 | `TESTED_AND_WIRED` |
| 账户事实数据（L1）优先级 | 存在 `RECONCILIATION` 来源白名单概念，但已知存在三处旁路（见 `ARCHITECTURE_INFLUENCE_MATRIX.md` §8.2 GAP-4） | 见 `docs/ARCHITECTURE_INFLUENCE_MATRIX.md` GAP-4 | `PARTIAL` |

### 4.2 B 组：数据失效分级隔离

| 能力项 | 现状 | 证据（文件:行号） | 状态 |
|---|---|---|---|
| **两个互不相通的 `DataStatus` 枚举** | **确认存在，但位置与既有记录不符**。生产写入/判断路径使用 `quant/models.py:8-11` 的 `DataStatus`（`PASS_` / `WARNING` / `BLOCK`）；领域层与 `data_identity` 使用 `quant/domain/enums.py:36-41` 的 `DataStatus`（`PASS` / `DEGRADED` / `QUARANTINED` / `BLOCKED`）。两者**无任何转换函数**，事实上断链 | `quant/models.py:8-11`、`quant/domain/enums.py:36-41`；`data_identity` 侧的 import 见 `quant/data_identity/lineage.py:31`；生产侧的 import 见 `quant/fund_pipeline.py:29` | `NOT_FOUND`（指「统一」这一能力） |
| `DEGRADED` / `QUARANTINED` 分级的实际使用 | 领域枚举的这两级**仅出现在测试中**（`tests/test_data_identity.py:127,169`），生产代码零使用 | grep `DataStatus.DEGRADED\|DataStatus.QUARANTINED` 全量结果 | `TESTED_BUT_NO_PROD_CALLER` |
| `WARNING` 等级的实际效力 | **产出但不被消费**。`fund_nav_quality_gate` 会返回 `WARNING`（`:323`），但所有下游消费点只判断 `== BLOCK` 或 `!= PASS_`；在生产主链 `fund_pipeline` 中 `WARNING` 与 `PASS_` 行为完全一致，等于三级枚举退化为二值 | 产出：`quant/fund_datahub.py:320-324`；消费：`quant/fund_pipeline.py:53`、`:76` | `PARTIAL`（形同虚设） |
| 失效四级分级（正常/陈旧/部分缺失/完全失效） | 无四级模型。**但基金分支存在两个真实的二级隔离机制**：①近端窗口异常 vs 历史异常区分（近端 BLOCK、历史仅 WARNING）；②`BLOCK_GLOBAL` vs `QUARANTINE` 区分 | ①`quant/fund_datahub.py:281-284`、`:309-318`；②`quant/fund_pipeline.py:38-59`，策略字段见 `config/fund/instruments.json:11,29` | `PARTIAL` |
| 隔离动作：冻结而非清仓 | **已正确实现**。被隔离标的的持仓按当前权重冻结，明确不新增、不基于异常数据赎回，剩余权重在健康标的间重标定 | `quant/fund_pipeline.py:91-123`；用户可见说明 `:350-352` | `TESTED_AND_WIRED` |
| **ETF 分支的失效处理** | **Fail-Open**。取数异常被吞掉并替换为空 `DataFrame`，随后基准为空时 regime 静默降级为 `Regime.NEUTRAL`，流程继续 | `quant/strategies/tactical_etf/engine.py:70-75`（`except (FileNotFoundError, ValueError): frames[...] = pd.DataFrame()`）、`:79-80`（`regime_result.regime if regime_result else Regime.NEUTRAL`） | `NOT_FOUND` |
| **ETF 分支的质量闸门** | **被 import 但从未调用**。`quality_gate` 在 `engine.py:15` 被导入，全 `quant/strategies/` 下无任何调用点。唯一调用它的是 `quant/pipeline.py:46`，而该文件的入口函数 `run_daily()`（`:26`）**全仓库零调用方**，是完全死代码 | 导入：`quant/strategies/tactical_etf/engine.py:15`；唯一调用点：`quant/pipeline.py:46`；死代码证据：grep `from quant.pipeline\|from .pipeline` 除定义处外无命中 | `BLOCKED` |
| 哨兵无数据判绿灯 | `indicator_level = max(scores) if scores else 0` —— 全部指标缺失时等级为 0（GREEN）。**注：该缺陷当前不在生产链上**——`MarketSentinel` 仅被 `app/v46_acceptance.py:180-181` 与测试引用，接线后立即成为高危 | `quant/sentinel/sentinel.py:97`；扩散至 `:105` `raw = max(indicator_level, event_level)` | `TESTED_BUT_NO_PROD_CALLER` |
| 基准失效全局阻断 | 基金分支已实现：基准净值不可用时不强行生成 regime，且置 `DATA_BLOCKED` | `quant/fund_pipeline.py:192-194`、`:236` | `TESTED_AND_WIRED` |

### 4.3 C 组：三时钟与信号年龄

| 能力项 | 现状 | 证据（文件:行号） | 状态 |
|---|---|---|---|
| 统一三时钟模型 | 不存在统一对象。三个时钟以零散字段形式散落：决策时钟 `as_of`（`app/run_daily.py:36`）、快照 `started_at`（`quant/observability/snapshot.py:189`）、订单 `signal_date` / `earliest_execution_date`（`quant/exe/order_factory.py:46,74`） | 同左 | `PARTIAL` |
| 数据时钟与决策时钟分离（基金分支） | **已实现**。`common_signal_date`（数据时钟）与 `as_of_date`（决策时钟）在决策载荷中分列两处输出 | `quant/fund_pipeline.py:88` 返回、`:385` 写入 `operational_notes`、`:435` 写入 `quality_isolation` | `TESTED_AND_WIRED` |
| 数据时钟与决策时钟分离（ETF 分支） | **未分离，且发生了混淆**。`TacticalETFResult.as_of_date` 直接写入决策时钟，报告中不存在任何数据时钟字段——报告标注为「2026-08-01 的信号」，实际底层数据截止 2026-07-03 | `quant/strategies/tactical_etf/engine.py:118`（`as_of_date=str(as_of.date())`）；`to_dict()` 输出字段清单见 `:44-51`，无数据日期字段 | `NOT_FOUND` |
| 交易计划中 `signal_date` 的语义 | **命名与内容不符**。计划行的 `signal_date` 填入的是 `decision.as_of_date`（决策时钟），而真正的信号数据日期是 `common_signal_date` | 写入：`quant/fund_execution.py:177`；取值来源：`quant/fund_pipeline.py:399` ← `:372` | `PARTIAL` |
| 执行时钟建模（未知价 / 确认延迟） | **概念正确且已落地**。基金订单严格按金额下单、`estimated_quantity` 恒为 0、明确不用当日净值反推份额；计划行输出下单日、定价规则与确认延迟 | `quant/exe/order_factory.py:51-56`（未知价原则说明）、`:74`（`estimated_quantity=0.0`）；`quant/fund_execution.py:185-187` | `TESTED_AND_WIRED` |
| **`signal_age` 显式计算函数** | **存在，但零生产调用方**。`quant/feat/time_decay.py` 提供 `signal_age()`、`data_freshness()`、`signal_expiration()`，唯一引用方是 `tests/test_features_regime.py:9` | 定义：`quant/feat/time_decay.py:6`、`:15`、`:19`；调用方 grep 仅命中测试 | `TESTED_BUT_NO_PROD_CALLER` |
| 数据陈旧度阈值（基金分支） | **已接入生产**。`max_staleness_calendar_days` 逐标的可配，超限直接 BLOCK | 判定：`quant/fund_datahub.py:277`；接线：`quant/fund_pipeline.py:167-171`；全局默认 4（`config/fund/settings.json:9`）；QDII 单例覆盖为 6（`config/fund/instruments.json:67`） | `TESTED_AND_WIRED` |
| 数据陈旧度阈值（ETF 分支） | **配置声明了，代码从不执行**。`config/settings.json:9` 设 `max_staleness_trading_days: 0`（零容忍），但唯一读取它的是死代码 `quant/pipeline.py:46`；生产路径 `run_tactical_etf` 完全不读该值 | 配置：`config/settings.json:9`；判定逻辑：`quant/datahub.py:137`；死代码：`quant/pipeline.py:26,46` | `BLOCKED` |
| 时点穿越检测 | 两条路径的闸门都实现了（数据日期晚于运行日期 → BLOCK），但 ETF 路径同样因闸门未调用而失效 | `quant/fund_datahub.py:275-276`、`quant/datahub.py:135-136` | `PARTIAL` |
| 摄取时钟（`retrieved_at`）的使用 | 仅在写入时自动填充、在闸门中校验字段存在性，**从未参与任何时效计算** | 填充：`quant/datahub.py:72-75`、`quant/fund_datahub.py:191-194`；校验存在性：`quant/fund_datahub.py:227` | `PARTIAL` |
| `DataRepository.load()` 的校验 | **零校验**。读 CSV、转日期、排序，直接返回。不检查字段、不检查时效、不检查来源 | `quant/datahub.py:29-35` | `NOT_FOUND` |

### 4.4 实测后果：29 天前的数据仍在产出 RISK_ON 建议

这是本文档最重要的一条现状记录，因为它把上面所有抽象缺陷合成了一个可复现的事故。

**观测事实**

| 项 | 值 | 证据 |
|---|---|---|
| 全部 ETF 行情数据最新日期 | **2026-07-03** | `data/market/{510300,510880,512660,515000,515030,518880}.csv` 尾行 |
| 数据 `source` 标记 | `legacy_direct_etf_cache_seed`（自我声明为遗留缓存种子数据） | `data/market/515000.csv` 尾行 |
| 数据 `retrieved_at` | `2026-07-03T10:32:07` | 同上 |
| 决策时钟 | 2026-08-01 | `app/run_daily.py:36` 取 `pd.Timestamp.today()` |
| **信号年龄** | **29 个日历日** | 决策时钟 − 数据时钟 |
| ETF 分支自动刷新 | **永久关闭** | `config/settings.json:13` `allow_network_refresh: false` |
| 声明的陈旧容忍度 | **0 个交易日** | `config/settings.json:9` `max_staleness_trading_days: 0` |
| 实际执行的陈旧检查 | **无** | `quality_gate` 在生产 ETF 路径未被调用（§4.2 末两行） |

**失效链条**

```
data/market/*.csv 停更于 2026-07-03（source=legacy_..._seed，且 allow_network_refresh=false）
        ↓
DataRepository.load() 零校验读入                      [datahub.py:29-35]
        ↓
engine.py 只做 date <= as_of 的上界过滤，无下界/年龄检查  [engine.py:72]
        ↓
quality_gate 已 import 但从未调用                      [engine.py:15]
        ↓
基准数据非空（有 07-03 的历史）→ core_regime 正常计算    [engine.py:78-79]
        ↓
产出 RISK_ON，选出 515000 等标的，按 max_single_weight=0.45 配权
        ↓                                            [config/settings.json:27]
TacticalETFResult.as_of_date = "2026-08-01"           [engine.py:118]
        ↓
报告向用户呈现为「2026-08-01 的建议：515000 占比 45%」
```

**性质判定**：这不是「数据没更新」的运维问题，而是**架构缺陷**——配置里写着零容忍，代码里一次都不检查，报告上标着今天的日期。三者互相掩护，使得数据停更 29 天不产生任何可见告警。任何单点修复（比如手工刷新数据）都不能消除该缺陷，因为下一次停更会以完全相同的方式静默发生。

---

## 5. 对既有追溯表的更正

本次逐行核对发现 `docs/V46_REQUIREMENTS_TRACEABILITY.md` 中三条记录与代码事实不符，须一并更正：

| 条目 | 原记录 | 核对结果 | 应更正为 |
|---|---|---|---|
| REQ-DATA-02 | 证据写作「`quant/domain/enums.py:36` 与 `quant/data_identity/models.py:8`」 | **`quant/data_identity/models.py` 文件不存在**（该包仅有 `__init__.py` / `asset_master.py` / `lineage.py`）。第二个 `DataStatus` 实际位于 `quant/models.py:8-11`。`data_identity` 本身 import 的是 domain 版本（`lineage.py:31`） | 证据改为「`quant/domain/enums.py:36-41` 与 `quant/models.py:8-11`」；分裂关系描述不变，仍然成立 |
| REQ-DATA-05 | 状态 `NOT_FOUND`，理由「grep `signal_age`/`data_age`/`staleness` 无命中」 | **grep 有命中**。`quant/feat/time_decay.py:6` 定义了 `signal_age()`；`max_staleness_calendar_days` 在基金分支是**已接线并生效**的生产参数（`quant/fund_datahub.py:277` + `quant/fund_pipeline.py:167-171`） | 状态改为 `PARTIAL`：基金分支 `TESTED_AND_WIRED`，ETF 分支 `BLOCKED`，`signal_age()` 函数 `TESTED_BUT_NO_PROD_CALLER`。实测后果（29 天陈旧数据出 RISK_ON）仅适用于 ETF 分支，描述需限定范围 |
| REQ-DATA-03 | 状态 `NOT_FOUND`，理由「当前是布尔式处理」 | 基金分支存在两个真实的二级隔离：近端/历史异常区分（`fund_datahub.py:281-284`）与 `BLOCK_GLOBAL`/`QUARANTINE` 区分（`fund_pipeline.py:38-59`） | 状态改为 `PARTIAL`。「布尔式处理」的判断对 ETF 分支成立，对基金分支不成立 |

> 更正原则：诚实盘点是双向的。既不能把未落地的能力说成已实现，也不能把已落地的能力漏记为缺失——后者同样会误导排期。

---

## 6. 差距与落地计划

### P0 —— 账户与数据事实

| 编号 | 任务 | 关键改动点 | 完成判据 |
|---|---|---|---|
| DF-P0-1 | **ETF 分支接入质量闸门** | 在 `quant/strategies/tactical_etf/engine.py` 的取数循环后调用已 import 的 `quality_gate`，并让失败结果向上传播为显式失效等级 | `engine.py:15` 的 import 不再是死 import；停更数据触发阻断 |
| DF-P0-2 | **消灭 Fail-Open 降级** | 移除 `engine.py:79-80` 的 `else Regime.NEUTRAL`；新增 `Regime.UNKNOWN` 或直接抛出由上层转 `DATA_BLOCKED`。同步修 `quant/sentinel/sentinel.py:97`，无指标时取最保守级而非 0 | 无数据时系统输出「不出信号」而非「中性信号」 |
| DF-P0-3 | **统一 `DataStatus` 枚举** | 以 `quant/domain/enums.py:36-41` 四级版本为准，`quant/models.py:8-11` 改为兼容别名或提供显式转换函数；禁止新增第三个 | 全仓库只有一处 `class DataStatus` 定义 |
| DF-P0-4 | **落地失效四级分级** | 把现有的 `BLOCK_GLOBAL`/`QUARANTINE` 与近端/历史区分归并到统一的 L0~L3 模型，补齐 L1「陈旧」这一级（当前陈旧直接跳到 BLOCK，缺少「冻结但不阻断」的中间态） | §2.1 分级表每一级都有对应代码路径与测试 |
| DF-P0-5 | **信号年龄进入生产链** | 将 `quant/feat/time_decay.py:6` 的 `signal_age()` 接入两条决策路径；阈值按日历组取自配置；结果写入决策报告与 run 快照 | 决策报告中出现 `signal_age` 字段；超龄运行被拒绝 |
| DF-P0-6 | **修复 ETF 报告的时钟混淆** | `TacticalETFResult` 增加 `data_as_of_date` 字段并在 `to_dict()`（`engine.py:44-51`）中输出，与 `as_of_date`（决策时钟）并列 | 报告可同时看到「数据截止 07-03 / 决策日 08-01 / 信号年龄 29 天」 |
| DF-P0-7 | **`data_identity` 接入生产** | 在两条数据加载路径中登记 `DataLineageRecord`，并在进入决策前调用 `require_production()` | `quant/data_identity` 出现 `quant/` 内的真实调用方（非测试、非验收脚本） |

### P1 —— 连续运行与市场感知

| 编号 | 任务 | 说明 |
|---|---|---|
| DF-P1-1 | 信号年龄阈值按日历组分设 | 依赖 `docs/CALENDAR_GROUPS.md` 的分组落地 |
| DF-P1-2 | `WARNING` 等级赋予实际行为 | 当前 `fund_datahub.py:323` 产出的 `WARNING` 在 `fund_pipeline.py:53,76` 被当作 PASS；应映射为 L1 陈旧或 L2 部分缺失并触发对应动作 |
| DF-P1-3 | 摄取时钟纳入监控 | `retrieved_at` 与 `data_clock` 长期同步不变即为「数据源已死」，应告警。ETF 侧 `retrieved_at` 无时区、基金侧带 `+00:00`，需统一 |
| DF-P1-4 | 三时钟一致性校验 | 落地 §3.4 的三条校验 |

### P2 —— 风险与组合

| 编号 | 任务 | 说明 |
|---|---|---|
| DF-P2-1 | 降级传染规则 | L3 派生指标继承上游最差等级；当前 regime / 动量 / 相关性均不携带质量等级 |
| DF-P2-2 | 陈旧数据下的持仓冻结统一化 | 基金分支已有（`fund_pipeline.py:91-123`），ETF 分支无对应机制 |

### P3 —— 研究基础设施

| 编号 | 任务 | 说明 |
|---|---|---|
| DF-P3-1 | `built_from` 派生溯源 | 为 `DataLineageRecord` 增加上游 `content_hash` 列表（对应 StockSharp S-3 借鉴项） |
| DF-P3-2 | 回测中的信号年龄建模 | 回测必须复现「决策时只能看到当时可得的数据版本」，否则信号年龄检查在回测中恒为 0，无法验证 |

### P4 —— 未来扩展

| 编号 | 任务 | 说明 |
|---|---|---|
| DF-P4-1 | 数据源双活与交叉校验 | 同一标的两个独立来源不一致时的仲裁规则 |
| DF-P4-2 | 数据版本快照（point-in-time database） | 支持「回到 T 日重放当时的数据视图」 |

---

## 7. 验收方式

以下每条均为可执行的验收用例，形式为「给定…当…则…」。

### 7.1 数据来源分离

| 编号 | 验收条件 |
|---|---|
| AC-DF-01 | **给定** 一条 `origin=DIRECT` 但 `source_symbol != symbol` 的数据，**当** 尝试登记血缘，**则** 抛出 `DataIdentityError`，且**不存在**任何可绕过该校验的开关参数 |
| AC-DF-02 | **给定** 一条 `origin=PROXY` 的数据，**当** 生产决策链请求该标的，**则** `require_production()` 抛错、本次决策阻断，且代理数据不出现在任何目标权重中 |
| AC-DF-03 | **给定** 一次完整的生产运行，**当** 运行结束，**则** run 快照中的血缘清单条目数 **等于** 本次实际消费的数据序列数（不允许出现未登记就被消费的数据） |
| AC-DF-04 | **给定** 任一 L4 研究模型输出，**当** 尝试将其写入目标权重，**则** 被拒绝并记录原因 |

### 7.2 失效分级隔离

| 编号 | 验收条件 |
|---|---|
| AC-DF-05 | **给定** 基准标的数据缺失，**当** 运行决策，**则** 状态为 `DATA_BLOCKED`，目标权重为空，**且不产生** `NEUTRAL` 或任何 regime 标签 |
| AC-DF-06 | **给定** 非基准标的中的 1 只数据完全失效、其余正常，**当** 运行决策，**则** 该标的被隔离冻结，其余标的**照常**产出信号（验证 L2 不升级为 L3） |
| AC-DF-07 | **给定** 某标的持仓 20% 且其数据失效，**当** 运行决策，**则** 该 20% 被冻结，**既不新增也不赎回**，且理由在报告中显式可见 |
| AC-DF-08 | **给定** 哨兵的全部指标读数均为 `None`，**当** 计算原始等级，**则** 结果为**最高警戒级**，而非 `GREEN` |
| AC-DF-09 | **给定** 一次 `WARNING` 级的质量检查结果，**当** 进入决策，**则** 该标的的处理方式与 `PASS` **可区分**（当前不可区分，此条为回归防线） |

### 7.3 三时钟与信号年龄

| 编号 | 验收条件 |
|---|---|
| AC-DF-10 | **给定** 数据截止 2026-07-03、决策日 2026-08-01，**当** 运行战术 ETF 决策，**则** 输出为「拒绝出信号，信号年龄 29 天 > 阈值」，**而不是** RISK_ON + 45% 权重建议（本条直接对应 §4.4 事故，是本文档的核心回归用例） |
| AC-DF-11 | **给定** 任一决策报告，**当** 人工查阅，**则** 可同时读到数据时钟、决策时钟、信号年龄三个值，三者不共用同一字段名 |
| AC-DF-12 | **给定** 数据日期晚于运行日期，**当** 通过质量闸门，**则** 判定为时点穿越并全局阻断（两条路径均须成立） |
| AC-DF-13 | **给定** 一笔场外基金订单，**当** 生成订单意向，**则** `estimated_quantity == 0`，且 `earliest_execution_date > signal_date`（执行时钟严格晚于决策时钟） |
| AC-DF-14 | **给定** 连续 N 次运行且数据源未更新，**当** 第 2 次及以后运行，**则** 系统必须告警「数据源可能已停更」，而不是重复产出相同的自信结论 |

### 7.4 反向验收（防回归）

| 编号 | 验收条件 |
|---|---|
| AC-DF-15 | 全仓库 grep `else Regime.NEUTRAL`、`if scores else 0`、`except.*: .*DataFrame()` 三类 Fail-Open 模式，命中数必须为 **0** |
| AC-DF-16 | 全仓库 `class DataStatus` 定义数必须为 **1** |
| AC-DF-17 | 任何 `quality_gate` / `fund_nav_quality_gate` 的 import 都必须存在对应调用点（禁止死 import） |

---

## 8. 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-08-01 | v1.0 | 初版。§1-3 目标设计（来源分离 / 失效四级 / 三时钟与信号年龄）；§4 逐项证据盘点（含 §4.4 「29 天陈旧数据产出 RISK_ON」完整失效链条实测记录）；§5 更正既有追溯表中 REQ-DATA-02/03/05 三条与代码不符的记录；§6 P0-P4 落地计划；§7 17 条验收用例 |
