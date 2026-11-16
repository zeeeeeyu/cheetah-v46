# 猎豹量化交易系统 V4.6

本仓库是 V4.6 的代码发布快照，源版本为 `45fd364953c34f005c97960f8c617a207324eb13`。收录核心基金和 ETF 分析、交易治理、账户与订单模型、测试，以及 Topic-01 研究代码和合成校验样本。运行配置默认为 `PAPER`，`allow_live_order_generation` 为 `false`。本地基金账户映射初始为未确认。

## 安装与离线验证

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m app.v46_acceptance
```

独立仓库中的 Topic-01 行为测试使用合成数据。旧系统跨仓回归和原数据的实证复跑需在本地归档环境完成。

## PAPER 分析准备

将获得使用许可的同代码 ETF 日线和基金累计净值分别放入 `data/market` 与 `data/fund_nav`，代码与 `config` 中的样本池对应。Topic-01 离线研究若需加载本地数据，需要另配置 `data/metadata/etf_master.csv`。基金和 ETF 使用各自的数据契约。 [数据身份和溯源](docs/data/V46_DATA_IDENTITY_AND_LINEAGE.md)

首次初始化纸面状态时，可以将 [`examples/paper_state`](examples/paper_state) 下的三个合成 JSON 复制到 `state`。下列命令保留该目录中已有的同名文件。

```powershell
Get-ChildItem -File -LiteralPath '.\examples\paper_state' | ForEach-Object {
    $paperStateTarget = Join-Path '.\state' $_.Name
    if (-not (Test-Path -LiteralPath $paperStateTarget)) {
        Copy-Item -LiteralPath $_.FullName -Destination $paperStateTarget
    }
}
python -m app.inspect_system
python -m app.release_check
```

诊断会检查本地行情和纸面状态。行情缺失时会报告 `NEEDS_ATTENTION`。在本地授权的数据与运行配置就绪后，可以经 `python -m app.menu` 打开总控台。日常分析入口为 `python -m app.run_daily --environment PAPER`。运行前应核对当前数据来源和更新设置。执行边界见[账户、委托与对账](docs/architecture/V46_EXECUTION_AND_RECONCILIATION.md)。

## 同步范围

实际账户状态、数据库、运行结果和行情在本地保存。修改账户、数据或输出设置后，提交前可用 `git diff --cached --stat` 和 `git status --short` 复核将同步的内容。 [发布范围](docs/RELEASE_SCOPE.md) · [验收覆盖](docs/V46_REQUIREMENTS_TRACEABILITY.md)
