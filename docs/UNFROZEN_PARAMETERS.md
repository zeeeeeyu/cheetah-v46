# V4.6 未冻结参数登记表（Unfrozen Parameters Registry）

> 文档编号：PARAM-REGISTRY-001
> 版本：v1.0
> 生成日期：2026-08-01
> 对应需求：补充约束 Prompt 第二十二节「区分已冻结的系统原则和仍需回测验证的参数」
> 关联文档：`docs/V46_REQUIREMENTS_TRACEABILITY.md`（REQ-PARAM-01~04）

---

## 0. 本文档的作用

区分两类东西，并对第二类设禁令：

| 类别 | 定义 | 谁可以改 |
|---|---|---|
| **已冻结的系统原则** | 架构级不变量，改动等于改系统定义（如「账户事实优先」「实盘 Fail-Closed」「研究层不得直接下单」） | 只能由人显式决策 + 文档变更 |
| **未冻结的参数** | 数值阈值、窗口长度、权重上限等，取值应由回测/实证决定 | **Coding Agent 一律不得自行决定** |

### 0.1 最高禁令

> **Coding Agent 不得为任何未冻结参数「顺手填一个看起来合理的值」。**
> 遇到必须有值才能跑通的情况，只能填入标记为 `PROVISIONAL` 的占位值，并在本表登记；**不得**在提交信息、报告或验收中把 `PROVISIONAL` 值描述为「已调优」「合理默认」。

这条禁令的由来：下表 §3 中大量参数目前来源不明，无法回答「这个 0.42 是怎么来的」。

### 0.2 参数来源分类（4 档）

| 标签 | 含义 | 可否用于实盘 |
|---|---|---|
| `FROZEN` | 已冻结，经决策确认，改动需走变更流程 | 可 |
| `BACKTESTED` | 有回测/实证依据，且依据可追溯 | 可 |
| `LEGACY` | 历史遗留值，从 V4.2/V4.5.1 继承，来源已不可考 | **需复核** |
| `PROVISIONAL` | 临时默认值，纯粹为了让代码能跑，无任何依据 | **不可** |

### 0.3 现状统计

| 分类 | 条数 | 说明 |
|---|---:|---|
| `FROZEN` | 8 | 主要是治理与安全开关 |
| `BACKTESTED` | 0 | **没有任何参数附带可追溯的回测依据** |
| `LEGACY` | 27 | 从旧系统继承，来源不可考 |
| `PROVISIONAL` | 18 | 硬编码魔数或占位值 |
| **合计** | **53** | |

> **诚实结论**：`BACKTESTED = 0`。系统当前**没有任何一个数值参数**能拿出回测依据。所有决策阈值要么是继承的，要么是拍的。这不代表它们一定错，但代表它们**都不能被称为「已验证」**。

---

## 1. 已冻结的系统原则（不是参数，禁止当参数调）

| # | 原则 | 载体 | 冻结理由 |
|---|---|---|---|
| FR-1 | 账户事实优先：`actual_positions` 只能由 `RECONCILIATION` 来源写入 | `quant/ledgers/guards.py:18-25` | 一旦允许其他来源写入，「实际持仓」就变成猜测，全部风控失去基准 |
| FR-2 | 实盘 Fail-Closed：默认 PAPER，实盘需显式授权 | `config/runtime.json` `default_environment=PAPER`、`allow_live_order_generation=false` | 见影响矩阵 S-X4：StockSharp 用字符串路由模拟/实盘，拼错即打实盘 |
| FR-3 | 研究层与决策层物理隔离，模型输出不得直接下单 | `quant/research_models/` | 见影响矩阵 K-1 |
| FR-4 | Kronos 零信任冻结 | `quant/research_models/` | 采样非确定性，无法复盘（影响矩阵 §5.3） |
| FR-5 | 未知价原则：当日净值不参与份额估算，`estimated_quantity=0` | `quant/exe/order_factory.py:54-55` | 场外基金 T+1 未知价，反推份额是伪精确 |
| FR-6 | 订单状态迁移 Fail-Closed，无绕过开关 | `quant/fund_orders/states.py:157-169` | 见影响矩阵 S-X1：「可选的严格」等于「没有严格」 |
| FR-7 | 风险簇是先验经济约束，不由统计相关性聚类生成 | `config/risk/risk_clusters.json`（人工 15 簇） | 见影响矩阵 R-X1：相关性在压力期收敛到 1，统计聚类恰在最需要时失效 |
| FR-8 | 人工审批必需 | `settings.json` `execution.manual_approval_required=true`、`fund/settings.json` 同项 + `pre_trade_confirmation_required=true` | 人在回路是 V4.6 的定义性特征 |

