# V4.5.1 Quant 模块依赖图

## 模块分类

| 类别 | 模块 | 说明 |
|------|------|------|
| **领域模型** | `models.py` | 枚举、DataClass（Asset/CheckResult/Signal/RegimeResult/Decision） |
| **配置** | `config.py` | 路径解析、JSON 读写（IO 工具函数） |
| **数据层** | `datahub.py` | ETF OHLCV 数据仓库、标准化、质量门控 |
| **基金数据层** | `fund_datahub.py` | 场外基金 NAV 数据仓库、自动刷新、质量门控 |
| **特征** | `features.py` | 动量、趋势质量、FI、下行波动率、ATR、相关性 |
| **基金特征** | `fund_features.py` | 场外基金 NAV 特征（复用 `compute_signal_features`） |
| **市场状态** | `regime.py` | MA120 + 吊灯止损 + HMM 影子模型 |
| **基金市场状态** | `fund_regime.py` | 基于 NAV 的市场状态 |
| **资产池** | `universe.py` | Instrument DataClass、验证、信号构建 |
| **基金资产池** | `fund_universe.py` | FundInstrument DataClass、验证、基金信号构建 |
| **组合构建** | `portfolio.py` | 准入过滤、相关性矩阵、逆下行波动率加权、HRP、行业/Regime 约束 |
| **执行** | `execution.py` | 状态加载、交易计划生成（ETF 分支） |
| **基金执行** | `fund_execution.py` | 基金状态加载、申赎约束验证、交易计划（基金分支） |
| **卫兵** | `guard.py` | 再平衡网闸、置信度、换手率计算、渐进调仓 |
| **治理** | `governance.py` | 决策日志追加、治理状态 |
| **基金治理** | `fund_governance.py` | 基金决策日志、基金治理状态 |
| **流水线** | `pipeline.py` | ETF 分支日运行主流程 |
| **基金流水线** | `fund_pipeline.py` | 基金分支日运行主流程 |
| **报告** | `reporting.py` | 决策报告输出 |
| **基金报告** | `fund_reporting.py` | 基金决策报告输出 |
| **回测** | `backtest.py` | ETF 回测引擎 |
| **基金回测** | `fund_backtest.py` | 场外基金回测引擎 |
| **基金历史覆盖** | `fund_historical_coverage.py` | 基金历史数据覆盖检查 |
| **基金计划** | `fund_schedule.py` | 复核周期估算 |
| **基金策略比较** | `fund_strategy_comparison.py` | 冠军/挑战者策略对比 |
| **基金观察** | `fund_watch.py` | 004813 等观察清单 |
| **基金账户设置** | `fund_account_setup.py` | 账户配置 |
| **基金费用假设** | `fund_fee_assumptions.py` | 费率假设管理 |
| **纸面交易** | `paper.py` | ETF 纸面交易模拟 |
| **基金纸面交易** | `fund_paper.py` | 基金纸面交易模拟 |
| **安全守卫** | `guard.py` | 全局守卫函数 |
| **基金治理治理** | `fund_governance.py` | 基金治理函数 |

## import 依赖图

```
pipeline.py
  ├── config.py (load_json, project_path)
  ├── datahub.py (DataRepository, quality_gate)
  ├── execution.py (build_trade_plan, load_state, write_trade_plan)
  ├── governance.py (append_decision_log, governance_status)
  ├── guard.py (rebalance_guard, signal_confidence, stage_target_weights, ...)
  ├── models.py (Decision, DecisionStatus, Regime, etc.)
  ├── portfolio.py (apply_*, cap_*, hrp_*, inverse_downside_vol_weights, select_assets)
  ├── regime.py (core_regime, hmm_shadow)
  ├── reporting.py (write_decision_report)
  └── universe.py (Instrument, build_signals, validate_catalog)

fund_pipeline.py
  ├── config.py
  ├── fund_datahub.py (FundNAVRepository, auto_refresh_direct_fund_navs, ...)
  ├── fund_execution.py (build_fund_trade_plan, load_fund_state, ...)
  ├── fund_features.py (nav_frames_as_close)
  ├── fund_governance.py (append_fund_decision_log, fund_governance_status)
  ├── fund_regime.py (fund_nav_regime, fund_hmm_shadow)
  ├── fund_reporting.py (write_fund_decision_report)
  ├── fund_schedule.py (estimate_next_review, review_interval_nav_observations)
  ├── fund_universe.py (FundInstrument, build_fund_signals, validate_fund_catalog)
  ├── guard.py (rebalance_guard, signal_confidence, ...)
  ├── models.py
  └── portfolio.py (same ETF 分支组合函数，复用)

universe.py → features.py, models.py
portfolio.py → features.py, models.py
regime.py → features.py, models.py
guard.py → models.py
execution.py → config.py
```

