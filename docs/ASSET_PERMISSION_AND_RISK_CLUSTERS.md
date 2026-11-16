# 资产权限与风险簇（Asset Permission & Risk Clusters）

> 文档编号：ARCH-ASSETPERM-001
> 版本：v1.0
> 生成日期：2026-08-01
> 对应需求：补充约束 Prompt 第十八节「资产权限必须默认拒绝」与第十二节「风险簇是先验约束」
> 关联文档：`docs/V46_REQUIREMENTS_TRACEABILITY.md`、`docs/ARCHITECTURE_INFLUENCE_MATRIX.md`（§8.2 GAP-1）

---

## 0. 本文档的作用

本文档定义两个互相独立、但都直接决定「哪些钱可以流向哪些标的」的机制：**资产权限**（单个标的能不能被交易）与**风险簇**（一组标的合计能占多少）。

前者是**准入闸门**，必须默认拒绝；后者是**集中度约束**，必须来自先验经济关系而非历史相关性统计。

本文档同时记录一个已确认的**安全缺陷**：由于下单链路完全不校验资产权限，**配置文件事实上等同于交易授权**——谁能改 `config/`，谁就直接获得了下单能力（§3.5）。

---

## 1. 目标设计（A）资产权限

### 1.1 三个权限位

每个资产必须携带三个显式权限位，**全部默认拒绝（Fail-Closed）**。缺省、缺字段、解析失败，一律按拒绝处理。

| 权限位 | 类型 | 默认值 | 含义 | 违反后果 |
|---|---|---|---|---|
| `strategy_eligible` | bool | **`False`** | 是否可被策略纳入候选池 | 未授权标的进入排名，污染选池 |
| `execution_eligible` | bool | **`False`** | 是否可实际下单/申赎 | 未授权标的被真实买入 |
| `permission` | enum | **`OBSERVATION_ONLY`** | 数据真实资产（`REAL`）还是仅观察标的（`OBSERVATION_ONLY`） | 把观察标的当真实持仓做对账 |

### 1.2 三条硬规则

| # | 规则 | 理由 |
|---|---|---|
| **AP-1** | **默认拒绝**：dataclass 默认值、JSON 缺省值、数据库 DDL 默认值三处必须**同时**为拒绝态。任意一处 Fail-Open 都会让另外两处的严格失效 | 一个新增字段的资产、一条手工插入的 DB 记录，都不能因为"没写"而获得交易权 |
| **AP-2** | **权限位互相约束**：`execution_eligible = True` 且 `permission = OBSERVATION_ONLY` 是**自相矛盾**，必须在加载期直接拒绝，不能靠运行时某个函数去救 | 见 §3.4 的真实案例：配置里已经出现这种矛盾，且无人发现 |
| **AP-3** | **下单前必须校验**：订单生成函数**必须**逐标的检查 `execution_eligible` 与 `permission`，不通过则拒绝并写入 `rejected_reasons`。校验点在订单生成器内部，不能放在调用方 | 放在调用方 = 只要有一条新调用路径忘了检查，权限就等于不存在 |

### 1.3 权限校验在链路上的位置

```
资产目录加载 ──► [AP-1 默认拒绝] ──► [AP-2 矛盾检查] ──► 策略选池
                                                            │
                                                     [strategy_eligible?]
                                                            ▼
                                                       目标权重
                                                            │
                                                     [AP-3 下单前校验]
                                                  [execution_eligible?]
                                                  [permission == REAL?]
                                                            ▼
                                                        订单意图
```

---

## 2. 目标设计（B）风险簇

### 2.1 定义：风险簇是先验的经济约束

**风险簇 = 因为共同的经济成因，天然应该被一起约束的一组资产。** 判定依据是行业、主题、底层资产、货币/地域暴露，**不是**历史收益率的相关系数。

### 2.2 为什么拒绝统计聚类

