# V4.6 研究评估框架（Research Evaluation Framework）

> 文档编号：ARCH-RESEVAL-001
> 版本：v1.0
> 生成日期：2026-08-01
> 对应需求：补充约束 Prompt 第十九节「评估必须超越收益」
> 关联文档：`docs/V46_REQUIREMENTS_TRACEABILITY.md`、`docs/ARCHITECTURE_INFLUENCE_MATRIX.md`

---

## 0. 本文档的作用

本文档定义 V4.6 判断「一个策略/模型能不能用」的标准。它的出发点是一个已被反复验证的事实：**只用收益率和夏普比做筛选，会系统性地选出过拟合策略** —— 因为在足够多的参数组合里，总有一组能在历史上跑出漂亮的曲线，而这组参数恰恰是最不可能在未来复现的那组。

因此本框架要求评估**同时覆盖四个维度**：收益与风险、泛化性、可解释性、失效模式。四个维度是**合取关系**，任何一维不达标即否决，不允许用高收益去补偿不可解释。

本文档同时确立**研究层与决策层物理隔离**这一硬约束，并给出 Kronos 零信任冻结的实证理由。

本文档遵循与 `docs/ARCHITECTURE_INFLUENCE_MATRIX.md` 相同的纪律：**每条现状陈述带 `文件:行号` 证据，并标注是否在生产调用链上**。能力不存在就写 `NOT_FOUND`。

---

## 1. 目标设计：四维评估矩阵

| 维度 | 回答的问题 | 不达标的后果 |
|---|---|---|
| **D1 收益与风险** | 赚了多少？最坏会怎样？ | 只看均值会低估尾部，一次极端行情抹平数年收益 |
| **D2 泛化性** | 换个时间、换个市场状态还成立吗？ | 过拟合，回测好看实盘归零 |
| **D3 可解释性** | 为什么这么做？ | 无法复盘、无法追责、出问题不知道改哪 |
| **D4 失效模式** | 什么时候会失效？失效时长什么样？怎么发现？ | 策略已死但仍在下单，损失持续累积 |

> **合取规则**：`可用 = D1通过 AND D2通过 AND D3通过 AND D4通过`。禁止加权求和 —— 加权允许高收益买通不可解释性，这正是要防的东西。

---

## 2. 维度 1 · 收益与风险

### 2.1 指标集

常规收益/波动/夏普只描述分布的中心，**必须补齐尾部与回撤两组度量**。

| 组 | 指标 | 含义 | 为什么必须有 |
|---|---|---|---|
| 收益 | 总收益、年化收益 | 中心趋势 | 基础 |
| 波动 | 年化波动、夏普、索提诺 | 离散度 | 基础，但对肥尾不敏感 |
| **尾部** | **VaR(α)** | α 分位损失 | 回答「典型的坏日子有多坏」 |
| **尾部** | **CVaR(α)** / 期望损失 | 超过 VaR 的条件均值 | **回答「坏日子真的来了有多坏」**。VaR 只给门槛，CVaR 给门槛之后的期望 —— 这才是仓位管理真正需要的数 |
| **尾部** | **EVaR(α)** | 熵风险度量，CVaR 的上界 | 对极端尾部更保守；作为可选加严项 |
| **回撤** | **MDD** 最大回撤 | 峰谷最大跌幅 | 回答「历史上最难受的一刻」 |
| **回撤** | **ADD** 平均回撤 | 回撤序列均值 | 单点 MDD 可能是偶然，均值刻画常态 |
| **回撤** | **CDaR(α)** 条件回撤 | 最差 α 分位回撤的条件均值 | MDD 是单个观测值、方差极大；CDaR 是尾部回撤的期望，**统计上稳定得多**，更适合做策略间比较 |
| **回撤** | **UCI** 溃疡指数 | 回撤平方均值的平方根 | **同时惩罚回撤的深度和持续时间**。两个策略 MDD 都是 -20%，一个 3 天恢复一个 3 年恢复，只有 UCI 能区分 |
| 成本 | 换手率、总成本、成本/收益比 | 摩擦 | 高频策略的收益常常全被成本吃掉 |

### 2.2 指标定义来源与「只借鉴不依赖」声明

上述尾部与回撤指标的**定义**参考 Riskfolio-Lib 的实现：

| 指标 | 参考实现位置 |
|---|---|
| CVaR | `riskfolio/src/RiskFunctions.py:356` `CVaR_Hist(X, alpha=0.05)` |
| EVaR | `riskfolio/src/RiskFunctions.py:556` `EVaR_Hist(X, alpha=0.05, solver="CLARABEL")` |
| MDD | `riskfolio/src/RiskFunctions.py:787` `MDD_Abs(X)` |
| ADD | `riskfolio/src/RiskFunctions.py:835` `ADD_Abs(X)` |
| CDaR | `riskfolio/src/RiskFunctions.py:943` `CDaR_Abs(X, alpha=0.05)` |
| UCI | `riskfolio/src/RiskFunctions.py:1111` `UCI_Abs(X)` |

**明确声明：只借鉴指标定义，不引入该依赖。** 理由（与 `ARCHITECTURE_INFLUENCE_MATRIX.md` §6.3 R-X2 一致，此处补实证）：

