"""V4.6 账户初始化脚本。

创建初始账户数据库，LIVE 权限默认关闭。
发布包只包含示例数据库。
"""

from __future__ import annotations

from pathlib import Path

from quant.accounts import (
    DEFAULT_DB_PATH, DEFAULT_ACCOUNT_ID, ensure_schema, get_connection, now_iso, write_audit_log,
)
from quant.accounts.legacy_import import (
    LegacyAccountSnapshot, import_legacy_to_ledger,
)


def init_empty_account(
    account_id: str = DEFAULT_ACCOUNT_ID,
    mode: str = "MICRO",
    db_path: Path | str | None = None,
) -> None:
    """创建一个空账户，LIVE 权限默认关闭。"""
    db = Path(db_path) if db_path else DEFAULT_DB_PATH
    ensure_schema(db)
    conn = get_connection(db)

    # 检查是否已存在
    cursor = conn.execute("SELECT account_id FROM accounts WHERE account_id = ?", (account_id,))
    if cursor.fetchone() is not None:
        print(f"账户 {account_id} 已存在，跳过创建")
        return

    conn.execute(
        """INSERT INTO accounts
           (account_id, account_mode, total_assets, cash_balance, live_order_permission,
            total_capital_in, currency, note, created_at, updated_at)
           VALUES (?, ?, 0, 0, 0, 0, 'CNY', ?, ?, ?)""",
        (account_id, mode,
         f"初始空账户 — LIVE 权限已关闭。请完成对账后再启用。",
         now_iso(), now_iso()),
    )
    conn.commit()

    write_audit_log(
        account_id, "ACCOUNT_INITIALIZED", "account", account_id,
        new_value=f"mode={mode}, live_permission=0",
        source="SYSTEM", db_path=db,
    )
    print(f"✅ 账户 {account_id} ({mode}) 已创建，LIVE 权限已关闭")


def init_example_account(db_path: Path | str | None = None) -> None:
    """创建示例账户（不包含真实数据）。"""
    db = Path(db_path) if db_path else DEFAULT_DB_PATH
    init_empty_account("EXAMPLE", "MICRO", db)
    conn = get_connection(db)

    conn.execute(
        """UPDATE accounts SET note = '示例账户 — 仅用于演示。请勿用于真实交易。' WHERE account_id = 'EXAMPLE'"""
    )
    conn.commit()
    print("✅ 示例账户已创建")


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "init"
    if cmd == "init":
        init_empty_account()
    elif cmd == "example":
        init_example_account()
    else:
        print(f"未知命令: {cmd}")