> 上述 8 条**不进入回测调参范围**。任何试图把它们参数化的改动都应被拒绝。

---

## 2. 明确留空的参数（做得对，保持）

这几个参数**故意为 null / false**，是正确的 Fail-Closed 设计，不要「补全」它们：

| 参数 | 位置 | 当前值 | 为什么必须留空 |
|---|---|---|---|
| `default_subscription_fee_rate` | `config/fund/settings.json` | `null` | 申购费率随销售平台/份额类别变化，猜测会导致成本估算系统性错误 |
| `default_redemption_fee_rate` | `config/fund/settings.json` | `null` | 赎回费是阶梯式（按持有天数），无法用单一默认值表达 |
| `allow_limited_purchase` | `config/fund/settings.json` | `false` | 限购状态必须实时核验，默认放行会产出无法执行的计划 |
| `allow_network_refresh`（ETF） | `config/settings.json` | `false` | ETF 侧走本地数据，禁止运行期联网 |
| `c_class_holding_days_note` | `config/fund/settings.json` | 文字说明 | 显式声明「系统不自动猜测赎回费」，这是正确姿态 |

---

## 3. 未冻结参数登记表

### 3.1 数据质量类（P0 · 最高优先级复核）

| # | 参数 | 位置 | 当前值 | 分类 | 问题 |
|---|---|---|---|---|---|
| UP-01 | `max_staleness_trading_days` | `config/settings.json` `data` | `0` | `LEGACY` | **声明了但生产路径不读**：`quant/pipeline.py:46` 会传，但真正跑的 `quant/strategies/tactical_etf/engine.py:84-87` **没有传这个参数**。配置形同虚设 —— 这正是 GAP-2 的配置层根因 |
| UP-02 | `max_staleness_calendar_days` | `config/fund/settings.json` `data` | `4` | `LEGACY` | 基金侧确有传入（`fund_pipeline.py:167-170`），但 4 天这个值来源不明；且**实测 29 天陈旧数据仍出信号**，说明该校验未覆盖战术分支 |
| UP-03 | `max_single_day_return_abs` | 两个 settings | `0.20` | `LEGACY` | 20% 单日异常阈值，来源不可考。对 QDII/杠杆类是否合适未验证 |
| UP-04 | `minimum_history_days` | 两个 settings | `126` | `LEGACY` | 与 `EligibilityCriteria.min_history_days` 默认 `252` **冲突**（`eligibility.py:98`），两个值差一倍 |
| UP-05 | `min_median_turnover_20d` | `config/settings.json` `data` | `100000000`（1 亿） | `LEGACY` | 与 `eligibility.py:99` 硬编码默认 `10_000_000`（1000 万）**差 10 倍**。当前 `engine.py:86` 会覆盖，但默认值是地雷 |
| UP-06 | `quality_window_trading_days` | `config/fund/settings.json` | `180` | `LEGACY` | 来源不明 |
| UP-07 | `min_data_completeness` | `eligibility.py:102` **硬编码** | `0.95` | `PROVISIONAL` | 不在配置里，`engine.py` 未传，静默用默认值 |
| UP-08 | `max_refresh_attempts_per_day` | `config/fund/settings.json` | `2` | `PROVISIONAL` | 拍的 |
| UP-09 | `min_refresh_retry_minutes` | `config/fund/settings.json` | `120` | `PROVISIONAL` | 拍的 |
| UP-10 | `request_interval_seconds` | `config/fund/settings.json` | `1.0` | `PROVISIONAL` | 限速值，拍的 |