| # | 理由 | 证据 |
|---|---|---|
| 1 | **依赖链过重**。`requirements.txt` 要求 `cvxpy>=1.7.2` + `clarabel>=0.11.1` + `SCS>=3.2.7` + `vectorbt>=0.28.0` + `statsmodels` + `arch` + `astropy` + `networkx` + `pybind11`。V4.6 是「模块化单体 + SQLite」的轻量栈，为了 6 个可以用 numpy 十几行写完的公式引入一整套凸优化 + 回测框架，得不偿失 | `Riskfolio-Lib-master/requirements.txt` |
| 2 | **EVaR/EDaR 需要凸优化求解器在环**。`EVaR_Hist` 签名即带 `solver` 参数，内部候选求解器列表为 `["CLARABEL","MOSEK","COPT","SCS","ECOS"]`；且源码注释明确写道 `I recommend only use this function with MOSEK solver` —— MOSEK 是**商业求解器** | `RiskFunctions.py:556`、`:590`、`:652`、`:1059` |
| 3 | **实现路线**：CVaR / MDD / ADD / CDaR / UCI 全部为**历史样本的纯统计量**，用 numpy 即可精确实现，无需求解器。EVaR 列为**可选加严项**，若实现则用一维凸优化（`scipy.optimize.minimize_scalar` 对偶变量）自研，不引 cvxpy | 落地约束 |

> 落点：新增 `quant/research/risk_metrics.py`（P3，尚未创建）。

### 2.3 D1 通过标准

- 报告必须**同时**给出中心指标与尾部/回撤指标，缺一不可；
- **禁止只报 MDD 不报 CDaR** —— MDD 是单观测值，用它做策略排序等于用一次极端事件排序；
- 所有指标必须标注计算区间、样本数、α 取值；样本数不足（如 < 250 个观测）必须显式标注「样本不足，指标不可用于决策」，而不是照常输出一个数字。

---

## 3. 维度 2 · 泛化性

### 3.1 三层要求

| 层 | 要求 |
|---|---|
| **样本外（OOS）** | 参数在训练区间确定后**冻结**，测试区间不得调整。冻结必须是**机器可校验的**（比较参数哈希），不是靠一句注释承诺 |
| **Walk-forward** | 滚动切分为多个 fold，逐 fold 报告，并给出跨 fold 的**稳定性**（测试期指标的标准差 / 变异系数）。**只报平均值不报离散度视为不合格** |
| **分市场状态（regime）评估** | 借鉴 TradeMaster 的 market dynamics labeling 思想：把历史按市场状态切段，**逐段报告表现**，而不是只看一个总账 |

### 3.2 Walk-forward 必须含 purge 与 embargo

训练窗结束与测试窗开始之间**必须留出间隔**：

```
[--------- train ---------][ purge ][ embargo ][----- test -----]
                            ^^^^^^^   ^^^^^^^^
                            剔除标签   隔离期，防止
                            重叠区     滚动特征跨界泄漏
```

- **purge**：训练集中标签计算窗口与测试期重叠的样本必须剔除；
- **embargo**：测试期开始后的一小段也要从后续训练集中排除；
- **间隔长度 ≥ 策略使用的最长回看窗口**（例如用了 MA120，则间隔至少 120 个交易日）。

**紧邻切分（train_end 的下一根就是 test_start）在使用滚动特征时必然泄漏**，这是 walk-forward 最常见的失效方式。

### 3.3 分 regime 评估的具体形态

复用系统已有的 `MarketRegime` 五档枚举（`quant/domain/enums.py:101-107`：`TREND_RISK_ON` / `RANGE_NEUTRAL` / `STRESS_RISK_OFF` / `RECOVERY_PROBE` / `UNKNOWN`）。

评估输出必须是一张**逐 regime 的矩阵**，而非一行汇总：

| regime | 样本天数 | 年化收益 | CVaR(5%) | MDD | CDaR(5%) | UCI |
|---|---|---|---|---|---|---|
| TREND_RISK_ON | … | … | … | … | … | … |
| RANGE_NEUTRAL | … | … | … | … | … | … |
| STRESS_RISK_OFF | … | … | … | … | … | … |
| RECOVERY_PROBE | … | … | … | … | … | … |
| UNKNOWN | … | … | … | … | … | … |

**判定规则**：
1. 任一 regime 的样本天数 < 60 → 该行标注「样本不足」，且该策略**不得**声称「已在该状态下验证」；
2. 若策略的正收益**集中来自单一 regime**（例如 90% 收益来自 TREND_RISK_ON），必须在结论中显式声明「本策略是 X 状态下的策略」，不得表述为通用策略；
3. 任一 regime 下 CDaR 超出风险预算 → D2 不通过，无论总账多好看。

---

## 4. 维度 3 · 可解释性

### 4.1 硬约束

> **不可解释的模型不得进入决策链。** 这不是偏好，是准入条件。

每一个决策必须能回答：**「因为哪些输入、取了什么值、对比了什么阈值，所以得出这个结论」**。

### 4.2 决策解释契约（`DecisionExplanation`）

自由文本理由**不满足**本约束。理由必须结构化：