| 问题 | 说明 |
|---|---|
| 平静期低估关联 | 两只同为半导体主题的基金，在震荡市里日收益相关性可能只有 0.5，统计聚类会把它们分开——但它们的风险来源是同一个 |
| 压力期突然收敛 | 系统性风险爆发时，几乎所有权益资产的相关性会向 1 收敛。此时统计出来的「分散化」瞬间归零 |
| **恰好在最需要时失效** | 统计聚类用平静期数据训练、在压力期被依赖。这是最坏的组合：它在你安全的时候告诉你安全，在你危险的时候还是告诉你安全 |

> 这一立场与 `docs/ARCHITECTURE_INFLUENCE_MATRIX.md` §6.3 的 `R-X1`（明确拒绝 Riskfolio-Lib 的 HRP/HERC 纯统计聚类）一致。

### 2.3 簇的三条硬规则

| # | 规则 | 说明 |
|---|---|---|
| **RC-1** | **簇定义人工维护、进版本控制**：簇归属写在 `config/risk/risk_clusters.json` 与资产目录中，任何变更走代码评审，不由算法自动产生 | 当前已定义 15 个簇 |
| **RC-2** | **一个资产可属于多个簇，权重全额计入每个簇**：不做拆分。013566 权重 30% 且属 3 个簇，则 3 个簇各计 30% | 拆分会系统性低估集中度 |
| **RC-3** | **簇暴露上限必须在下单前校验**：超限则削减至上限，**超出部分回落为现金**，不允许挪到另一个未经验证的主题 | 与 `apply_sector_cap`（`quant/portfolio.py:146-160`）的现金回落原则一致 |

### 2.4 已定义的 15 个风险簇

来源：`config/risk/risk_clusters.json`

| 分组 | 簇 ID |
|---|---|
| 规模/风格（3） | `LARGE_CAP_CORE`、`SMALL_MID_GROWTH`、`DIVIDEND_VALUE` |
| 行业/主题（7） | `ADVANCED_MANUFACTURING`、`MILITARY_DEFENSE`、`NEW_ENERGY`、`SEMICONDUCTOR`、`CONSUMPTION`、`HEALTHCARE`、`FINANCIAL` |
| 避险/防御（3） | `GOLD_SAFE_HAVEN`、`BOND_DEFENSIVE`、`CASH_DEFENSIVE` |
| 跨境/商品（2） | `CROSS_BORDER_EQUITY`、`COMMODITY` |

---

## 3. 当前实现现状（诚实盘点）

### 3.1 资产权限盘点表

