"""Run event-attention strategy in PAPER only."""
from __future__ import annotations
import argparse
import pandas as pd
from quant.config import load_json
from quant.datahub import DataRepository
from quant.strategies.experimental.event_attention import run_event_attention_etf, generate_paper_report
from app.common import snapshot_run

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--as-of", default=str(pd.Timestamp.today().date()))
    args = parser.parse_args()
    repo = DataRepository()
    frames = {}
    for item in load_json("config/instruments.json"):
        try:
            frames[item["symbol"]] = repo.load(item["symbol"])
        except Exception:
            pass
    result = run_event_attention_etf(args.as_of, frames=frames)
    path = generate_paper_report(result)
    snapshot_run("event_attention", result, environment="PAPER",
                 as_of=str(args.as_of), report_path=path,
                 metrics={"n_signals": len(result.signals)})
    print(f"事件注意力PAPER完成: {path}")
    print(f"信号数: {len(result.signals)}；真实资金权限: 0")

if __name__ == "__main__":
    main()