### 3.2 信号类（P1）

> 注意：`signals` 区块在 `config/settings.json` 与 `config/fund/settings.json` 中**完全重复且取值相同**。两份配置各自演化会静默产生漂移，属结构性隐患。

| # | 参数 | 当前值 | 分类 | 问题 |
|---|---|---|---|---|
| UP-11 | `short_window` | `20` | `LEGACY` | 双份重复 |
| UP-12 | `medium_window` | `60` | `LEGACY` | 双份重复 |
| UP-13 | `long_window` | `120` | `LEGACY` | 双份重复 |
| UP-14 | `fi_window` | `60` | `LEGACY` | 双份重复 |
| UP-15 | `minimum_absolute_momentum` | `0.0` | `LEGACY` | 阈值为 0 意味着该过滤事实上不生效，是有意还是遗留？ |
| UP-16 | `minimum_trend_quality` | `0.42` | `LEGACY` | **典型「来源不明的精确值」**：为什么是 0.42 而不是 0.4？无依据 |
| UP-17 | `minimum_rank_margin` | `0.08` | `LEGACY` | 同上 |
| UP-18 | `require_two_day_stability` | `true` | `LEGACY` | 二日稳定确认，是简易迟滞的雏形，但与 REQ-STATE-05 要求的完整迟滞机制不同 |

### 3.3 组合与风控类（P2）

| # | 参数 | 位置 | 当前值 | 分类 | 问题 |
|---|---|---|---|---|---|
| UP-19 | `max_selected_assets` | 两处 | `3` | `LEGACY` | 双份重复 |
| UP-20 | `max_single_weight` | ETF `0.45` / 基金 `0.40` | 两处 | `LEGACY` | **两侧不一致**，差异理由无记录 |
| UP-21 | `max_pair_correlation` | 两处 | `0.85` | `LEGACY` | 与 FR-7（风险簇为先验约束）存在职责重叠，关系未理清 |
| UP-22 | `max_sector_weight` | 两处 | `0.60` | `LEGACY` | — |
| UP-23 | `neutral_equity_cap` | 两处 | `0.50` | `LEGACY` | 该值与「慢状态决定战略仓位区间」（REQ-STATE-01）应联动，目前无联动 |
| UP-24 | `risk_off_equity_cap` | 两处 | `0.20` | `LEGACY` | 同上 |
| UP-25 | `min_fund_size` | `eligibility.py:100` **硬编码** | `100_000_000` | `PROVISIONAL` | 不在配置，未传入 |
| UP-26 | `max_bid_ask_spread` | `eligibility.py:101` **硬编码** | `0.005` | `PROVISIONAL` | 不在配置，未传入 |
| UP-27 | `max_premium_discount` | `eligibility.py:102` **硬编码** | `0.02` | `PROVISIONAL` | 不在配置，未传入 |
| UP-28 | `max_lot_amount` | `eligibility.py:103` **硬编码** | `50_000` | `PROVISIONAL` | 不在配置，未传入 |

> **UP-25~28 + UP-07 合计 5 个参数**：`EligibilityCriteria` 共 7 个字段，而 `engine.py:84-87` **只传了 2 个**，其余 5 个全部静默使用硬编码默认值。这些值既不在配置里，也不在 run 快照里，等于**不可见、不可审计、不可复现**。

### 3.4 执行类（P1）

