"""V4.6 release/readiness checks.

Checks only active project code. Archived old systems are outside quant_v4_6.
"""
from __future__ import annotations
import json, re, sqlite3, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
results: list[tuple[str, bool, str]] = []

WINDOWS_ABSOLUTE_PATH = re.compile(r"(?i)(?<![A-Z0-9_])[A-Z]:[\\/]")

def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ""))

def active_python_files():
    for base in (ROOT / "app", ROOT / "quant", ROOT / "tests"):
        if base.exists():
            for path in base.rglob("*.py"):
                if path.resolve() == Path(__file__).resolve():
                    continue
                yield path

def scan_tokens(tokens: list[str]) -> list[tuple[str, str]]:
    found = []
    for py in active_python_files():
        text = py.read_text(encoding="utf-8", errors="ignore")
        if WINDOWS_ABSOLUTE_PATH.search(text):
            found.append((str(py.relative_to(ROOT)), "Windows absolute path"))
        for token in tokens:
            if token in text:
                found.append((str(py.relative_to(ROOT)), token))
    return found

def run_all() -> int:
    print("\n猎豹 V4.6 — 发布与首次运行检查")
    print(f"根目录: {ROOT}")
    print("=" * 60)

    # Tests
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "tests", "-q"],
            cwd=ROOT, capture_output=True, text=True, timeout=180,
        )
        output = (r.stdout + r.stderr).strip()
        check("活动工程测试通过", r.returncode == 0, output.splitlines()[-1] if output else "无输出")
    except Exception as exc:
        check("活动工程测试通过", False, str(exc))

    # Portability
    hardcoded = scan_tokens(["quant_v4_5_trusted"])
    check("活动代码无用户绝对路径", not hardcoded, str(hardcoded[:5]) if hardcoded else "通过")

    weak = []
    for py in active_python_files():
        for lineno, line in enumerate(py.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if line.strip() == "assert True":
                weak.append(f"{py.relative_to(ROOT)}:{lineno}")
    check("测试无无效 assert True", not weak, ", ".join(weak[:5]) if weak else "通过")

    check("V4.6活动工程不内嵌legacy", not (ROOT / "legacy").exists())
    check("统一账户配置为MAIN",
          json.loads((ROOT/"config/runtime.json").read_text(encoding="utf-8")).get("account_id") == "MAIN")
    runtime = json.loads((ROOT/"config/runtime.json").read_text(encoding="utf-8"))
    check("默认环境为PAPER", runtime.get("default_environment") == "PAPER")
    check("配置禁止LIVE自动生成", runtime.get("allow_live_order_generation") is False)

    market_count = len(list((ROOT/"data/market").glob("*.csv")))
    fund_count = len(list((ROOT/"data/fund_nav").glob("*.csv")))
    check("存在ETF本地行情", market_count > 0, f"{market_count}个")
    check("存在基金直接净值", fund_count > 0, f"{fund_count}个")

    required_state = [
        ROOT/"state/portfolio_state.json",
        ROOT/"state/fund_portfolio_state.json",
        ROOT/"state/fund_tradeability_snapshot.json",
    ]
    check("安全状态模板齐全", all(p.exists() for p in required_state))

    db = ROOT/"storage/ledgers/account.db"
    if not db.exists():
        check("发布包无真实账户数据库", True, "首次运行时初始化")
    else:
        try:
            conn = sqlite3.connect(db)
            rows = conn.execute("SELECT account_id, live_order_permission FROM accounts").fetchall()
            ok = all(int(row[1]) == 0 for row in rows)
            check("数据库LIVE权限全部关闭", ok, str(rows))
        except Exception as exc:
            check("数据库LIVE权限全部关闭", False, str(exc))

    entrypoints = [
        "app/menu.py", "app/inspect_system.py", "app/run_daily.py",
        "app/run_core_fund.py", "app/run_tactical_etf.py", "app/run_backtest.py",
    ]
    check("首次运行入口齐全", all((ROOT/x).exists() for x in entrypoints))

    passed = sum(1 for _, ok, _ in results if ok)
    print("=" * 60)
    print(f"结果: {passed}/{len(results)} 通过")
    if passed == len(results):
        print("状态: READY_FOR_FIRST_PAPER_RUN")
        return 0
    print("状态: NEEDS_ATTENTION")
    return 1

if __name__ == "__main__":
    raise SystemExit(run_all())