| 能力项 | 现状 | 证据(文件:行号) | 状态标签 |
|---|---|---|---|
| 权限位字段是否存在 | 存在三个字段 `strategy_eligible` / `execution_eligible` / `permission` | `quant/domain/assets.py:53,56,59` | `IMPLEMENTED_UNTESTED` |
| **生产用模型的默认值** | **`True` / `True` / `REAL`——即默认放行（Fail-Open）**。新建资产不写权限即获得完整交易权 | `quant/domain/assets.py:53`（`strategy_eligible: bool = True`）、`:56`（`execution_eligible: bool = True`）、`:59`（`permission: CapitalPermission = CapitalPermission.REAL`） | `BLOCKED` |
| 另一套模型的默认值 | `False` / `False` / `OBSERVATION_ONLY`——正确的 Fail-Closed | `quant/data_identity/asset_master.py:40`（`strategy_eligible: bool = False`）、`:41`（`execution_eligible: bool = False`）、`:39`（`permission: str = "OBSERVATION_ONLY"`） | `TESTED_BUT_NO_PROD_CALLER` |
| **两套模型哪套在生产用** | **用的是 Fail-Open 的那套**。`quant/assets/__init__.py:93-94` 的 `AssetMasterLoader` 加载为 `quant.domain.assets.Asset`；Fail-Closed 的 `AssetRecord` 无生产消费方 | `quant/assets/__init__.py:89-94`、`quant/domain/assets.py:53-59` | `BLOCKED` |
| **数据库 DDL 默认值** | **也是 Fail-Open**：`DEFAULT 1 / DEFAULT 1`。第三处放行 | `quant/accounts/__init__.py:96`（`strategy_eligible INTEGER NOT NULL DEFAULT 1`）、`:97`（`execution_eligible INTEGER NOT NULL DEFAULT 1`） | `BLOCKED` |
| ETF 下单是否校验权限 | **否**。循环体内只检查价格、数量、成本占比，**无任何权限判断** | `quant/exe/order_factory.py:26-37`（`generate_etf_orders` 全函数） | `BLOCKED` |
| 基金下单是否校验权限 | **否**。循环体内只检查净值为正、目标金额为正，**无任何权限判断** | `quant/exe/order_factory.py:51-59`（`generate_fund_orders` 主循环） | `BLOCKED` |
| 观察权限否决函数 | 函数体逻辑正确（`OBSERVATION_ONLY` → 返回否决） | `quant/decision/risk_veto.py:83-95` | `IMPLEMENTED_UNTESTED` |
| **该函数的调用方** | **零个**。全仓库 grep `check_observation_only` 仅命中定义行本身，连测试都没有调用 | grep `check_observation_only` → 唯一命中 `quant/decision/risk_veto.py:83` | `NOT_FOUND` |
| `execution_eligible` 是否被任何逻辑读取 | **否**。全仓库出现处只有：字段定义、`to_dict`/`from_dict` 序列化、JSON schema 声明、DDL。**没有一处 `if` 判断** | `quant/domain/assets.py:53,56,80,81`；`quant/data_identity/asset_master.py:40,41,70,71,99,100`；`quant/assets/__init__.py:93,94,209,210`；`quant/accounts/__init__.py:96,97` | `NOT_FOUND` |
| 生产资产配置是否含权限字段 | **否**。生产链读的 `config/fund/instruments.json`（6 只基金）**完全没有** `permission` / `execution_eligible` / `strategy_eligible` 任何一个字段，只有 `active: true` | `quant/fund_pipeline.py:142`（读 `config/fund/instruments.json`）；该文件 6 条记录字段清单中无权限项 | `NOT_FOUND` |
| 带权限的资产目录是否在生产链上 | **否**。`config/assets/asset_master.json`（13 个资产，含完整权限字段）不被 `quant/fund_pipeline.py` 读取 | `quant/fund_pipeline.py:7-30` import 清单不含 `quant.assets` / `quant.data_identity` | `TESTED_BUT_NO_PROD_CALLER` |
| 资产目录加载期校验 | 有校验，但只查 lot_size、代理符号、风险簇非空——**不查权限位一致性** | `quant/assets/__init__.py:125-139` | `PARTIAL` |

### 3.2 风险簇盘点表