| # | 参数 | 位置 | 当前值 | 分类 | 问题 |
|---|---|---|---|---|---|
| UP-29 | `tolerance_band` | 两处 | `0.05` | `LEGACY` | ETF 侧该检查因 `pipeline.py:102` 的 `UnboundLocalError`（GAP-6）**从未真正执行过** |
| UP-30 | `max_non_emergency_turnover` | ETF `0.35` / 基金 `0.25` | 两处 | `LEGACY` | 两侧不一致，差异理由无记录 |
| UP-31 | `estimated_one_way_cost` | `config/settings.json` | `0.0015` | `LEGACY` | 15bp 单边成本估计，无依据 |
| UP-32 | `order_submission_lag_trading_days` | `config/fund/settings.json` | `1` | `LEGACY` | 与 T+1 机制一致，但未按日历组区分（REQ-CAL-05） |
| UP-33 | `purchase_confirmation_lag_trading_days` | 基金 | `2` | `LEGACY` | QDII 应更长，未区分 |
| UP-34 | `redemption_confirmation_lag_trading_days` | 基金 | `2` | `LEGACY` | 同上 |
| UP-35 | `scheduled_rebalance_interval_trading_days` | 两处 | `20` | `LEGACY` | 与 §3.6 复核节奏的 20 重复定义 |

### 3.5 市场状态类（P1）

| # | 参数 | 位置 | 当前值 | 分类 | 问题 |
|---|---|---|---|---|---|
| UP-36 | `atr_window` | `config/settings.json` `regime` | `20` | `LEGACY` | — |
| UP-37 | `chandelier_multiple` | `config/settings.json` `regime` | `3.0` | `LEGACY` | 吊灯止损倍数，经典默认值，未针对本资产池验证 |
| UP-38 | `hmm_min_history_days` | 两处 | `252` | `LEGACY` | — |
| UP-39 | `hmm_shadow_only` | 两处 | `true` | `FROZEN`(建议) | HMM 仅影子运行，建议升为冻结原则（等同 FR-3 的精神） |

### 3.6 复核节奏类（P1）

| # | 参数 | 当前值 | 分类 | 问题 |
|---|---|---|---|---|
| UP-40 | `stable_risk_on_strong_interval_nav_days` | `20` | `LEGACY` | 配置内注释说明了意图，但值本身无依据 |
| UP-41 | `neutral_or_weak_interval_nav_days` | `10` | `LEGACY` | — |
| UP-42 | `risk_off_interval_nav_days` | `3` | `LEGACY` | — |
| UP-43 | `data_retry_interval_business_days` | `1` | `PROVISIONAL` | — |
| UP-44 | `suggested_run_time` | `"21:00"` | `PROVISIONAL` | 运维约定，非策略参数，可保留 |

### 3.7 治理与研究类（P3）

| # | 参数 | 当前值 | 分类 | 问题 |
|---|---|---|---|---|
| UP-45 | `min_out_of_sample_decisions_before_review` | `30` | `LEGACY` | 双份重复 |
| UP-46 | `min_paper_days_before_review` | `120` | `LEGACY` | 双份重复 |
| UP-47 | `minimum_common_days`（回测） | `252` | `LEGACY` | — |
| UP-48 | `minimum_common_days_research_ready` | `504` | `LEGACY` | — |
| UP-49 | `train_nav_observations` | `252` | `PROVISIONAL` | walk-forward 切分，无依据 |
| UP-50 | `test_nav_observations` | `126` | `PROVISIONAL` | 同上 |
| UP-51 | `step_nav_observations` | `126` | `PROVISIONAL` | 同上 |
| UP-52 | `minimum_folds_for_interpretation` | `3` | `PROVISIONAL` | 3 折偏少，统计意义弱 |
| UP-53 | `top2_dynamic_exposure`（1.0 / 0.75 / 0.2） | 三值 | `PROVISIONAL` | 配置内已注明「仅用于研究比较」，姿态正确 |

---

## 4. 结构性问题（不是单个参数，是配置架构）

