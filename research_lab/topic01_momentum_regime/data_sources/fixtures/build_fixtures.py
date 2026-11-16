"""独立夹具生成入口（不触网）。

仅封装 fetch_phase15_data.build_fixtures + lineage_mismatch 写入，便于单独调用：

    python fixtures/build_fixtures.py
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
# 使本文件可作为脚本直接运行，导入兄弟模块的 fetch_phase15_data
sys.path.insert(0, os.path.normpath(os.path.join(_HERE, "..")))

from fetch_phase15_data import build_fixtures, _write_lineage_mismatch  # noqa: E402


def main() -> None:
    written = build_fixtures(_HERE)
    _write_lineage_mismatch(_HERE)
    print(f"已写入 {len(written)} 个 MOCK 夹具文件 + lineage_mismatch.json")
    for w in written:
        print(f"  - {w}")


if __name__ == "__main__":
    main()