| 字段 | 说明 |
|---|---|
| `reason_code` | 枚举码（如 `DATA_QUALITY_BLOCKED` / `MOMENTUM_RANK_TOP_N` / `REGIME_GATE_REJECTED`），可被程序判定与统计 |
| `inputs` | 参与本条判断的输入清单，每项含 `{name, value, as_of, source_symbol, data_origin}` |
| `threshold` | 被比较的阈值及其配置来源（`config` 文件路径 + 键路径） |
| `comparison` | 实际比较式，如 `score(515000)=1.67 > threshold=0.50` |
| `conclusion` | 该条判断的结论 |
| `contribution` | 本条对最终权重的贡献（可为 0，但不可缺省） |

**可复现性要求**：给定同一份输入快照与同一份配置，重放决策必须得到**逐字节相同**的输出。任何引入随机性的组件都必须显式接受 seed，且 seed 写入运行档案（见 `docs/HISTORICAL_RUN_REGISTRY.md`）。

### 4.3 准入判定

| 模型类型 | 是否可进决策链 |
|---|---|
| 规则 / 阈值 / 排序类 | 可，需满足 §4.2 契约 |
| 线性模型、单棵浅决策树 | 可（系数/路径即解释），需满足 §4.2 契约 |
| 树集成（含特征重要度 + 单样本归因） | 需个案评审 |
| 深度模型 / 生成式模型 / 采样式推理 | **不可**，仅限研究层 |

---

## 5. 维度 4 · 失效模式

### 5.1 硬约束

> 任何策略进入 PAPER 之前，必须提交一张**失效模式卡（Failure Mode Card）**。**没有失效模式卡的策略不得进入 PAPER，更不得进入 LIVE。**

「我不知道它什么时候会失效」等价于「我不知道我在做什么」。

### 5.2 失效模式卡 schema

| 字段 | 要求 |
|---|---|
| `failure_hypotheses` | 列出**至少 3 条**具体失效假设。禁止写「市场剧烈波动时」这类无法检测的空话，必须可观测。例：「当动量因子在 60 日窗口内的 IC 由正转负并持续 10 日以上」 |
| `failure_signature` | 每条假设失效时**长什么样**：哪个指标、往哪个方向、量级多大。例：「换手率上升 2 倍以上而胜率跌破 40%」 |
| `detection_rule` | **可执行的检测规则**（能落成一个每日运行的函数），含指标、窗口、阈值 |
| `detection_lag` | 从失效发生到检测到，预计滞后多少个交易日。**这个数必须诚实**，它决定了最大可承受损失 |
| `response_action` | 检测到之后做什么：降级到 `PAPER` / 减仓 / 完全停用。必须是**自动触发**的，不能依赖人记得看报告 |
| `historical_precedent` | 历史上是否发生过、发生在哪一段、当时损失多少。若无先例必须写「无先例，本卡为先验推测」 |

### 5.3 失效检测必须常驻

失效检测不是上线前跑一次，而是**每次运行都跑**：检测结果写入运行档案的 `metrics`，连续 N 次触发即自动执行 `response_action`，并留下不可篡改的降级记录。

---

## 6. 关键约束 · 研究层与决策层物理隔离

### 6.1 隔离规则

| # | 规则 |
|---|---|
| I-1 | **外部预测模型只能产出研究结论，不得直接生成交易信号。** 模型输出进入 `model_targets` 账（研究），要变成决策必须经过决策引擎的显式规则，且该规则本身必须满足 §4 可解释性契约 |
| I-2 | **默认关闭**：新模型注册即 `disabled` + `L0` + `frozen`，需逐级治理提升 |
| I-3 | **生产门槛**：`enabled AND frozen AND trust_level >= L4_SHADOW` 三者同时满足才可被生产消费 |
| I-4 | **解冻必须过本框架**：任何模型解冻前，必须提交 D1–D4 四维评估结论 + 失效模式卡 |

### 6.2 Kronos 必须保持零信任冻结 —— 四条实证理由

参考源码：Kronos 项目。

| # | 阻断理由 | 证据 |
|---|---|---|
| **K-1** | **推理结果不确定，同样输入多次运行结果不同**。解码用 `torch.multinomial` 随机采样（两处），且**整个 `model/` 目录中不存在任何 `manual_seed` / `set_seed` / `random.seed` 调用**（grep 命中数 0），连"固定种子换取可复现"这条退路都没有。这直接违反 §4.2 的可复现性要求 —— 无法复盘 = 无法追责 | `Kronos-master/model/kronos.py:271` `torch.multinomial(s1_probs.view(-1, self.s1_vocab_size), 1)`；`:384` `x = torch.multinomial(probs, num_samples=1)` |
| **K-2** | **依赖 temperature / top_k / top_p 三个自由超参**，每一个都会改变输出分布，且没有任何一组取值是"正确"的。`sample_from_logits` 先做 `logits / temperature` 再做 top-k/top-p 截断；调用处默认 `T=1.0, top_k=0, top_p=0.99`（另一处默认 `top_p=0.9`）—— **同一个仓库里两个入口的默认值都不一致**，说明这些值本身就是随手选的 | `kronos.py:373-377` `sample_from_logits(logits, temperature, top_k, top_p, ...)`；截断实现 `:331-371` `top_k_top_p_filtering`；默认值 `:389` `top_p=0.99` vs `:519` `top_p=0.9` |
| **K-3** | **输入要求完整 OHLCV，字段根本不匹配**。模型硬编码 `price_cols = ['open','high','low','close']`，缺任一列直接 `raise ValueError`。而 V4.6 的场外基金数据**只有单点净值**：`data/fund_nav/*.csv` 的实际表头为 `date,nav,nav_basis,daily_return_pct,purchase_status,redemption_status,source,source_symbol,retrieved_at` —— **没有 open/high/low/close/volume 中的任何一个**。这不是"接口适配"能解决的问题：场外基金一天只有一个价，把 nav 复制四份填进 OHLC 是**伪造输入**，模型学到的日内形态在这种输入上没有任何意义 | 字段定义 `kronos.py:489-491`；硬校验 `kronos.py:524` 与 `:601` `if not all(col in df.columns for col in self.price_cols): raise ValueError`；实际数据表头 `quant_v4_6/data/fund_nav/000051.csv` 第 1 行 |
| **K-4** | **微调依赖 CUDA，本地无可复现训练条件**。训练脚本硬编码 `torch.device(f"cuda:{local_rank}")`（DDP 多卡），无 CPU 回退分支；测试脚本默认 `--device cuda:1`。没有训练条件 = 无法做 §3 要求的 walk-forward 与分 regime 验证 = 永远无法提升到 L4 | `Kronos-master/finetune/train_predictor.py:185`；`finetune/train_tokenizer.py:223`；`finetune/qlib_test.py:305` |