| # | 问题 | 证据 | 影响 |
|---|---|---|---|
| CFG-1 | **配置声明了但代码不读** | `max_staleness_trading_days` 在 `config/settings.json` 中为 `0`，但生产路径 `engine.py:84-87` 不传该参数 | 给人「已设防」的错觉，实际无防护。GAP-2 的根因之一 |
| CFG-2 | **硬编码默认值绕过配置** | `EligibilityCriteria` 7 个字段中 5 个从未被传入（`eligibility.py:98-103`） | 参数不可见、不可审计、不在 run 快照中 |
| CFG-3 | **同名参数双份重复且已出现分叉** | `signals` 区块两份完全相同；`max_single_weight`（0.45 vs 0.40）、`max_non_emergency_turnover`（0.35 vs 0.25）已分叉 | 改一处忘另一处，静默行为不一致 |
| CFG-4 | **同名参数不同默认值** | `minimum_history_days` 配置 `126` vs `eligibility.py:98` 默认 `252`；`min_median_turnover_20d` 配置 1 亿 vs 默认 1000 万 | 一旦调用方漏传，行为突变且无告警 |
| CFG-5 | **配置无来源标记** | 全部 53 个参数无任何 `source` / `frozen` / `last_validated` 元数据 | 无法回答「这个值是谁定的、什么时候、依据什么」 |
| CFG-6 | **`system_name` 已过期** | `config/settings.json` 仍写 `"Quant V4.1-V4.5 Trusted Decision Upgrade"` | 版本标识与实际（V4.6）不符，快照里会记录错误版本名 |

---

## 5. 落地计划

| 优先级 | 动作 | 对应条目 |
|---|---|---|
| **P0** | 修复 CFG-1：让生产路径真正读取并强制 staleness 阈值 | UP-01、UP-02、GAP-2 |
| **P0** | 修复 CFG-2：`EligibilityCriteria` 全部 7 个字段必须显式传入并进入 run 快照 | UP-07、UP-25~28 |
| **P0** | 为每个参数增加来源元数据（`source` / `classification` / `last_validated`） | CFG-5 |
| **P0** | 修正 CFG-6 版本标识 | CFG-6 |
| **P1** | 消除 CFG-3 双份重复：抽出共享 `signals` 基线，两侧只写差异并注明差异理由 | UP-11~18、UP-20、UP-30 |
| **P1** | 消除 CFG-4：删除所有 dataclass 硬编码默认值，改为必填参数（缺参即报错，Fail-Closed） | UP-04、UP-05 |
| **P2** | 把 `hmm_shadow_only` 从参数升为冻结原则 | UP-39 |
| **P3** | 建立回测验证流程，逐步把 `LEGACY` 转为 `BACKTESTED` | 全部 27 条 LEGACY |

---

## 6. 验收方式

| 验收条件 | 表述 |
|---|---|
| ACC-PARAM-1 | 给定任一未冻结参数，当查询本表时，则必须能得到其分类（FROZEN/BACKTESTED/LEGACY/PROVISIONAL）与位置 |
| ACC-PARAM-2 | 给定一次运行，当生成 run 快照时，则快照必须包含**全部生效参数**的取值（含原先硬编码的 5 个） |
| ACC-PARAM-3 | 给定一个 `PROVISIONAL` 参数，当系统以 LIVE 模式启动时，则必须拒绝启动并列出未确认参数清单 |
| ACC-PARAM-4 | 给定 `EligibilityCriteria`，当调用方未显式传入任一字段时，则必须抛出异常而非使用默认值 |
| ACC-PARAM-5 | 给定两份 settings，当同名参数取值不一致时，则配置校验必须报告差异并要求显式声明理由 |

---

## 7. 变更记录

| 日期 | 版本 | 变更 |
|---|---|---|
| 2026-08-01 | v1.0 | 初版。登记 53 个参数 + 8 条冻结原则 + 5 个正确留空项 + 6 个结构性问题。核心发现：`BACKTESTED = 0`；`EligibilityCriteria` 7 字段中 5 个静默使用硬编码默认；`max_staleness_trading_days` 声明了但生产路径不读 |
