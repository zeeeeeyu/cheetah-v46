"""V4.6 账户对账命令 — CLI 入口。

用户人工核对手持份额、成本、市值后使用。
账实未通过前，LIVE_ORDER_PERMISSION 保持 false。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, date
from pathlib import Path
from typing import Any

from quant.accounts import (
    DEFAULT_DB_PATH,
    DEFAULT_ACCOUNT_ID,
    ensure_schema,
    get_connection,
    now_iso,
    write_audit_log,
)
from quant.ledgers.guards import assert_write_allowed
from quant.accounts.legacy_import import (
    LegacyAccountSnapshot,
    import_legacy_to_ledger,
    merge_legacy_snapshots,
    parse_v42_portfolio_state,
    parse_v451_portfolio_state,
    parse_v451_fund_portfolio_state,
    run_legacy_import,
)
from quant.accounts.position_service import aggregate_positions, get_account_summary
from quant.accounts.pnl import compute_pnl
from quant.config import project_path


def _yes_no(prompt: str, default: bool = True) -> bool:
    raw = input(prompt).strip().lower()
    if not raw:
        return default
    return raw in {"y", "yes", "是", "1", "true"}


def _float_input(prompt: str, default: float | None = None) -> float | None:
    raw = input(prompt).strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        print("输入无效，请输入数字。")
        return _float_input(prompt, default)



def _discover_legacy_roots(legacy_root: Path | None = None) -> tuple[Path, Path]:
    """发现独立保存的 V4.2 和 V4.5.1 工程。

    默认查找 V4.6 同级目录下的 Cheetah_V4.2_fixed 与 Quant_V4.5.1_fixed。
    也可通过 --legacy-root 指向包含这两个目录的共同根目录。
    """
    base = Path(legacy_root).expanduser().resolve() if legacy_root else project_path().parent
    v42_candidates = [
        base / "Cheetah_V4.2_fixed",
        base / "Cheetah_V4.2",
        base,
    ]
    v451_candidates = [
        base / "Quant_V4.5.1_fixed",
        base / "Quant_V4.5.1",
        base,
    ]
    v42_root = next((x for x in v42_candidates if (x / "portfolio_state.json").exists()), v42_candidates[0])
    v451_root = next(
        (x for x in v451_candidates if (x / "state" / "fund_portfolio_state.json").exists()
         or (x / "state" / "portfolio_state.json").exists()),
        v451_candidates[0],
    )
    return v42_root, v451_root


def cmd_import_legacy(
    db_path: Path | str | None = None,
    legacy_root: Path | None = None,
    account_id: str = DEFAULT_ACCOUNT_ID,
) -> None:
    """把独立旧系统状态作为 UNVERIFIED 迁移输入导入统一 Ledger。"""
    import sys as _sys
    if legacy_root is None:
        for i, arg in enumerate(_sys.argv):
            if arg == "--legacy-root" and i + 1 < len(_sys.argv):
                legacy_root = Path(_sys.argv[i + 1])
                break

    v42_root, v451_root = _discover_legacy_roots(legacy_root)
    v42_path = v42_root / "portfolio_state.json"
    v451_etf = v451_root / "state" / "portfolio_state.json"
    v451_fund = v451_root / "state" / "fund_portfolio_state.json"

    print("\n旧状态导入工具")
    print("=" * 60)
    print(f"目标账户: {account_id}")
    print("旧系统只作为迁移输入，不会成为 V4.6 运行依赖：")
    print(f"  V4.2:        {v42_path}")
    print(f"  V4.5.1 ETF:  {v451_etf}")
    print(f"  V4.5.1 基金: {v451_fund}")
    print()

    if not any(x.exists() for x in (v42_path, v451_etf, v451_fund)):
        print("❌ 未找到旧状态文件。请使用 --legacy-root 指向包含两个独立旧系统的根目录。")
        return
    if not _yes_no("继续导入？[Y/n]", True):
        print("已取消。")
        return

    result = run_legacy_import(
        v42_path=v42_path if v42_path.exists() else None,
        v451_etf_path=v451_etf if v451_etf.exists() else None,
        v451_fund_path=v451_fund if v451_fund.exists() else None,
        db_path=db_path,
        account_id=account_id,
    )

    print(f"\n导入结果：{result.status}")
    print(f"  账户: {result.account_id}")
    print(f"  导入交易: {result.transactions_imported}")
    print(f"  导入持仓: {result.positions_imported}")
    print(f"  现金（旧状态估算）: ¥{result.legacy_cash:.2f}")
    if result.warnings:
        print("  警告:")
        for w in result.warnings:
            print(f"    ⚠️ {w}")
    if result.errors:
        print("  错误:")
        for e in result.errors:
            print(f"    ❌ {e}")

    print("\n⚠️ 所有导入数据标记为 UNVERIFIED。")
    print("⚠️ LIVE_ORDER_PERMISSION 保持关闭。")
    print("请运行账户对账，使用真实份额和成本覆盖旧权重估算。")

def cmd_reconcile_account(db_path: Path | str | None = None) -> None:
    """交互式对账命令。

    用户输入：
    - 基金代码
    - 持有份额
    - 平均成本
    - 可用份额
    - 累计投入
    - 实际现金
    - 最近确认日期
    """
    ensure_schema(db_path)
    conn = get_connection(db_path)
    account_id = DEFAULT_ACCOUNT_ID

    print("\n📋 账户对账工具")
    print("=" * 60)
    print("请在销售平台核对你每只基金的真实数据后填入。")
    print("系统不会猜测或假设你的任何持仓。")
    print()

    # 1. Ensure account
    cursor = conn.execute("SELECT * FROM accounts WHERE account_id = ?", (account_id,))
    existing = cursor.fetchone()
    if existing is None:
        print("未找到账户。请先运行 import_legacy_state。")
        if _yes_no("创建新空账户？", True):
            conn.execute(
                """INSERT INTO accounts (account_id, account_mode, total_assets, cash_balance,
                   live_order_permission, note, created_at, updated_at)
                   VALUES (?, 'MICRO', 0, 0, 0, ?, ?, ?)""",
                (account_id, "新建空账户 — 等待用户初始化", now_iso(), now_iso()),
            )
            conn.commit()
        else:
            return

    # 2. Input declared holdings
    print("\n请逐只基金输入持仓数据（输入空代码结束）：")
    declared_holdings: dict[str, dict[str, float]] = {}
    while True:
        code = input("\n基金代码（留空结束）：").strip()
        if not code:
            break
        if len(code) != 6 or not code.isdigit():
            print("基金代码应为 6 位数字。")
            continue
        name = input(f"  基金名称：").strip() or code
        shares = _float_input(f"  {name} 持有份额：", 0)
        if shares is None or shares < 0:
            print("  份额无效。")
            continue
        cost = _float_input(f"  {name} 平均成本（元/份）：")
        available = _float_input(f"  {name} 可用份额（默认为持有份额）：", shares)
        declared_holdings[code] = {
            "name": name,
            "shares": shares or 0,
            "avg_cost": cost or 0,
            "available": available or shares or 0,
        }

    total_capital = _float_input("\n累计投入本金总额（元）：", 0)
    actual_cash = _float_input("实际现金/货基余额（元）：", 0)
    confirm_date = input("最近确认日期 YYYY-MM-DD（留空用今天）：").strip() or str(date.today())

    # 3. Compare with ledger
    ledger_positions = aggregate_positions(account_id, db_path=db_path)
    ledger_data = {}
    for p in ledger_positions:
        ledger_data[p.symbol] = {
            "quantity": p.total_quantity,
            "avg_cost": p.average_cost,
            "market_value": p.market_value,
        }

    print("\n📊 对账差异报告")
    print("=" * 60)
    mismatches: list[str] = []
    total_declared_value = 0.0
    total_ledger_value = 0.0

    for code, declared in declared_holdings.items():
        declared_value = declared["shares"] * declared["avg_cost"]
        total_declared_value += declared_value

        ledger_item = ledger_data.get(code)
        if ledger_item:
            ledger_value = ledger_item["quantity"] * (ledger_item["avg_cost"] or 0)
            total_ledger_value += ledger_value
            diff = declared_value - ledger_value
            status = "✅ 一致" if abs(diff) < 0.01 else "⚠️ 差异"
            if abs(diff) >= 0.01:
                mismatches.append(f"{code}: 声明 {declared_value:.2f} vs Ledger {ledger_value:.2f} (差 {diff:.2f})")
        else:
            status = "🆕 新资产"
            mismatches.append(f"{code}: 新声明资产，Ledger 中不存在")

        print(f"  {code} {declared.get('name', '')}: {status}")
        print(f"    声明: {declared['shares']:.2f}份 × ¥{declared['avg_cost']:.4f} = ¥{declared_value:.2f}")
        if ledger_item:
            print(f"    Ledger: {ledger_item['quantity']:.2f}份 × ¥{ledger_item['avg_cost']:.4f} = ¥{ledger_value:.2f}")

    cash_diff = actual_cash - (existing["cash_balance"] if existing else 0)
    if abs(cash_diff) > 0.01:
        mismatches.append(f"现金: 声明 ¥{actual_cash:.2f} vs Ledger ¥{(existing['cash_balance'] if existing else 0):.2f}")

    print(f"\n📈 总市值：声明 ¥{total_declared_value:.2f} | Ledger ¥{total_ledger_value:.2f}")
    print(f"💰 现金：声明 ¥{actual_cash:.2f} | Ledger ¥{(existing['cash_balance'] if existing else 0):.2f}")
    print(f"📝 累计投入：¥{total_capital:.2f}")

    # 4. Decide action
    if mismatches:
        print("\n⚠️ 存在以下差异需要人工确认：")
        for m in mismatches:
            print(f"  • {m}")
        print("\n差异可能源于：")
        print("  - 旧状态估算不精确")
        print("  - 遗漏了交易记录")
        print("  - 现金余额未更新")
        print()
        if _yes_no("是否接受当前声明值作为已对账基准？", False):
            _apply_reconciled_state(
                conn, account_id, declared_holdings, total_capital, actual_cash,
                confirm_date, mismatches, db_path,
            )
            print("\n✅ 已保存对账基准。LIVE_ORDER_PERMISSION 仍未启用。")
            print("请在确认账实完全一致后，手动启用 LIVE 权限。")
        else:
            print("\n⏸️ 未保存对账结果。请核实后重试。")
            print("LIVE_ORDER_PERMISSION 保持关闭。")
    else:
        print("\n✅ 账实一致！")
        if _yes_no("是否确认此对账结果？", True):
            _apply_reconciled_state(
                conn, account_id, declared_holdings, total_capital, actual_cash,
                confirm_date, [], db_path,
            )
            print("\n✅ 对账完成。如需启用实盘，请手动设置。")

    # 5. Compute PnL
    print("\n📊 盈亏概览：")
    try:
        pnl = compute_pnl(account_id, confirm_date, db_path=db_path)
        print(f"  累计投入: ¥{pnl.total_capital_in:.2f}")
        print(f"  组合市值: ¥{pnl.portfolio_value:.2f}")
        print(f"  已实现盈亏: ¥{pnl.realized_pnl:.2f}")
        print(f"  未实现盈亏: ¥{pnl.unrealized_pnl:.2f}")
        print(f"  累计费用: ¥{pnl.total_fees:.2f}")
        print(f"  总回报: ¥{pnl.total_return_amount:.2f} ({pnl.total_return_pct:.2f}%)")
    except Exception as e:
        print(f"  计算失败: {e}")


def _apply_reconciled_state(
    conn: Any, account_id: str,
    declared_holdings: dict,
    total_capital: float, actual_cash: float,
    confirm_date: str, mismatches: list[str],
    db_path: Any,
) -> None:
    """将用户声明的对账后状态写入 Ledger。"""
    timestamp = now_iso()

    # Update account
    total_value = sum(
        h["shares"] * h["avg_cost"] for h in declared_holdings.values()
    ) + actual_cash

    conn.execute(
        """UPDATE accounts SET
           total_capital_in = ?, cash_balance = ?, total_assets = ?,
           realized_pnl = 0, unrealized_pnl = 0,
           updated_at = ?, note = ?
           WHERE account_id = ?""",
        (total_capital, actual_cash, total_value,
         timestamp, f"用户人工对账 — {confirm_date}。差异数: {len(mismatches)}",
         account_id),
    )

    # Record reconciliation run
    mismatches_json = json.dumps(mismatches, ensure_ascii=False)
    conn.execute(
        """INSERT INTO reconciliation_runs
           (account_id, run_date, status, ledger_cash, declared_cash, cash_diff,
            ledger_value, declared_value, value_diff, mismatches, resolved, created_at)
           VALUES (?, ?, 'COMPLETED', 0, ?, ?, 0, ?, 0, ?, ?, ?)""",
        (account_id, confirm_date, actual_cash, 0.0, total_value, mismatches_json,
         len(mismatches) == 0, timestamp),
    )

    # Write position snapshots for declared holdings
    # GAP-4: 通过台账白名单守卫校验来源，杜绝绕过
    assert_write_allowed("position_snapshots", "USER_RECONCILIATION")
    for code, h in declared_holdings.items():
        conn.execute(
            """INSERT INTO position_snapshots
               (account_id, symbol, quantity, available_quantity, average_cost,
                market_value, snapshot_date, source, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'USER_RECONCILIATION', ?)""",
            (account_id, code, h["shares"], h["available"],
             h["avg_cost"], h["shares"] * h["avg_cost"],
             confirm_date, timestamp),
        )

    write_audit_log(
        account_id, "ACCOUNT_RECONCILED", "account", account_id,
        new_value=json.dumps({
            "confirm_date": confirm_date,
            "total_capital": total_capital,
            "cash": actual_cash,
            "holdings_count": len(declared_holdings),
            "mismatches": len(mismatches),
        }, ensure_ascii=False),
        source="USER_RECONCILIATION", db_path=db_path,
    )
    conn.commit()


def cmd_show_status(db_path: Path | str | None = None) -> None:
    """显示账户状态摘要。"""
    ensure_schema(db_path)
    conn = get_connection(db_path)
    account_id = DEFAULT_ACCOUNT_ID

    summary = get_account_summary(account_id, db_path)
    if not summary:
        print("未找到账户。请先运行 import_legacy_state。")
        return

    print(f"\n📋 账户状态 — {account_id}")
    print("=" * 60)
    print(f"  模式: {summary.get('account_mode', 'N/A')}")
    print(f"  累计投入: ¥{summary.get('total_capital_in', 0):.2f}")
    print(f"  现金余额: ¥{summary.get('cash_balance', 0):.2f}")
    print(f"  总资产: ¥{summary.get('total_assets', 0):.2f}")
    print(f"  已实现盈亏: ¥{summary.get('realized_pnl', 0):.2f}")
    print(f"  未实现盈亏: ¥{summary.get('unrealized_pnl', 0):.2f}")
    print(f"  累计费用: ¥{summary.get('total_fees', 0):.2f}")
    live_ok = summary.get('live_order_permission', 0)
    print(f"  LIVE 订单权限: {'✅ 已启用' if live_ok else '❌ 已禁用'}")

    positions = aggregate_positions(account_id, db_path=db_path)
    if positions:
        print(f"\n  当前持仓 ({len(positions)} 只):")
        for p in positions:
            print(f"    {p.symbol}: {p.total_quantity:.2f}份 @ ¥{p.average_cost:.4f}")
    else:
        print("\n  无持仓记录。")


def cmd_enable_live(db_path: Path | str | None = None) -> None:
    """手动启用 LIVE 订单权限。

    仅当账实一致且用户明确确认后才应调用。
    """
    ensure_schema(db_path)
    conn = get_connection(db_path)
    account_id = DEFAULT_ACCOUNT_ID

    summary = get_account_summary(account_id, db_path)
    if not summary:
        print("未找到账户。")
        return

    print("\n⚠️ 启用 LIVE 订单权限")
    print("=" * 60)
    print("此操作将允许系统为你的真实账户生成交易建议。")
    print("你必须已经：")
    print("  1. 完成所有基金份额、成本、市值的核对")
    print("  2. 确认账实一致")
    print("  3. 理解系统仅生成建议，不自动下单")
    print()

    if not _yes_no("确认已核对账实并准备启用？[y/N]", False):
        print("已取消。")
        return

    conn.execute(
        "UPDATE accounts SET live_order_permission = 1, updated_at = ? WHERE account_id = ?",
        (now_iso(), account_id),
    )
    write_audit_log(
        account_id, "LIVE_PERMISSION_ENABLED", "account", account_id,
        new_value="1",
        source="USER_MANUAL",
        db_path=db_path,
    )
    conn.commit()
    print("✅ LIVE 订单权限已启用。系统现在可以生成交易建议（仍需人工审批）。")


# ── CLI Router ──────────────────────────────────────────────────

COMMANDS = {
    "1": ("导入旧状态", cmd_import_legacy),
    "2": ("对账工具", cmd_reconcile_account),
    "3": ("查看账户状态", cmd_show_status),
    "4": ("启用 LIVE 权限", cmd_enable_live),
}


def main() -> None:
    print("\n猎豹 V4.6 — 账户对账工具")
    print("=" * 60)

    db_path = DEFAULT_DB_PATH
    ensure_schema(db_path)

    while True:
        print("\n请选择操作：")
        for key, (label, _) in sorted(COMMANDS.items()):
            print(f"  [{key}] {label}")
        print("  [q] 退出")
        choice = input("\n选择: ").strip().lower()
        if choice == "q":
            break
        cmd = COMMANDS.get(choice)
        if cmd:
            cmd[1](db_path)
        else:
            print("无效选择。")


if __name__ == "__main__":
    main()