| 能力项 | 现状 | 证据(文件:行号) | 状态标签 |
|---|---|---|---|
| 簇定义配置 | 人工定义 15 个簇，含 `max_exposure` 与 `estimation_quality` | `config/risk/risk_clusters.json`；加载逻辑 `quant/assets/risk_clusters.py:74-87` | `IMPLEMENTED_UNTESTED` |
| 簇暴露计算器 | `RiskClusterCalculator.compute()` 实现完整：多簇全额计入、现金排除、突破判定、质量标注 | `quant/assets/risk_clusters.py:57-179`（类定义 `:57`，`compute` `:89-178`，多簇累加 `:127-134`，突破判定 `:150-152`） | `IMPLEMENTED_UNTESTED` |
| 是否为先验定义（非统计聚类） | **是**。簇归属来自配置文件与资产目录，代码中无任何相关性聚类 | `quant/assets/risk_clusters.py:121`（`clusters = asset.risk_clusters`，直接读取声明值） | `TESTED_AND_WIRED`（就设计立场而言） |
| 簇暴露测试覆盖 | 有测试 | `tests/test_asset_risk.py:18,31-32,95-100,146-151` | `IMPLEMENTED_UNTESTED` → 测试存在 |
| **簇计算器的调用方** | 仅 `DecisionEngine` 与测试。生产入口不调用 | `quant/decision/decision_engine.py:27,95,101`；`tests/test_asset_risk.py:18,32` | `TESTED_BUT_NO_PROD_CALLER` |
| **`DecisionEngine` 的调用方** | **零个生产调用方**。全仓库仅 `tests/test_execution.py:10,23` 与 `tests/test_portfolio_decision.py:10,76,79,82,85` | grep `DecisionEngine` → 定义 `quant/decision/decision_engine.py:78` + 仅测试引用 | `TESTED_BUT_NO_PROD_CALLER` |
| 簇暴露是否接入生产 | **否**。`quant/fund_pipeline.py` 的 import 清单中不含 `quant.assets.risk_clusters`、`quant.decision.*` | `quant/fund_pipeline.py:7-30` | `TESTED_BUT_NO_PROD_CALLER` |
| 生产实际有的集中度约束 | 只有**单一聚合桶**：把所有 `asset_class == "equity_sector"` 的权重加总，超过 `max_sector_weight` 则等比缩减、余额回落现金 | `quant/portfolio.py:146-160`（`apply_sector_cap`），经 `quant/fund_pipeline.py:215,219` 调用 | `TESTED_AND_WIRED` |
| 该约束能否替代风险簇 | **不能**。它是**一个**桶，不是 15 个簇；无法表达「半导体 20% 上限」与「军工 15% 上限」的差异；同时持有 3 只半导体基金不会被识别为集中 | `quant/portfolio.py:148-152`（仅按 `asset_class` 单一标签聚合） | `PARTIAL` |
| 生产的 regime 仓位上限 | 按 `group == "equity"` 聚合，按 regime 设权益总仓上限 | `quant/portfolio.py:163-171`（`apply_regime_caps`），经 `quant/fund_pipeline.py:216,220` 调用 | `TESTED_AND_WIRED` |
| 相关性约束（生产在跑） | 选池阶段限制两两相关性 `max_pair_correlation` | `quant/fund_pipeline.py:205-211`（`select_assets`） | `TESTED_AND_WIRED` |
| 相关性约束是否等于风险簇 | **否，且方向相反**。它正是本文档 §2.2 拒绝的统计口径：平静期两只同主题基金相关性不高即被判为「可以同时持有」 | `quant/fund_pipeline.py:210`（`max_pair_corr` 来自 `settings["portfolio"]["max_pair_correlation"]`） | `PARTIAL` |

### 3.3 三处 Fail-Open 的叠加效应

权限默认值在三个层次上**全部**是放行：

| 层次 | 位置 | 默认值 |
|---|---|---|
| 领域模型 | `quant/domain/assets.py:53,56,59` | `True` / `True` / `REAL` |
| 数据库 DDL | `quant/accounts/__init__.py:96,97` | `DEFAULT 1` / `DEFAULT 1` |
| 生产配置 | `config/fund/instruments.json` | 字段根本不存在 → 走模型默认 → 放行 |

唯一 Fail-Closed 的 `quant/data_identity/asset_master.py:39-41` 没有生产消费方。**三比一，且那个「一」不在链上。**

### 3.4 已存在的权限矛盾（真实案例，无人发现）

`config/assets/asset_master.json:3-19` 中的资产 `004813`（中欧先进制造股票A）：

| 字段 | 值 | 行号 |
|---|---|---|
| `strategy_eligible` | `true` | `config/assets/asset_master.json:12` |
| `execution_eligible` | `true` | `config/assets/asset_master.json:13` |
| `permission` | `"OBSERVATION_ONLY"` | `config/assets/asset_master.json:14` |
| `notes` | `"中欧先进制造。仅观察，不自动补仓或赎回。不生成自动订单。"` | `config/assets/asset_master.json:18` |

配置作者的**意图非常明确**——只观察、不下单。但：

1. `execution_eligible: true` 与 `permission: OBSERVATION_ONLY` **互相矛盾**，加载期校验（`quant/assets/__init__.py:125-139`）不检查这一点，矛盾被静默接受；
2. 唯一能兑现 `OBSERVATION_ONLY` 语义的 `check_observation_only`（`quant/decision/risk_veto.py:83`）**零调用方**；
3. 订单生成器（`quant/exe/order_factory.py:51-59`）根本不读这两个字段。

**结论：这条注释所声明的保护，在代码层面完全不存在。** 该资产一旦进入目标权重，会像任何普通资产一样被生成订单。

