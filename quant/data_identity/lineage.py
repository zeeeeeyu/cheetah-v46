"""V4.6 数据血缘与质量登记 —— 代理隔离的机器约束落点。

问题
====
审计发现：旧工程虽然在 ``datahub.quality_gate`` 里检查了
``source_symbol == symbol``，但缺少一个**一等的数据身份治理层**——没有对象
去登记"每一条被消费的数据来自哪里、截止到何时、内容哈希是什么、是直连
还是代理、质量是否达标"，因此"代理数据伪装成直连数据"这件事没有机器约束。

本模块补齐这一层：
- ``DataOrigin``：数据来源性质（DIRECT / PROXY / DERIVED / SYNTHETIC）。
- ``DataLineageRecord``：一条数据的完整身份与指纹（不可变）。
- ``DataIdentityRegistry``：登记 + 查询 + 生产可消费性判定。

代理隔离三条硬约束（Fail-Closed）
================================
1. **直连即真身**：origin==DIRECT 时，``source_symbol`` 必须等于 ``symbol``；
   否则拒绝登记（代理数据不得伪装成直连数据）。
2. **生产只吃直连且合格**：仅 origin==DIRECT 且 quality==PASS 的数据可被生产消费。
3. **代理必须显式**：PROXY/DERIVED/SYNTHETIC 数据可以登记（供研究），但
   ``is_production_eligible`` 永远为 False，从物理上无法进入真实决策路径。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from quant.domain.enums import DataStatus

__all__ = [
    "DataOrigin",
    "DataLineageRecord",
    "DataIdentityRegistry",
    "DataIdentityError",
    "content_fingerprint",
]


class DataIdentityError(ValueError):
    """数据身份治理相关的硬错误（代理伪装、重复登记冲突等）。"""


class DataOrigin(str, Enum):
    """数据来源性质。"""

    DIRECT = "DIRECT"        # 直连标的自身的真实行情/净值
    PROXY = "PROXY"          # 代理数据（用别的标的替代，仅研究）
    DERIVED = "DERIVED"      # 由其他数据计算派生（仅研究）
    SYNTHETIC = "SYNTHETIC"  # 人工合成/模拟（仅研究/测试）

    @property
    def is_direct(self) -> bool:
        return self is DataOrigin.DIRECT


def content_fingerprint(payload: Any) -> str:
    """为一段数据内容计算稳定指纹（sha256）。

    支持 bytes / str / pandas.DataFrame / 任意可 repr 对象。DataFrame 使用
    列名 + 形状 + 内容的字节序列，保证同内容同指纹、异内容异指纹。
    """
    if isinstance(payload, bytes):
        data = payload
    elif isinstance(payload, str):
        data = payload.encode("utf-8")
    elif hasattr(payload, "to_csv"):  # pandas.DataFrame / Series
        data = payload.to_csv(index=True).encode("utf-8")
    else:
        data = repr(payload).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class DataLineageRecord:
    """一条数据的完整身份与指纹（不可变、可入运行快照）。"""

    symbol: str
    """逻辑标的（我们要为哪个标的服务的数据）。"""

    source: str
    """数据来源渠道（如 local_csv / akshare_direct_etf / manual_import）。"""

    source_symbol: str
    """数据文件里真正承载的标的代码（代理检测的关键）。"""

    origin: DataOrigin
    """来源性质（DIRECT / PROXY / DERIVED / SYNTHETIC）。"""

    content_hash: str
    """内容指纹（sha256）。"""

    as_of: str
    """数据截止时间（YYYY-MM-DD）。"""

    quality: DataStatus = DataStatus.PASS
    """质量状态（复用 V4.6 领域枚举 PASS/DEGRADED/QUARANTINED/BLOCKED）。"""

    row_count: int = 0
    retrieved_at: str | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        # 硬约束 1：直连即真身 —— DIRECT 数据的 source_symbol 必须等于 symbol。
        if self.origin.is_direct and str(self.source_symbol) != str(self.symbol):
            raise DataIdentityError(
                f"代理伪装被拒绝：origin=DIRECT 但 source_symbol="
                f"{self.source_symbol!r} != symbol={self.symbol!r}。"
                f"直连数据的承载标的必须与逻辑标的一致。"
            )

    @property
    def is_production_eligible(self) -> bool:
        """是否可被生产决策消费。

        硬约束 2 & 3：必须 origin==DIRECT 且 quality==PASS 且 source_symbol==symbol。
        任何代理/派生/合成数据永远不可进入生产路径。
        """
        return (
            self.origin.is_direct
            and self.quality == DataStatus.PASS
            and str(self.source_symbol) == str(self.symbol)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "source": self.source,
            "source_symbol": self.source_symbol,
            "origin": self.origin.value,
            "content_hash": self.content_hash,
            "as_of": self.as_of,
            "quality": self.quality.value,
            "row_count": self.row_count,
            "retrieved_at": self.retrieved_at,
            "notes": self.notes,
            "is_production_eligible": self.is_production_eligible,
        }


class DataIdentityRegistry:
    """数据身份登记表 —— 一次运行中所有被消费数据的血缘账本。"""

    def __init__(self) -> None:
        self._records: dict[str, DataLineageRecord] = {}

    def __len__(self) -> int:  # pragma: no cover - trivial
        return len(self._records)

    def register(self, record: DataLineageRecord) -> DataLineageRecord:
        """登记一条数据血缘。

        同一 symbol 重复登记时，若内容指纹一致则视为幂等（返回原记录）；
        若指纹冲突（同标的却是不同内容）则抛错，避免"偷偷换数据"。
        """
        existing = self._records.get(record.symbol)
        if existing is not None:
            if existing.content_hash == record.content_hash:
                return existing  # 幂等
            raise DataIdentityError(
                f"数据血缘冲突：{record.symbol} 已登记指纹 "
                f"{existing.content_hash[:12]}…，与新指纹 "
                f"{record.content_hash[:12]}… 不一致。"
            )
        self._records[record.symbol] = record
        return record

    def get(self, symbol: str) -> DataLineageRecord:
        try:
            return self._records[symbol]
        except KeyError as exc:
            raise DataIdentityError(f"数据未登记血缘：{symbol}") from exc

    def is_production_eligible(self, symbol: str) -> bool:
        rec = self._records.get(symbol)
        return bool(rec and rec.is_production_eligible)

    def require_production(self, symbol: str) -> DataLineageRecord:
        """生产路径守卫：拿不到"直连且合格"的数据就 Fail-Closed。"""
        rec = self.get(symbol)
        if not rec.is_production_eligible:
            raise DataIdentityError(
                f"数据不可进入生产路径：{symbol}（origin={rec.origin.value}, "
                f"quality={rec.quality.value}, source_symbol={rec.source_symbol}）。"
            )
        return rec

    def all(self) -> tuple[DataLineageRecord, ...]:
        return tuple(self._records.values())

    def proxy_records(self) -> tuple[DataLineageRecord, ...]:
        """所有非直连（代理/派生/合成）记录。"""
        return tuple(r for r in self._records.values() if not r.origin.is_direct)

    def to_manifest(self) -> list[dict[str, Any]]:
        """导出为可写入运行快照 manifest 的血缘清单。"""
        return [r.to_dict() for r in self._records.values()]
