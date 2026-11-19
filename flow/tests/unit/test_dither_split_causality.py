# =============================================================================
# 文件名     : test_dither_split_causality.py
# 模块名     : test_dither_split_causality
# 版本       : v1.0
# 功能       : 验证 D6 和 D5 对 SADC 判决的不同作用点，并观察最终码消扰。
# 设计说明   : 生产入口及显式 Fig.11 消融夹具分别记录。研究夹具关闭制造失配与噪声。
#              直流保持固定，只改一个扰动位，确保中间粗码变化能归因到该位。
#              最终码不变是单独的行为条件，不能以粗码测试代替。
# 变更记录   : 移除旧向量导入，将研究夹具的 D6 复原条件与生产事务回归分开。
# =============================================================================
"""回归扰动的粗码分流和最终码复原。

D6 上段作用于粗码补偿一侧，D5 下段作用于 SADC 输入一侧。
本组测试固定相同直流输入，分别切换 D6、D5，读取 SADC 粗码和 20 位输出。
Fig.11 研究夹具在此输入下开启 D6 会改变最终码，严格预期失败用例持续跟踪。
生产模型另由包级入口检查相同四种输入，预期码为 594193。
"""

from __future__ import annotations

import numpy as np
import pytest


# =============================================================================
# 1. 固定激励、制造种子和噪声条件
# =============================================================================
from adi_model import simulate
from adi_model.behavioral.sampler import dc_input
from tools.run_p4_ablation import _build_model as build_fig11_study


DC = 0.4
UPPER_BIT = 5  # D6
LOWER_BIT = 4  # D5


def _research(word: int):
    model = build_fig11_study(0.0, False)
    x = lambda t: np.full_like(np.asarray(t, dtype=float), DC)  # noqa: E731
    return model.simulate(x, 4, dither_words=np.full(4, word, dtype=np.int64))


# =============================================================================
# 2. 逐位检查粗判决路径并跟踪输出复原条件
# =============================================================================
def test_fig11_upper_bit_does_not_move_the_sadc_decision():
    base = _research(0)
    upper = _research(1 << UPPER_BIT)
    assert np.array_equal(np.asarray(base.raw.coarse), np.asarray(upper.raw.coarse)), (
        "setting only the upper dither bit changed the SADC decision; "
        "the split has collapsed into a single injection point"
    )


def test_fig11_lower_bit_does_move_the_sadc_decision():
    """Control: if nothing moved, the test above would pass for the wrong reason."""
    base = _research(0)
    lower = _research(1 << LOWER_BIT)
    assert not np.array_equal(
        np.asarray(base.raw.coarse), np.asarray(lower.raw.coarse)
    ), "the lower bit did not reach the quantiser; it is not the lower branch"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Fig.11 研究夹具在 0.4 V 的码移试验中开启 D6 后最终码变化。"
        "粗码分流在前两个用例检查，最终码复原条件通过时严格 XPASS。"
    ),
)
def test_fig11_final_output_is_restored_for_the_d6_d5_combinations():
    """De-dithering: the final code must not depend on the injected word."""
    outputs = {
        int(np.asarray(_research(w).codes.code)[0])
        for w in (0, 1 << UPPER_BIT, 1 << LOWER_BIT, (1 << UPPER_BIT) | (1 << LOWER_BIT))
    }
    assert len(outputs) == 1, f"final code depends on the dither word: {outputs}"


def test_production_output_is_restored_for_the_same_four_words():
    """同一输入分别只开低段、只开上段、两段同时开，直接比较包级生产输出。"""
    # 这四个字测试 D6/D5 因果关系；完整 64 字覆盖在独立生产顶层录波中检验。
    words = [0, 1 << LOWER_BIT, 1 << UPPER_BIT, (1 << LOWER_BIT) | (1 << UPPER_BIT)]
    result = simulate(dc_input(DC), len(words), dither_words=words)
    np.testing.assert_array_equal(result.raw.coarse, [290, 292, 290, 292])
    np.testing.assert_array_equal(result.codes.code, [594193] * len(words))
    assert not np.any(result.codes.analog_overflow)