> **补充：即便解决了 K-1~K-4，Kronos 也过不了 D3。** 它是生成式自回归 Transformer，按 §4.3 属于「深度/生成式/采样式推理」，**结构上不可进决策链**。它唯一有价值的可能用法是作为**情景生成器**（生成大量可能路径用于压力测试），而不是点预测器 —— 这属于研究层，且需另行评估。

---

## 7. 当前实现现状（诚实盘点）

> 状态标签取值：`NOT_FOUND` / `PARTIAL` / `IMPLEMENTED_UNTESTED` / `TESTED_BUT_NO_PROD_CALLER` / `TESTED_AND_WIRED` / `BLOCKED`

### 7.1 维度 1 · 收益与风险

| 能力项 | 现状 | 证据(文件:行号) | 状态标签 |
|---|---|---|---|
| 基础收益/风险指标 | 实现了 7 个：`total_return` / `annual_return` / `max_drawdown` / `drawdown_duration_days` / `sharpe` / `sortino` / `calmar` | `quant/research/backtest.py:67-104`（返回字典 `:96-104`） | `PARTIAL` |
| MDD 最大回撤 | 有，`nav/cummax - 1` 取 min | `quant/research/backtest.py:77-79` | `PARTIAL` |
| 回撤持续天数 | 有 | `quant/research/backtest.py:80-87` | `PARTIAL` |
| **VaR** | 无 | 全仓库 `grep -rn "VaR"` 在 `quant/` 下命中数 0 | `NOT_FOUND` |
| **CVaR** | 无 | 同上 | `NOT_FOUND` |
| **EVaR** | 无 | 同上 | `NOT_FOUND` |
| **ADD 平均回撤** | 无 | 只有 `max_drawdown`，无回撤序列均值 | `NOT_FOUND` |
| **CDaR 条件回撤** | 无 | `quant/research/backtest.py:96-104` 返回字典无此项 | `NOT_FOUND` |
| **UCI 溃疡指数** | 无 | 同上 | `NOT_FOUND` |
| 成本纳入 | 有，回测模拟器含费率/滑点/最低佣金；场外基金按 T+2 未知价 | `quant/research/backtest.py:141-153`（ETF）、`:182-203`（基金） | `PARTIAL` |
| 样本不足时拒绝出指标 | **反着来的**：`len(nav) < 2` 时不是拒绝，而是**返回一组全 0 的指标**。下游拿到 `total_return=0.0` 无法区分「真的没赚钱」和「根本没数据」 | `quant/research/backtest.py:69-70` | `BLOCKED` |

### 7.2 维度 2 · 泛化性

