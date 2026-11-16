"""Topic-01 研究生成物确定性再生器。

背景（Phase-1.5 复现性加固）：
- ``results/TEST-*.json`` 与 ``experiment_registry.csv`` 已被 ``.gitignore`` 排除，
  因为它们是「运行即再生」的产物，每次 pytest / run_all 仅刷新时间戳，内容结论不变，
  入库会以时间戳噪声弄脏工作树。
- ``run_all.main()`` 是权威再生器：截断并重写 ``experiment_registry.csv``
  （覆盖 T01-01..T01-10 全部 25 个规范实验），并写出 ``results/*.json``。

用法：
    python -m research_lab.topic01_momentum_regime.regen_research_outputs
或仓库根目录直接：
    python research_lab/topic01_momentum_regime/regen_research_outputs.py

注意：双轨证据（ext_provenance / ext_hypothesis）落点 ``evidence/hypothesis_registry.csv``，
该文件仍入库，不在此再生器范围内。
"""

from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
# 仓库根目录（research_lab 的父目录）必须在 sys.path 上，
# 才能以「包」方式导入 run_all，使其内部 `from .experiment_runner` 相对导入成立。
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from research_lab.topic01_momentum_regime.run_all import main as _run_all_main  # noqa: E402


def main() -> None:
    print("=== Topic-01 研究生成物再生（确定性）===")
    print("将重写 experiment_registry.csv 与 results/*.json（TEST-* 与 T01-*）。")
    print("这些文件已被 .gitignore 排除，再生不影响 git 跟踪状态。")
    _run_all_main()
    print("\n再生完成。为保证 git 干净，仅保留 results/T01-*.json 与 evidence/ 入库；")
    print("experiment_registry.csv 与 results/TEST-*.json 为可再生物，已被忽略。")


if __name__ == "__main__":
    main()
