"""V4.6 战术 ETF 策略包。

将 V4.2 从"ETF 代理基金雷达"改造为真正的直接 ETF 战术引擎。

包含三个子策略：
- TACTICAL_TREND — 趋势跟随（首版实盘）
- TACTICAL_BREAKOUT — 突破策略（接口预留）
- TACTICAL_MEAN_REVERSION — 均值回归（接口预留）

资产池三级：
- RESEARCH_UNIVERSE — 20-50 只研究池
- ELIGIBLE_UNIVERSE — 通过流动性/数据检查
- LIVE_UNIVERSE — 根据账户规模筛选
"""