| 能力项 | 现状 | 证据(文件:行号) | 状态标签 |
|---|---|---|---|
| Walk-forward 切分函数 | 存在。`create_walk_forward_folds(dates, train_size=252, test_size=126, step=126)` | `quant/research/walk_forward.py:31-44` | `TESTED_BUT_NO_PROD_CALLER` |
| Walk-forward 执行器 | 存在。`run_walk_forward()`。**调用方只有 `tests/test_research.py`**，无任何 `app/` 或 `quant/` 生产调用 | 定义 `quant/research/walk_forward.py:46-74`；调用方仅 `tests/test_research.py:7,54,59` | `TESTED_BUT_NO_PROD_CALLER` |
| **purge / embargo** | 完全没有。`test` 切片起点就是 `start + train_size`，**与 train 末尾紧邻，零间隔**。策略若使用滚动窗口特征（系统里有 MA120），训练末尾的特征已经"看见"了测试期开头的数据 | `quant/research/walk_forward.py:36-37`；全仓库 `grep -rni "purged\|embargo"` 命中数 0 | `NOT_FOUND` |
| **参数冻结的真实性** | **是装饰性的，不是真的**。两个问题：(1) `strategy_fn` 在 train 与 test 上被调用时传入的是**同一个 `settings` 对象**，训练阶段并不产出任何参数，所谓"冻结"没有冻结任何东西；(2) `parameters_frozen` 只被赋值为一句中文说明字符串 | `quant/research/walk_forward.py:58` 与 `:61` 均传 `settings`；`:60` `fold.parameters_frozen = {"freeze_note": "参数已在训练集确定，测试集不调整"}` | `BLOCKED` |
| **参数冻结校验** | **该断言永远不可能失败**。`assert_parameters_frozen` 断言的是 `fold.parameters_frozen is not None`，而该字段的定义是 `field(default_factory=dict)` —— 默认值是 `{}`，是空字典而非 `None`。**无论如何都过**。这是一个提供虚假安全感的空校验 | 断言 `quant/research/walk_forward.py:76-78`；字段定义 `:20` | `BLOCKED` |
| 跨 fold 稳定性 | 有，报告 `average_test_metric` / `std_test_metric` / `stability_score` | `quant/research/walk_forward.py:67-72` | `TESTED_BUT_NO_PROD_CALLER` |
| **稳定性只看单指标** | 稳定性是基于 `total_return` 一个指标算的（`metrics.get("total_return", 0)`），风险指标的跨 fold 稳定性完全没有 | `quant/research/walk_forward.py:69` | `PARTIAL` |
| **实验选优只看收益** | `best_trial(metric="total_return")` —— 默认按总收益取最大值。**这正是本文档要防止的行为，而它是系统里唯一的选优函数** | `quant/research/walk_forward.py:117-119` | `BLOCKED` |
| 另一套滚动窗口对比 | `_walk_forward_summary()` 是**质量更高的实现**：真实滚动窗口、三策略横向对比、按 total_return/max_drawdown/sortino 三个维度各自计胜场、fold 数不足时返回 `INSUFFICIENT_FOLDS` 而非硬出结论。且源码注释诚实说明"规则是固定的，前置训练窗只提供历史路径，不是拟合参数"。**但它连测试都没有调用方** —— 其宿主函数 `run_strategy_comparison` 在全仓库零调用（测试只 import 了 `_metric_winner_many` 和 `STRATEGY_LABELS` 两个辅助符号） | 实现 `quant/fund_strategy_comparison.py:481-533`；诚实注释 `:493-495`；宿主调用点 `:754`；全仓库调用方 grep 仅 `tests/test_safety_phase1.py:7`（未调用主函数） | `IMPLEMENTED_UNTESTED` |
| **分 regime 评估** | **字段存在，写入方为零**。`BacktestResult.regime_metrics: dict[str, float]` 已声明，但全仓库 `grep -rn "regime_metrics"` **只有这一处命中 —— 即定义处本身，没有任何代码给它赋过值**。它永远是空字典。更糟的是 `docs/research/V46_BACKTEST_STANDARD.md` 的字段表里把它列为「市场状态分解」，读文档的人会以为这个能力存在 | 定义 `quant/research/backtest.py:48`；零写入（grep 全仓库仅 1 处命中）；误导性文档 `docs/research/V46_BACKTEST_STANDARD.md` 字段表 `regime_metrics` 行 | `NOT_FOUND` |
| `MarketRegime` 枚举 | 五档枚举存在，可直接复用 | `quant/domain/enums.py:101-107` | `TESTED_BUT_NO_PROD_CALLER` |
| **回测可复现性字段** | `dataset_version` 与 `config_hash` **两个字段同样零写入**（grep 全仓库各只命中定义处 1 次），永远是空串。`code_version` 更糟：它是硬编码字面量 `"V4.6"`，不是 git commit —— 换句话说所有回测结果都自称同一个"版本" | `quant/research/backtest.py:28`（硬编码）、`:29-30`（零写入） | `NOT_FOUND` |

### 7.3 维度 3 · 可解释性

| 能力项 | 现状 | 证据(文件:行号) | 状态标签 |
|---|---|---|---|
| 决策带理由 | 有，但是**中文自由文本列表**。实证（真实运行产物）：核心基金 `reasons` 为 `["000217 数据异常已隔离：本轮不参与排名、不新增申购…", "数据质量阻断：['005658']"]`；战术 ETF `reasons` 为 `["市场状态: RISK_ON", "研究池: 6, 合格池: 6, 实盘池: 6", "趋势策略: 趋势跟随: 选取 3 只 ETF", "Top5 排名: 515000(1.67), 510300(0.36), …"]` | `artifacts/2026-08-01/20260801-050102-daily-02d1810/daily_paper.json` 内 `core_fund.reasons` 与 `tactical_etf.reasons` | `PARTIAL` |
| `reason_code` 枚举 | 无。全部是自由文本，无法程序化统计「过去 90 天有多少次因数据质量被阻断」 | 全仓库 `grep -rn "reason_code\|def explain\|rationale\|explanation"` 在 `quant/` 下命中数 0 | `NOT_FOUND` |
| 输入溯源到值 | 无。理由里能看到**结果**（`515000(1.67)`），看不到**produce 这个 1.67 的输入是哪几个数、as_of 是哪天**。三个月后无法重算这个 1.67 | 同上产物 | `NOT_FOUND` |
| 阈值与配置来源 | 无。比较式与阈值不出现在解释中 | 同上产物 | `NOT_FOUND` |
| 贡献度分解 | 无 | 同上 | `NOT_FOUND` |
| 决策可重放 | 无重放入口。且由于运行档案缺配置快照与数据批次哈希（见 `docs/HISTORICAL_RUN_REGISTRY.md` §2.2），**即使写了重放器也没有足够输入去重放** | `docs/HISTORICAL_RUN_REGISTRY.md` §2.2 | `NOT_FOUND` |
| 特征快照 ID 字段 | 字段存在 `Signal.feature_snapshot_id`，特征存储器也存在 | `quant/domain/signals.py:35,57`；`quant/feat/store.py:13,37-40` | `TESTED_BUT_NO_PROD_CALLER` |