## 模块纯净度评估

| 模块 | 纯函数 | 写文件 | 读状态 | 依赖全局 |
|------|--------|--------|--------|----------|
| `models.py` | ✅ | ❌ | ❌ | ❌ |
| `config.py` | ✅ | ❌ | ✅ | ❌ |
| `features.py` | ✅ | ❌ | ❌ | ❌ |
| `datahub.py` | ❌ | ✅ | ✅ | ❌ |
| `regime.py` | ✅ (*) | ❌ | ❌ | ❌ |
| `universe.py` | ✅ | ❌ | ❌ | ❌ |
| `portfolio.py` | ✅ (*) | ❌ | ❌ | ❌ |
| `execution.py` | ❌ | ✅ trade plan | ✅ state | ❌ |
| `guard.py` | ✅ | ❌ | ❌ | ❌ |
| `governance.py` | ❌ | ✅ log | ❌ | ❌ |
| `pipeline.py` | ❌ | ✅ decision log + report | ✅ state | ❌ |
| `fund_pipeline.py` | ❌ | ✅ fund log + report | ✅ state | ✅ |
| `backtest.py` | ❌ | ✅ csv | ❌ | ❌ |
| `fund_backtest.py` | ❌ | ✅ csv | ❌ | ❌ |

(*) 有少量 pandas/numpy 副作用（pct_change 等），但无文件 IO

## 哪些模块只服务 ETF

- `datahub.py` — ETF OHLCV 数据仓库（`DataRepository`）
- `execution.py` — ETF 交易计划
- `pipeline.py` — ETF 主流程
- `backtest.py` — ETF 回测

## 哪些模块只服务基金

- `fund_datahub.py` — 场外基金 NAV 仓库
- `fund_execution.py` — 基金交易计划 + 申赎约束
- `fund_pipeline.py` — 基金主流程
- `fund_backtest.py` — 基金回测
- `fund_features.py` — NAV 特征适配
- `fund_regime.py` — NAV 市场状态
- `fund_universe.py` — 基金 Instrument 定义
- `fund_schedule.py` — 复核周期
- `fund_fee_assumptions.py` — 费率假设
- `fund_account_setup.py` — 账户设置
- `fund_historical_coverage.py` — 历史覆盖检查
- `fund_watch.py` — 观察清单
- `fund_paper.py` — 基金纸面交易
- `fund_reporting.py` — 基金报告
- `fund_governance.py` — 基金治理
- `fund_strategy_comparison.py` — 策略对比

## 哪些模块在 ETF 和基金之间共享

- `models.py` — 领域枚举和数据类（ETF 和基金共用 Decision/Regime/Signal）
- `config.py` — 文件 IO 工具
- `features.py` — 动量计算、FI、下行波动率（ETF 直接用，基金通过 `nav_frames_as_close` 适配）
- `guard.py` — 再平衡网闸、置信度判断
- `portfolio.py` — 选择、加权、行业限制、Regime 限制
- `governance.py` — 决策日志

## 重复逻辑识别

1. **`pipeline.py` 和 `fund_pipeline.py` 高度相似** — 主流程结构几乎一致（框架复制粘贴）
2. **`regime.py` 和 `fund_regime.py` 逻辑重复** — 但 fund_regime 使用 NAV close 而非 OHLCV
3. **ETF 和基金共享 `portfolio.py`** — 组合构建在语义上是一致的，未来应统一
4. **`execution.py` 和 `fund_execution.py` 各自实现状态管理** — 没有统一的状态读取接口