### 3.5 安全缺陷：配置文件事实上等同于交易授权

把 §3.1 的证据串起来，得到一条完整的攻击/误操作路径：

```
在 config/fund/instruments.json 追加一条记录（active: true）
        │  该文件无任何权限字段
        ▼
quant/fund_pipeline.py:142  加载 → validate_fund_catalog（只校验目录结构完整性）
        ▼
quant/fund_pipeline.py:200  进入信号计算
        ▼
quant/fund_pipeline.py:205  进入选池
        ▼
quant/fund_pipeline.py:213-220  进入配权（仅单票/行业/regime 上限）
        ▼
quant/fund_pipeline.py:396-408  build_fund_trade_plan → 写出真实申赎计划
```

**全程零次资产权限校验。**

严重性分析：

| 维度 | 结论 |
|---|---|
| 触发门槛 | 只需能写 `config/` 目录。不需要改任何 `.py`，不触发代码评审 |
| 检测难度 | 高。配置变更不产生权限告警；`validate_fund_catalog` 与 `validate_fund_state`（`quant/fund_execution.py:43-57`）都只校验结构与权重和，不校验授权 |
| 现有缓解 | 仅有**执行侧**的三道人工闸门：账户映射确认 `mapping_confirmed`（`quant/fund_pipeline.py:294,305`）、申赎状态/费率快照核实（`quant/fund_execution.py:75-131`）、以及计划为人工提交（不自动下单）。这些拦的是「能不能执行」，**不是**「这个标的有没有被授权」 |
| 本质 | **配置文件的写权限 = 交易授权。** 权限模型形同虚设——它存在于 dataclass 字段里，但不存在于任何一条 `if` 语句里 |

> 这是本文档记录的**最严重**缺陷，对应 `docs/ARCHITECTURE_INFLUENCE_MATRIX.md` §8.2 的 `GAP-1`。

---

## 4. 差距与落地计划

| 优先级 | 任务 | 依赖 | 交付物 |
|---|---|---|---|
| **P0** | **翻转三处默认值为 Fail-Closed**：`quant/domain/assets.py:53,56,59` → `False/False/OBSERVATION_ONLY`；`quant/accounts/__init__.py:96,97` → `DEFAULT 0`。同时为现有 13 个资产在 `config/assets/asset_master.json` 中**显式**补齐权限（不靠默认值） | 无 | 默认拒绝生效 |
| **P0** | **在订单生成器内部加权限校验**：`quant/exe/order_factory.py:26-37` 与 `:51-59` 两个循环内均加入 `execution_eligible` 与 `permission == REAL` 检查，不通过写入 `rejected_reasons` | 上一条 | 下单链闭合 |
| **P0** | **加载期矛盾检查**：在 `quant/assets/__init__.py:125-139` 增加 AP-2 规则，`execution_eligible=True` 且 `permission=OBSERVATION_ONLY` 直接抛异常。先修 `config/assets/asset_master.json:12-14` 的 004813 | 无 | 矛盾配置无法加载 |
| **P0** | **给生产配置补权限字段**：`config/fund/instruments.json` 6 条记录全部显式声明三个权限位；`validate_fund_catalog` 增加「缺权限字段即拒绝加载」 | 无 | 生产链有权限可查 |
| **P1** | **接通 `check_observation_only`**（`quant/decision/risk_veto.py:83`）：让它有真实调用方，或明确删除以避免「有函数就等于有保护」的错觉 | P0 | 零调用方函数归零 |
| **P1** | **统一两套资产模型**：`quant/domain/assets.py:Asset` 与 `quant/data_identity/asset_master.py:AssetRecord` 择一保留，或明确前者为运行时视图、后者为权威源且必须由后者构造前者 | P0 | 消除双源 |
| **P2** | **把 `RiskClusterCalculator`（`quant/assets/risk_clusters.py:57`）接入 `quant/fund_pipeline.py`**：在 `:215-220` 的 cap 链之后增加簇暴露校验，超限削减、余额回落现金 | P0 | 15 个簇真实生效 |
| **P2** | **簇上限违反的处理策略**：明确「削减」而非「清仓」，并把削减动作写入 run 快照 | 上一条 | 可审计的削减 |
| **P3** | **簇归属的定期人工复核**：主题漂移（基金改变投资方向）时簇归属会过期，需要复核节奏 | P2 | 复核 runbook |
| **P4** | **底层持仓穿透后的自动簇归属建议**（仅建议，仍需人工确认；不得自动生效） | P3 + 持仓数据 | 建议工具 |

