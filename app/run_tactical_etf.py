"""Run V4.6 tactical ETF analysis. PAPER/read-only by default."""
from __future__ import annotations
import argparse
from quant.strategies.tactical_etf.engine import run_tactical_etf
from app.common import save_json_report, snapshot_run

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", default=None)
    parser.add_argument("--account-mode", default="MICRO")
    args = parser.parse_args()
    result = run_tactical_etf(args.as_of, account_mode=args.account_mode)
    path = save_json_report("decisions", f"tactical_etf_{result.as_of_date}.json", result)
    snapshot_run("tactical_etf", result, environment="PAPER",
                 as_of=str(result.as_of_date), report_path=path,
                 metrics={"regime": result.regime_label})
    print(f"战术ETF分析完成，市场状态: {result.regime_label}")
    print(f"选择: {result.selected_etfs}")
    print(f"目标权重: {result.target_weights}")
    print(f"报告: {path}")
    print("注意：首次运行仅用于PAPER观察，不生成真实订单。")

if __name__ == "__main__":
    main()
