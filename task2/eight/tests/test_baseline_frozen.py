# =============================================================================
# 文件名     : test_baseline_frozen.py
# 模块名     : task2.eight.tests.test_baseline_frozen
# 版本       : v1.0
# 功能       : 守住行为模型的八项数值基线，任何默认值或模型改动导致的漂移都在这里变红
#
# 设计说明   :
#   * 这是 M2-M6 全部改动的唯一数值护栏。噪声、时偏、码口径三类改动都会动到
#     这八项里的某几项，没有这层护栏就只能靠人眼比归档，等于没护栏。
#   * 默认 strict 逐位相等。CI 显式选择 portable，仅对频谱／线性度标量
#     允许绝对 1e-9 的浮点尾数包络；单位、版本、种子和整数对拍不放宽。
#   * 指纹是环境相关的（python / numpy / adctoolbox 三者版本都记在
#     fingerprint.json 里）。环境不同时本测试会红，报错信息会指出是版本漂移
#     还是数值漂移——这两件事必须分开判断，否则会把换 numpy 当成改模型。
#   * 依赖 adctoolbox 才能算频谱。缺包时给可执行的提示而不是裸 ImportError。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本。
# =============================================================================

"""守住行为模型的八项数值基线。"""

from __future__ import annotations

import json
import os

import pytest

FROZEN_HINT = (
    "指纹是环境相关的，用记录的环境重跑："
    "uv run --python 3.12 --with numpy==2.5.3 --with matplotlib "
    "--with adctoolbox==0.9.1 python tools/snapshot_baseline.py --check"
)


def test_baseline_fingerprint_is_unchanged() -> None:
    """重算八项指标并与冻结指纹逐字段比较。"""
    from tools.snapshot_baseline import FINGERPRINT, METRICS, compare, measure

    if not FINGERPRINT.is_file():
        pytest.fail(f"基线指纹不存在: {FINGERPRINT}；先跑 tools/snapshot_baseline.py")

    expected = json.loads(FINGERPRINT.read_text(encoding="utf-8"))
    try:
        actual = measure()
    except ImportError as exc:
        pytest.fail(f"缺少运行依赖（{exc}）。{FROZEN_HINT}")

    mode = os.environ.get("ADC_BASELINE_FLOAT_MODE", "strict")
    assert mode in {"strict", "portable"}, f"unknown ADC_BASELINE_FLOAT_MODE: {mode}"
    problems = compare(expected, actual, portable=mode == "portable")
    assert not problems, (
        f"基线指纹漂移 {len(problems)} 项（共 {len(METRICS)} 项）：\n"
        + "\n".join(f"  {item}" for item in problems)
        + f"\n{FROZEN_HINT}"
    )
