"""V4.6 模型目标账（第一本账）—— 持久化 ForecastBundle 与 TargetPortfolio。

物理隔离：本模块只连接 model_targets.db，永远不触碰决策账/持仓账的表。
写入受 guards.assert_write_allowed('model_targets', source) 约束。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..domain.forecasts import ForecastBundle
from ..domain.portfolios import TargetPortfolio
from . import MODEL_TARGETS_DB, get_connection, now_iso
from .guards import assert_write_allowed

_LEDGER = "model_targets"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS forecast_bundles (
    bundle_id       TEXT PRIMARY KEY,
    model_id        TEXT NOT NULL,
    model_version   TEXT NOT NULL,
    as_of           TEXT NOT NULL,
    horizon         TEXT NOT NULL,
    trust_level     INTEGER NOT NULL,
    is_frozen_model INTEGER NOT NULL DEFAULT 1,
    data_status     TEXT NOT NULL DEFAULT 'PASS',
    predictions     TEXT NOT NULL DEFAULT '{}',
    reasons         TEXT NOT NULL DEFAULT '[]',
    metadata        TEXT NOT NULL DEFAULT '{}',
    source          TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS target_portfolios (
    portfolio_id        TEXT PRIMARY KEY,
    account_id          TEXT NOT NULL,
    strategy_id         TEXT NOT NULL,
    as_of               TEXT NOT NULL,
    environment         TEXT NOT NULL,
    target_weights      TEXT NOT NULL DEFAULT '{}',
    cash_weight         DECIMAL(18,6) NOT NULL DEFAULT 0,
    source_forecast_id  TEXT,
    trust_level         INTEGER NOT NULL,
    reasons             TEXT NOT NULL DEFAULT '[]',
    metadata            TEXT NOT NULL DEFAULT '{}',
    source              TEXT NOT NULL,
    created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_target_portfolios_account ON target_portfolios(account_id, as_of);
CREATE INDEX IF NOT EXISTS idx_forecast_bundles_model ON forecast_bundles(model_id, as_of);
"""


def ensure_schema(db_path: Path | str | None = None) -> None:
    conn = get_connection(db_path or MODEL_TARGETS_DB)
    conn.executescript(SCHEMA_SQL)
    conn.commit()


# ── ForecastBundle ────────────────────────────────────────────────

def write_forecast_bundle(
    bundle: ForecastBundle,
    source: str = "MODEL",
    db_path: Path | str | None = None,
) -> str:
    """写入一次模型预测（幂等：bundle_id 冲突则覆盖为同一批次）。"""
    assert_write_allowed(_LEDGER, source)
    ensure_schema(db_path)
    conn = get_connection(db_path or MODEL_TARGETS_DB)
    conn.execute(
        """INSERT OR REPLACE INTO forecast_bundles
           (bundle_id, model_id, model_version, as_of, horizon, trust_level,
            is_frozen_model, data_status, predictions, reasons, metadata, source, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            bundle.bundle_id, bundle.model_id, bundle.model_version, bundle.as_of,
            bundle.horizon, int(bundle.trust_level.value), 1 if bundle.is_frozen_model else 0,
            bundle.data_status.value, json.dumps(bundle.predictions, ensure_ascii=False),
            json.dumps(list(bundle.reasons), ensure_ascii=False),
            json.dumps(bundle.metadata, ensure_ascii=False), source, now_iso(),
        ),
    )
    conn.commit()
    return bundle.bundle_id


def read_forecast_bundle(bundle_id: str, db_path: Path | str | None = None) -> ForecastBundle | None:
    ensure_schema(db_path)
    conn = get_connection(db_path or MODEL_TARGETS_DB)
    row = conn.execute("SELECT * FROM forecast_bundles WHERE bundle_id = ?", (bundle_id,)).fetchone()
    if row is None:
        return None
    return ForecastBundle.from_dict({
        "bundle_id": row["bundle_id"],
        "model_id": row["model_id"],
        "model_version": row["model_version"],
        "as_of": row["as_of"],
        "horizon": row["horizon"],
        "trust_level": row["trust_level"],
        "is_frozen_model": bool(row["is_frozen_model"]),
        "data_status": row["data_status"],
        "predictions": json.loads(row["predictions"]),
        "reasons": json.loads(row["reasons"]),
        "metadata": json.loads(row["metadata"]),
    })


# ── TargetPortfolio ───────────────────────────────────────────────

def write_target_portfolio(
    portfolio: TargetPortfolio,
    source: str = "STRATEGY",
    db_path: Path | str | None = None,
) -> str:
    """写入一个目标组合（幂等：portfolio_id 冲突则覆盖）。"""
    assert_write_allowed(_LEDGER, source)
    ensure_schema(db_path)
    conn = get_connection(db_path or MODEL_TARGETS_DB)
    conn.execute(
        """INSERT OR REPLACE INTO target_portfolios
           (portfolio_id, account_id, strategy_id, as_of, environment, target_weights,
            cash_weight, source_forecast_id, trust_level, reasons, metadata, source, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            portfolio.portfolio_id, portfolio.account_id, portfolio.strategy_id, portfolio.as_of,
            portfolio.environment.value, json.dumps(portfolio.target_weights, ensure_ascii=False),
            portfolio.cash_weight, portfolio.source_forecast_id, int(portfolio.trust_level.value),
            json.dumps(list(portfolio.reasons), ensure_ascii=False),
            json.dumps(portfolio.metadata, ensure_ascii=False), source, now_iso(),
        ),
    )
    conn.commit()
    return portfolio.portfolio_id


def read_target_portfolio(portfolio_id: str, db_path: Path | str | None = None) -> TargetPortfolio | None:
    ensure_schema(db_path)
    conn = get_connection(db_path or MODEL_TARGETS_DB)
    row = conn.execute("SELECT * FROM target_portfolios WHERE portfolio_id = ?", (portfolio_id,)).fetchone()
    if row is None:
        return None
    return TargetPortfolio.from_dict({
        "portfolio_id": row["portfolio_id"],
        "account_id": row["account_id"],
        "strategy_id": row["strategy_id"],
        "as_of": row["as_of"],
        "environment": row["environment"],
        "target_weights": json.loads(row["target_weights"]),
        "cash_weight": float(row["cash_weight"]),
        "source_forecast_id": row["source_forecast_id"],
        "trust_level": row["trust_level"],
        "reasons": json.loads(row["reasons"]),
        "metadata": json.loads(row["metadata"]),
    })


def list_target_portfolios(account_id: str, db_path: Path | str | None = None) -> list[TargetPortfolio]:
    ensure_schema(db_path)
    conn = get_connection(db_path or MODEL_TARGETS_DB)
    rows = conn.execute(
        "SELECT portfolio_id FROM target_portfolios WHERE account_id = ? ORDER BY as_of DESC, created_at DESC",
        (account_id,),
    ).fetchall()
    out: list[TargetPortfolio] = []
    for r in rows:
        tp = read_target_portfolio(r["portfolio_id"], db_path)
        if tp is not None:
            out.append(tp)
    return out
