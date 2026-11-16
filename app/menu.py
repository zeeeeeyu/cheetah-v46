"""V4.6 unified console.

The menu invokes real CLI modules. LIVE order submission is intentionally absent.
"""
from __future__ import annotations
import subprocess, sys

VERSION = "V4.6.0-rc1-clean"

ITEMS = {
    "1": ("首次运行诊断", ["-m", "app.inspect_system"]),
    "2": ("初始化统一账户 MAIN", ["-m", "app.init_account"]),
    "3": ("导入旧状态（UNVERIFIED）", ["-m", "app.reconcile_account"]),
    "4": ("账户对账与状态", ["-m", "app.reconcile_account"]),
    "5": ("运行核心基金分析", ["-m", "app.run_core_fund"]),
    "6": ("运行战术ETF分析", ["-m", "app.run_tactical_etf"]),
    "7": ("运行每日PAPER汇总", ["-m", "app.run_daily", "--environment", "PAPER"]),
    "8": ("运行事件注意力PAPER", ["-m", "app.run_paper"]),
    "9": ("运行基准回测", ["-m", "app.run_backtest"]),
    "10": ("运行发布检查", ["-m", "app.release_check"]),
}

def _run(args: list[str]) -> int:
    print(f"\n执行: {sys.executable} {' '.join(args)}\n")
    return subprocess.call([sys.executable, *args])

def menu() -> None:
    while True:
        print("\n猎豹量化交易系统 V4.6 — 总控台")
        print(f"版本: {VERSION} | 默认环境: PAPER | 自动下单: 禁止")
        print("=" * 62)
        for key, (label, _) in ITEMS.items():
            print(f"  [{key}] {label}")
        print("  [q] 退出")
        choice = input("\n选择: ").strip().lower()
        if choice == "q":
            print("退出。")
            return
        item = ITEMS.get(choice)
        if item is None:
            print("无效选择。")
            continue
        _run(item[1])

if __name__ == "__main__":
    menu()
