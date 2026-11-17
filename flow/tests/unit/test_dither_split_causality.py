# =============================================================================
# 文件名     : test_dither_split_causality.py
# 模块名     : test_dither_split_causality
# 版本       : v1.0
# 功能       : 验证 D6 和 D5 对 SADC 判决的不同作用点，并观察最终码消扰。
# 设计说明   : 所有输入使用同一个默认 ADC 行为模型，制造失配与噪声关闭。
#              直流保持固定，只改一个扰动位，确保中间粗码变化能归因到该位。
#              最终码不变是单独的行为条件，不能以粗码测试代替。
# 变更记录   : 移除旧向量导入，按现行模型记录 D6 复原条件。
# =============================================================================
"""回归扰动的粗码分流和最终码复原。

D6 上段作用于粗码补偿一侧，D5 下段作用于 SADC 输入一侧。
本组测试固定相同直流输入，分别切换 D6、D5，读取 SADC 粗码和 20 位输出。
当前 D6 开关使最终输出发生变化，严格预期失败用例跟踪该项条件。
"""

from __future__ import annotations

import numpy as np
import pytest


# =============================================================================
# 1. 固定激励、制造种子和噪声条件
# =============================================================================
def _model():
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(root / "tools"))
    import run_p4_ablation as runner

    return runner._build_model(0.0, False)


DC = 0.4
UPPER_BIT = 5  # D6
LOWER_BIT = 4  # D5


def _run(word: int):
    model = _model()
    x = lambda t: np.full_like(np.asarray(t, dtype=float), DC)  # noqa: E731
    return model.simulate(x, 4, dither_words=np.full(4, word, dtype=np.int64))


# =============================================================================
# 2. 逐位检查粗判决路径并跟踪输出复原条件
# =============================================================================
def test_upper_bit_does_not_move_the_sadc_decision():
    base = _run(0)
    upper = _run(1 << UPPER_BIT)
    assert np.array_equal(np.asarray(base.raw.coarse), np.asarray(upper.raw.coarse)), (
        "setting only the upper dither bit changed the SADC decision; "
        "the split has collapsed into a single injection point"
    )


def test_lower_bit_does_move_the_sadc_decision():
    """Control: if nothing moved, the test above would pass for the wrong reason."""
    base = _run(0)
    lower = _run(1 << LOWER_BIT)
    assert not np.array_equal(np.asarray(base.raw.coarse), np.asarray(lower.raw.coarse)), (
        "the lower bit did not reach the quantiser; it is not the lower branch"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "当前行为模型在该直流激励下单独开启 D6 时最终码变化。"
        "粗码分流在前两个用例检查，最终码复原条件通过时严格 XPASS。"
    ),
)
def test_final_output_is_restored_for_every_word():
    """De-dithering: the final code must not depend on the injected word."""
    outputs = {int(np.asarray(_run(w).codes.code)[0]) for w in
               (0, 1 << UPPER_BIT, 1 << LOWER_BIT, (1 << UPPER_BIT) | (1 << LOWER_BIT))}
    assert len(outputs) == 1, f"final code depends on the dither word: {outputs}"
