"""V4.6 first-run diagnostic. Read-only; never changes positions or LIVE permission."""
from __future__ import annotations
import json, sqlite3, sys
from pathlib import Path
from quant.config import project_path, load_json
from quant.accounts import DEFAULT_DB_PATH, DEFAULT_ACCOUNT_ID
from quant.provisional import (
    PROVISIONAL_KEYS,
    verify_provisional_markers,
    verify_provisional_values,
)

def inspect_system() -> dict:
    checks = []
    def add(name: str, ok: bool, detail: str = ""):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    add("项目根目录", project_path().exists(), str(project_path()))
    required = [
        "config/settings.json", "config/fund/settings.json",
        "config/instruments.json", "config/fund/instruments.json",
        "state/portfolio_state.json", "state/fund_portfolio_state.json",
        "state/fund_tradeability_snapshot.json",
    ]
    for rel in required:
        p = project_path(*rel.split("/"))
        add(rel, p.exists(), str(p))

    market = sorted(project_path("data", "market").glob("*.csv"))
    fund_nav = sorted(project_path("data", "fund_nav").glob("*.csv"))
    add("ETF本地行情", len(market) > 0, f"{len(market)} 个CSV")
    add("基金直接净值", len(fund_nav) > 0, f"{len(fund_nav)} 个CSV")

    db = DEFAULT_DB_PATH
    if not db.exists():
        add("账户Ledger", True, "尚未初始化；首次运行可执行 python -m app.init_account")
        add("LIVE权限", True, "未创建数据库，默认关闭")
    else:
        try:
            conn = sqlite3.connect(db)
            row = conn.execute(
                "SELECT account_id, live_order_permission, total_assets FROM accounts WHERE account_id=?",
                (DEFAULT_ACCOUNT_ID,),
            ).fetchone()
            if row is None:
                add("账户Ledger", False, f"数据库存在但缺少账户 {DEFAULT_ACCOUNT_ID}")
                add("LIVE权限", True, "无目标账户")
            else:
                add("账户Ledger", True, f"账户={row[0]}, 总资产={row[2]}")
                add("LIVE权限", int(row[1]) == 0, "关闭" if int(row[1]) == 0 else "已开启，首次运行前应关闭")
        except Exception as exc:
            add("账户Ledger", False, str(exc))

    legacy = project_path("legacy")
    add("V4.6无旧版运行依赖", not legacy.exists(), "legacy已移出活动工程" if not legacy.exists() else str(legacy))

    # ── W2-1：PROVISIONAL 参数闸门（D-01 落地，Fail-Closed）────────────────
    # 未冻结参数只登记不校验，等于没登记：config 被改动后无人察觉，策略行为静默漂移。
    # 此处失败会使 status 变为 NEEDS_ATTENTION，run_daily.py:33-35 随即终止运行。
    try:
        settings = load_json("config/settings.json")
    except Exception as exc:  # noqa: BLE001
        add("PROVISIONAL参数登记", False, f"settings.json 读取失败: {exc}")
        add("PROVISIONAL生效值一致", False, "无法校验")
    else:
        missing = verify_provisional_markers(settings)
        add(
            "PROVISIONAL参数登记",
            not missing,
            f"{len(PROVISIONAL_KEYS)} 项登记完整"
            if not missing
            else f"settings.provisional_parameters 缺失: {', '.join(missing)}",
        )
        drifted = verify_provisional_values(settings)
        add(
            "PROVISIONAL生效值一致",
            not drifted,
            "生效值与 quant/provisional.py 登记一致"
            if not drifted
            else f"参数漂移（改动未同步登记）: {'; '.join(drifted)}",
        )

    ok = all(item["ok"] for item in checks)
    return {"status": "READY_FOR_PAPER" if ok else "NEEDS_ATTENTION", "checks": checks}

def main():
    result = inspect_system()
    print("\n猎豹 V4.6 — 首次运行诊断")
    print("=" * 60)
    for item in result["checks"]:
        print(f"{'✅' if item['ok'] else '❌'} {item['name']}: {item['detail']}")
    print("=" * 60)
    print(f"结论: {result['status']}")
    if result["status"] != "READY_FOR_PAPER":
        sys.exit(1)

if __name__ == "__main__":
    main()
