"""Run V4.6 core fund analysis. Decision only; no automatic order submission."""
from __future__ import annotations
import argparse
from quant.fund_pipeline import run_fund_daily
from app.common import save_json_report, snapshot_run

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", default=None)
    args = parser.parse_args()
    decision = run_fund_daily(args.as_of)
    path = save_json_report("fund_decisions", f"core_fund_{decision.as_of_date}.json", decision)
    snapshot_run("core_fund", decision, environment="PAPER",
                 as_of=str(decision.as_of_date), report_path=path,
                 metrics={"status": decision.status.value})
    print(f"核心基金分析完成: {decision.status.value}")
    print(f"市场状态: {decision.regime.regime.value}")
    print(f"目标权重: {decision.target_weights}")
    print(f"报告: {path}")
    print("注意：此入口只生成分析与人工计划，不自动下单。")

if __name__ == "__main__":
    main()
