"""市场哨兵等级（GREEN / YELLOW / ORANGE / RED / DATA_ERROR）与迟滞规则。

市场哨兵回答的问题不是"现在应该买什么"，而是"现在市场处于什么风险
状态、系统是否应当降低风险敞口或暂停开仓"。它是防御性的、状态感知的。

五级语义::

    GREEN       常态：指标健康，允许正常运行与开仓。
    YELLOW      警戒：轻度恶化，收紧风险、提高复核频率。
    ORANGE      高危：显著压力，削减风险敞口、暂停新增开仓。
    RED         危机：系统性风险/黑天鹅，Risk-Off、只减不加。
    DATA_ERROR  无法判断：没有任何可用风险指标。

``DATA_ERROR`` 被**刻意排在 RED 之上**，理由是 Fail-Closed：
"不知道市场状态" 必须至少与 "已知处于最坏状态" 同等保守。
在此之前，全部指标缺失会落到 ``max([]) → 0`` 即 GREEN，
等价于「数据源全挂时系统报告一切正常」，这是最危险的一类 Fail-Open。

**非对称迟滞（状态迟滞）** 是本模块的核心设计，直接对应架构原则
"风险状态可以快速进入，风险解除需要较慢确认，不允许单日噪声频繁切换"：

  * 升级（风险变大）：**即时**。原始等级一旦高于当前等级，立即跳升到位。
  * 降级（风险变小）：**需连续 N 次确认**，且**每次只降一级**（不允许
    RED 一步回到 GREEN）。任何一次原始等级回升都会打断降级确认计数。
"""

from __future__ import annotations

from enum import IntEnum


class SentinelLevel(IntEnum):
    """市场哨兵五级（数值越大越保守，可直接比较）。"""
    GREEN = 0
    YELLOW = 1
    ORANGE = 2
    RED = 3
    DATA_ERROR = 4

    @property
    def label(self) -> str:
        return self.name

    @property
    def allows_new_entries(self) -> bool:
        """该等级是否允许新增开仓。ORANGE / RED / DATA_ERROR 暂停开仓。"""
        return self <= SentinelLevel.YELLOW

    @property
    def is_risk_off(self) -> bool:
        """是否进入 Risk-Off（只减不加）。"""
        return self >= SentinelLevel.ORANGE

    @property
    def is_data_failure(self) -> bool:
        """是否因数据失效而无法判断（区别于「已判断为危机」）。"""
        return self == SentinelLevel.DATA_ERROR


def escalate_immediately(current: SentinelLevel, raw: SentinelLevel) -> bool:
    """是否应即时升级：原始等级高于当前等级。"""
    return raw > current


def next_level_down(current: SentinelLevel) -> SentinelLevel:
    """返回下一个更低等级（每次只降一级）。GREEN 不再下降。"""
    if current == SentinelLevel.GREEN:
        return SentinelLevel.GREEN
    return SentinelLevel(current - 1)