---

## 5. 验收方式

### 5.1 资产权限 —— 默认拒绝

- 给定一条**不含**任何权限字段的资产记录，当加载该资产，则 `strategy_eligible` 为 `False`、`execution_eligible` 为 `False`、`permission` 为 `OBSERVATION_ONLY`。
- 给定一条直接 `INSERT` 到数据库、未指定权限列的资产记录，当读出该记录，则权限列值为 `0`（拒绝），不是 `1`。

### 5.2 资产权限 —— 下单校验

- 给定一个 `execution_eligible = False` 的标的出现在 `target_weights` 中，当调用 `generate_fund_orders`，则 `orders` 中**不含**该标的，且 `rejected_reasons` 中含该标的的权限拒绝原因。
- 给定一个 `permission = OBSERVATION_ONLY` 的标的（如 `004813`），当调用 `generate_etf_orders` 或 `generate_fund_orders`，则同样被拒绝。
- 给定订单生成器被从**任意新调用路径**调用，当传入未授权标的，则依然被拒绝——校验必须在生成器内部，不依赖调用方。

### 5.3 资产权限 —— 矛盾检查

- 给定一条 `execution_eligible = true` 且 `permission = "OBSERVATION_ONLY"` 的配置记录，当加载资产目录，则抛出明确异常并指出矛盾字段；**不允许**静默接受。

### 5.4 配置≠授权（安全缺陷闭环验收）

- 给定在 `config/fund/instruments.json` 中新增一条**未声明权限字段**的基金记录，当运行 `run_fund_daily`，则该基金**不进入**选池、**不出现**在 `target_weights`、**不产生**交易计划行，且决策报告中记录「未授权标的已排除」。
- 给定同一条记录被显式声明为 `execution_eligible: false`，当运行完整流程，则结果同上。
- 给定该记录被显式声明为三个权限位全部授权，当运行完整流程，则该基金正常参与——**授权必须是显式动作，不是默认状态**。

### 5.5 风险簇

- 给定三只资产均属 `SEMICONDUCTOR` 簇、权重各 10%，当计算簇暴露，则 `SEMICONDUCTOR` 暴露为 **30%**（全额计入，不拆分）。
- 给定某簇 `max_exposure = 0.20` 而目标权重使其达到 0.28，当执行下单前校验，则该簇成员被等比削减至合计 0.20，**超出的 0.08 回落为现金**，不得转移到其他簇。
- 给定簇暴露被削减，当读取 run 快照，则能查到被削减的簇 ID、削减前后暴露、以及受影响标的清单。
- 给定风险簇模块，当扫描其实现，则**不得**出现任何基于历史相关性矩阵的层次聚类（HRP/HERC 类算法）——簇归属只能来自配置声明。
- 给定 `RiskClusterCalculator` 已接入生产，当运行 `run_fund_daily`，则 run 快照中存在 `cluster_exposures` 段；缺失即视为仍未接入（当前状态：缺失）。

---

## 6. 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-08-01 | v1.0 | 初版。定义三权限位 Fail-Closed 与 15 簇先验约束；核实三处 Fail-Open 默认值（`quant/domain/assets.py:53,56,59`、`quant/accounts/__init__.py:96,97`、生产配置无权限字段）；核实 `quant/exe/order_factory.py:26-37,51-59` 下单零权限校验；核实 `check_observation_only`（`quant/decision/risk_veto.py:83`）零调用方；核实 `RiskClusterCalculator`（`quant/assets/risk_clusters.py:57-179`）仅经 `DecisionEngine` 被测试调用；定位 `config/assets/asset_master.json:12-14` 的真实权限矛盾案例；记录「配置文件事实上等同于交易授权」安全缺陷 |