> **附带发现（与 D3 直接相关）**：同一次真实运行中，核心基金引擎判定 `regime = "NEUTRAL"`（理由：「基准场外基金净值不可用或未通过质量校验；不强行生成市场状态」），而战术 ETF 引擎在同一时刻判定 `regime_label = "RISK_ON"`。**两个引擎对同一天的市场状态给出互相矛盾的结论，且系统没有任何机制发现或记录这个矛盾**。这正是 `ARCHITECTURE_INFLUENCE_MATRIX.md` §8.2 GAP-2 的现场证据。

### 7.4 维度 4 · 失效模式

| 能力项 | 现状 | 证据(文件:行号) | 状态标签 |
|---|---|---|---|
| 失效模式卡 | 无此概念 | 全仓库 `grep -rni "failure_mode\|strategy_decay\|degradation_detect\|invalidation"` 命中数 **0** | `NOT_FOUND` |
| 失效检测规则 | 无 | 同上 | `NOT_FOUND` |
| 检测滞后声明 | 无 | 同上 | `NOT_FOUND` |
| 自动降级响应 | 部分相关物：`StrategyLifecycle` 有 `SUSPENDED` 态且定义了迁移规则；`ModelRegistry.disable()` 提供 kill-switch。但**没有任何自动触发它们的检测逻辑** —— 开关有，没接线 | 生命周期 `quant/domain/enums.py:79-98`；kill-switch `quant/research_models/registry.py:80-82` | `PARTIAL` |
| 治理复核门槛 | 有 `min_out_of_sample_decisions_before_review` 配置项，按样本外决策次数触发复核 | `quant/governance.py:24`、`quant/fund_governance.py:24` | `PARTIAL` |

### 7.5 研究/决策隔离与 Kronos 冻结

**必须如实肯定：冻结是真的落地了，不是口头承诺。** 三层 Fail-Closed：

| 能力项 | 现状 | 证据(文件:行号) | 状态标签 |
|---|---|---|---|
| Kronos 默认不可调用 | **是**。`KronosModel.predict()` 无条件 `raise ModelNotConnectedError`，方法体内没有任何分支能走通 | `quant/research_models/kronos.py:48-52` |`IMPLEMENTED_UNTESTED`（有测试覆盖，见下） |
| `is_frozen` 恒真 | **是**。property 直接 `return True`，**没有 setter**，无法从外部改成 False | `quant/research_models/kronos.py:44-46` | `TESTED_BUT_NO_PROD_CALLER` |
| 注册即 L0+冻结+禁用 | **是**。`ModelRegistryEntry` 默认 `trust_level=L0_UNVERIFIED, is_frozen=True, is_enabled=False`；`register_kronos` 额外写入禁用理由 | `quant/research_models/registry.py:39-41`、`:122-128`；`kronos.py:55-62` | `TESTED_BUT_NO_PROD_CALLER` |
| 生产门槛三重与 | **是**。`is_production_eligible = enabled AND frozen AND trust>=L4`；`require_production()` 拿不到就抛错 | `quant/research_models/registry.py:47-53`、`:149-157`；门槛定义 `quant/domain/enums.py:145-148` | `TESTED_BUT_NO_PROD_CALLER` |
| 条目不可变 | **是**。`ModelRegistryEntry` 是 `@dataclass(frozen=True)`，promote/enable 都返回**新对象**并追加 audit_trail | `quant/research_models/registry.py:33-34`、`:55-57` | `TESTED_BUT_NO_PROD_CALLER` |
| 测试覆盖 | 有，且验收脚本也覆盖 | `tests/test_research_models.py:117-140`；`app/v46_acceptance.py:262-269` | `TESTED_BUT_NO_PROD_CALLER` |

**但冻结机制本身有三个真实漏洞：**

| # | 漏洞 | 证据(文件:行号) | 状态标签 |
|---|---|---|---|
| L-1 | **注册表是纯内存字典，无持久化**。`self._entries: dict` 随进程消亡；`audit_trail` 记录的所有提升/禁用历史**进程一退出全部丢失**。「提升留痕便于审计」这句 docstring 承诺在当前实现下不成立 | `quant/research_models/registry.py:102-103`；无任何落盘代码 | `PARTIAL` |
| L-2 | **`promote` 允许一步从 L0 跳到 L6，且不要求任何证据**。唯一校验是「不能降级」（`if int(level.value) < int(self.trust_level.value): raise`），没有「必须逐级」、没有「必须附评估报告 ID」。`promote(L6_PROVEN, actor="me")` 直接通过，`actor` 是任意字符串 | `quant/research_models/registry.py:59-66` | `PARTIAL` |
| L-3 | **`is_frozen` 在门槛里是"与"不是"或"，语义反直觉**。`is_production_eligible` 要求 `is_frozen == True`，即**冻结的模型才能进生产**（这里 frozen 表示「权重已固定不再训练」）。而 Kronos 文档语境里的"冻结"表示「禁止使用」。同一个词在同一个模块里承载两种相反含义，是未来误操作的温床 | `quant/research_models/registry.py:47-53` vs `kronos.py:44-46` 的注释语境 | `PARTIAL` |

