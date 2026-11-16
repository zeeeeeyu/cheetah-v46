"""Run reproducible benchmark backtests on packaged direct ETF data."""
from __future__ import annotations
import argparse, json
from pathlib import Path
from quant.config import load_json, project_path
from quant.datahub import DataRepository
from quant.research.benchmarks import BENCHMARK_STRATEGIES
from app.common import save_json_report, snapshot_run

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategies", default="CASH,BUY_AND_HOLD,EQUAL_WEIGHT,MA120,TOP1_MOMENTUM,V46_TACTICAL_TREND")
    args = parser.parse_args()
    instruments = load_json("config/instruments.json")
    repo = DataRepository()
    frames = {}
    for item in instruments:
        sym = item["symbol"]
        try:
            frames[sym] = repo.load(sym)
        except Exception:
            continue
    settings = load_json("config/settings.json")
    names = [x.strip() for x in args.strategies.split(",") if x.strip()]
    results = {}
    for name in names:
        fn = BENCHMARK_STRATEGIES.get(name)
        if fn is None:
            results[name] = {"status": "UNKNOWN_STRATEGY"}
            continue
        r = fn(frames, settings)
        results[name] = r.to_dict() if hasattr(r, "to_dict") else r.__dict__
    path = save_json_report("backtests", "benchmark_results.json", results)
    snapshot_run("backtest", results, environment="RESEARCH", report_path=path,
                 metrics={"n_strategies": len(results)})
    print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
    print(f"回测结果: {path}")

if __name__ == "__main__":
    main()
