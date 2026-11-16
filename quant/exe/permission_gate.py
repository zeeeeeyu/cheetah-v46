"""V4.6 交易权限闸门（Fail-Closed）。

设计原则（对应 REQ-ASSET-01/02/03）：

1. **未知即拒绝**：资产不在主数据目录中 = 没有交易授权，直接拒单。
   过去的行为是「配置里出现即可下单」，等价于把配置文件当成交易授权书。

2. **双条件同时放行**：``execution_eligible`` 与 ``permission`` 必须**都**允许，
   任一为否即拒绝。二者语义不同：
   - ``execution_eligible``：这个标的在**机制上**是否可交易（是否有下单通道）
   - ``permission``：策略对这个标的**被授予**了什么级别的资金权限

   实际数据中已出现两者冲突的条目（``execution_eligible=true`` 但
   ``permission=OBSERVATION_ONLY``），必须按「取更严格者」处理。

3. **环境敏感**：``PAPER`` 权限的资产不得生成 LIVE 订单。

4. **无绕过开关**：本模块不提供 ``enforce=False`` 之类的旁路参数。
   参见 ``docs/ARCHITECTURE_INFLUENCE_MATRIX.md`` §2.3 S-X1：
   StockSharp 的状态校验默认 Fail-Open，「可选的严格」等于「没有严格」。
   如需在测试中放行，请显式注入一个包含目标资产的 registry，而不是关闭校验。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from quant.domain.assets import Asset
from quant.domain.enums import CapitalPermission, Environment

# 任何环境下都不得生成订单的权限
_NEVER_TRADABLE = frozenset({
    CapitalPermission.OBSERVATION_ONLY,
    CapitalPermission.DISABLED,
})

# 仅允许在模拟环境生成订单的权限
_PAPER_ONLY = frozenset({CapitalPermission.PAPER})


@dataclass(frozen=True)
class PermissionCheck:
    """单个标的的权限校验结果。"""

    symbol: str
    allowed: bool
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        return {"symbol": self.symbol, "allowed": self.allowed, "reason": self.reason}


def check_execution_permission(
    symbol: str,
    asset: Asset | None,
    environment: Environment = Environment.LIVE,
) -> PermissionCheck:
    """校验单个标的是否被授权生成订单。

    Fail-Closed：``asset`` 为 None（不在主数据中）一律拒绝。
    """
    if asset is None:
        return PermissionCheck(
            symbol, False,
            f"{symbol}: 不在资产主数据目录中，无交易授权（Fail-Closed 拒单）",
        )

    if not asset.execution_eligible:
        return PermissionCheck(
            symbol, False,
            f"{symbol}: execution_eligible=False，禁止生成订单",
        )

    if asset.permission in _NEVER_TRADABLE:
        return PermissionCheck(
            symbol, False,
            f"{symbol}: 资金权限为 {asset.permission.value}，禁止生成订单",
        )

    if asset.permission in _PAPER_ONLY and environment == Environment.LIVE:
        return PermissionCheck(
            symbol, False,
            f"{symbol}: 资金权限为 PAPER，不得生成 LIVE 订单",
        )

    return PermissionCheck(symbol, True)


def load_asset_registry(config_dir: Path | None = None) -> dict[str, Asset]:
    """从 asset_master.json 加载 ``{symbol: Asset}`` 索引。

    延迟导入以避免 ``quant.assets`` ↔ ``quant.exe`` 的循环依赖。
    """
    from quant.assets import AssetMasterLoader

    return {a.symbol: a for a in AssetMasterLoader(config_dir).load_all()}


def resolve_registry(
    asset_registry: Mapping[str, Asset] | None,
) -> Mapping[str, Asset]:
    """解析资产目录。

    传 None 表示「使用系统主数据」；传空 dict 表示「没有任何资产被授权」，
    后者是合法的 Fail-Closed 用法，不会回退到主数据。
    """
    if asset_registry is not None:
        return asset_registry
    return load_asset_registry()