### 7.6 `docs/research/` 已有内容

| 文件 | 内容 | 与现实的偏差 |
|---|---|---|
| `docs/research/V46_BACKTEST_STANDARD.md` | 列出 `BacktestResult` 字段表与 ETF/基金回测规则、6 条禁止行为 | **字段表把 `regime_metrics`（市场状态分解）、`dataset_version`、`config_hash` 列为已有能力，但这三个字段在代码中零写入**（§7.2）。且 `code_version` 被描述为「代码版本（V4.6）」，实际是硬编码字面量而非 git commit |
| `docs/research/V46_WALK_FORWARD_STANDARD.md` | 描述窗口划分、fold 记录、稳定性评估、参数实验注册表 | **窗口图示为 `训练集(252日) → 验证集(0日) → 测试集(126日)`，明确写出验证间隔为 0 日**，即文档层面就承认了无 purge/embargo。「训练集：参数优化」这句与代码不符 —— 代码从不优化参数（§7.2）。「测试窗口参数不得在测试期间调整」这条约束**无任何机器校验**（`assert_parameters_frozen` 恒真） |
| 覆盖缺口 | **无任何文档涉及尾部风险指标、分 regime 评估、可解释性契约、失效模式** | 本文档补齐 |

---

## 8. 差距与落地计划

### P0 · 账户与数据事实

| # | 任务 | 对应缺口 |
|---|---|---|
| P0-1 | **修掉恒真断言**：`assert_parameters_frozen`（`walk_forward.py:76-78`）改为比较训练前后的参数哈希，不一致即抛错 | §7.2 |
| P0-2 | **样本不足改为拒绝**：`compute_performance_metrics`（`backtest.py:69-70`）在样本不足时返回 `status=INSUFFICIENT_SAMPLE` 并拒绝给数，不再返回全 0 | §7.1 |
| P0-3 | **纠正误导性文档**：在 `V46_BACKTEST_STANDARD.md` / `V46_WALK_FORWARD_STANDARD.md` 中把零写入字段与无校验约束显式标注为「未实现」 | §7.6 |

### P1 · 连续运行与市场感知

| # | 任务 |
|---|---|
| P1-1 | 实现 `DecisionExplanation` 结构化解释对象 + `reason_code` 枚举，替换自由文本 `reasons` |
| P1-2 | 决策解释接入运行档案，每条解释携带 `inputs[].as_of` 与 `data_origin` |
| P1-3 | 建立跨引擎 regime 一致性校验：核心基金与战术 ETF 判定冲突时必须显式记录并告警（§7.3 附带发现） |

### P2 · 风险与组合

| # | 任务 |
|---|---|
| P2-1 | 新建 `quant/research/risk_metrics.py`，用 numpy 实现 VaR / CVaR / ADD / CDaR / UCI（不引 Riskfolio），EVaR 列为可选加严项 |
| P2-2 | 把上述指标接入 `BacktestResult` 与 `compute_performance_metrics` |
| P2-3 | 把 CDaR / UCI 纳入风险预算约束，替代当前以 MDD 单点值做判断的做法 |

### P3 · 研究基础设施

| # | 任务 |
|---|---|
| P3-1 | Walk-forward 加入 purge + embargo 参数，间隔默认取「策略最长回看窗口」并强制非零 |
| P3-2 | 实现分 regime 评估：真正给 `regime_metrics`（`backtest.py:48`）写值，输出 §3.3 的逐 regime 矩阵 |
| P3-3 | 回测可复现性：`config_hash` / `dataset_version` 真实写入；`code_version` 改取 git commit（与 `docs/HISTORICAL_RUN_REGISTRY.md` P0-3 共用实现） |
| P3-4 | 多目标选优替换 `best_trial(metric="total_return")`（`walk_forward.py:117-119`）：改为帕累托前沿 + 四维合取门槛 |
| P3-5 | 打通 `run_strategy_comparison`（`fund_strategy_comparison.py:754`）的调用链，让唯一一份高质量滚动对比实现真正被跑到 |
| P3-6 | 研究模型注册表持久化（修 L-1）；`promote` 强制逐级 + 必须附评估报告 ID（修 L-2）；`is_frozen` 更名消歧（修 L-3） |
| P3-7 | 实现失效模式卡 schema + 常驻检测器 + 自动降级接线（接到已有的 `StrategyLifecycle.SUSPENDED` 与 `ModelRegistry.disable()`） |

### P4 · 未来扩展

| # | 任务 |
|---|---|
| P4-1 | Kronos 作为**情景生成器**（非点预测器）的可行性评估。前置条件：先解决 K-1（确定性）与 K-3（字段不匹配），且**仅限研究层** |
| P4-2 | 因子 IC 衰减监控作为通用失效检测器 |

