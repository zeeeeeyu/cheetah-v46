"""V4.6 统一领域模型包。

本包定义了猎豹 V4.6 的核心领域契约，独立于基础设施（AkShare、pandas、CSV、JSON）。
所有领域对象：
- 不导入 AkShare
- 不读写 CSV/JSON/SQLite
- 不调用 pandas 行情下载接口
- 纯 Python 数据类 + 枚举
"""