---

## 9. 验收方式

### 9.1 D1 收益与风险

- **AC-RESEVAL-01**：给定一条 NAV 序列，当调用绩效计算时，则返回结果**同时包含** `cvar_5` / `mdd` / `add` / `cdar_5` / `uci` 五个键，缺任一键即失败。
- **AC-RESEVAL-02**：给定一条已知的构造序列（含 3 段回撤），当计算 UCI 与 CDaR 时，则结果与独立手工计算值在 1e-6 内一致。
- **AC-RESEVAL-03**：给定样本数 < 250 的 NAV 序列，当计算绩效时，则返回 `status="INSUFFICIENT_SAMPLE"` 且**不返回任何指标数值**。（当前行为：返回全 0 指标 —— 必须变红。）
- **AC-RESEVAL-04**：给定项目依赖清单，当检查时，则**不存在** `riskfolio-lib` / `cvxpy` / `vectorbt` 依赖项。

### 9.2 D2 泛化性

- **AC-RESEVAL-05**：给定策略最长回看窗口为 K 日，当生成 walk-forward folds 时，则每个 fold 的 `test_start` 与 `train_end` 之间的间隔 **≥ K 个交易日**；间隔为 0 时构造函数抛异常。
- **AC-RESEVAL-06**：给定一次 walk-forward 运行，当测试期参数与训练期冻结参数不一致时，则 `assert_parameters_frozen` **抛异常**。（当前该断言恒真，必须能造出一个失败用例。）
- **AC-RESEVAL-07**：给定一次回测，当读取 `BacktestResult.regime_metrics` 时，则它**非空**，且其键集合是 `MarketRegime` 取值的子集，且各 regime 天数之和等于回测总天数。
- **AC-RESEVAL-08**：给定某 regime 的样本天数 < 60，当生成评估报告时，则该 regime 行标注「样本不足」，且策略结论中**不得出现**「已在该状态下验证」字样。
- **AC-RESEVAL-09**：给定一组参数实验，当调用选优函数时，则返回的是**帕累托前沿集合**而非单一「收益最高者」；且任何未通过 D2/D3/D4 门槛的实验**不出现在候选集中**。
- **AC-RESEVAL-10**：给定两次使用相同数据与配置的回测，当比对 `config_hash` 与 `dataset_version` 时，则二者相等且**均非空串**。

### 9.3 D3 可解释性

- **AC-RESEVAL-11**：给定一次决策产出，当遍历其解释条目时，则每条都含非空 `reason_code`（枚举值）、非空 `inputs`（每项带 `value` 与 `as_of`）、非空 `comparison`。**自由文本条目数为 0。**
- **AC-RESEVAL-12**：给定一次历史运行的档案，当用该档案重放决策时，则输出与原决策**逐字节一致**。
- **AC-RESEVAL-13**：给定同一天的核心基金与战术 ETF 判定，当两者 regime 不一致时，则系统产生显式告警并写入运行档案。（当前：`NEUTRAL` vs `RISK_ON` 静默共存。）

### 9.4 D4 失效模式

- **AC-RESEVAL-14**：给定任一策略申请从 `BACKTESTED` 提升到 `PAPER`，当缺少失效模式卡时，则提升被拒绝。
- **AC-RESEVAL-15**：给定一张失效模式卡，当校验时，则 `failure_hypotheses` 条数 ≥ 3，且每条都有可执行的 `detection_rule` 与明确的 `detection_lag` 数值。
- **AC-RESEVAL-16**：给定注入一段满足某条失效签名的历史数据，当运行检测器时，则该失效被检出，且在连续 N 次触发后**自动**执行 `response_action`（无需人工干预）。

### 9.5 研究/决策隔离

- **AC-RESEVAL-17**：给定 `KronosModel`，当调用 `predict()` 时，则**必定**抛 `ModelNotConnectedError`，不存在任何参数组合能让它返回预测结果。
- **AC-RESEVAL-18**：给定默认注册表，当查询 `production_eligible()` 时，则返回空元组。
- **AC-RESEVAL-19**：给定尝试对某模型执行 `promote(L0 → L6)`，当未附评估报告 ID 时，则抛异常；且**逐级提升**（L0→L1→…）是唯一合法路径。
- **AC-RESEVAL-20**：给定注册表发生一次 promote/disable，当进程重启后重新加载注册表时，则该次操作及其 `audit_trail` **仍然存在**。（当前：内存字典，重启即丢 —— 必须变红。）
- **AC-RESEVAL-21**：给定生产决策链的全部代码路径，当静态扫描时，则**不存在**从 `quant/research_models/` 到 `quant/decision/` 的直接调用边。

---

## 10. 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-08-01 | v1.0 | 初版。确立四维合取评估矩阵（收益风险/泛化性/可解释性/失效模式）；定义尾部与回撤指标集并声明「借鉴 Riskfolio 定义、不引其依赖」；定义决策解释契约与失效模式卡 schema；以四条实证证据确认 Kronos 零信任冻结；盘点发现 `regime_metrics`/`config_hash`/`dataset_version` 三字段零写入、`assert_parameters_frozen` 恒真、walk-forward 无 purge/embargo、`best_trial` 只按收益选优、研究模型注册表无持久化等缺陷 |
