# =============================================================================
# 文件名     : test_behavioral_closure.py
# 模块名     : test_behavioral_closure
# 版本       : v1.0
# 功能       : 验证保护标志的调用合同与最苛刻扰动字的边界关系。
# 设计说明   :
#   * 测试只覆盖名义阈值及调用方传入的保护标志，不推断物理标志可实现。
#   * +5 V 的半开端点和前级拒绝分开检查，避免用限幅掩盖越界。
# -----------------------------------------------------------------------------
# 变更记录   : 初始版本，暂无变更记录。
# =============================================================================
"""检查扰动保护候选的逐字合同、边界及不支持的正端点。"""

import numpy as np
import pytest

from adi_model.capacitors import ExperimentConfig, cap_pool
from task1.model.codebook import codebook
from task2.eight.behavioral_closure import apply_external_guard, encode_signed20_with_saturation
from task2.eight.model import coarse_decision, decode, native_step_v


# [接口] 同笔标志必须先于采样生效，不能把无效标志默认为 False。
def test_external_guard_preserves_unprotected_word_and_rejects_bad_input():
    """验证保护字置零与无保护字保留，并拒绝非法形状和位宽。"""
    words = np.array([0, 1, 31, 63], dtype=np.uint8)
    np.testing.assert_array_equal(
        apply_external_guard(words, np.array([False, True, False, True])), [0, 0, 31, 0]
    )
    with pytest.raises(ValueError):
        apply_external_guard(words, np.array([True]))
    with pytest.raises(ValueError):
        apply_external_guard(np.array([64]), np.array([False]))


# [端点] 最苛刻的保护阈值需覆盖所有 64 字，保护后只能使用零字。
def test_guarded_lower_endpoint_and_half_open_upper_endpoint():
    """以独立逐字枚举核对保护阈值两侧和 +5 V 的范围定义。"""
    config = ExperimentConfig()
    nominal, _ = cap_pool(config)
    thresholds = np.r_[codebook(nominal, config), config.full_scale_peak_v]
    step = native_step_v(config)
    word = np.arange(64, dtype=np.uint8)
    info = decode(word)
    limits = thresholds[info.upper] + info.lower_eighth * step / 8
    boundary = float(np.max(limits))
    worst = int(np.argmax(limits))
    probe = np.full(64, boundary - 1e-9)
    _, _, before = coarse_decision(probe, info, thresholds, step)
    assert not before[worst]
    protected = decode(apply_external_guard(word, np.ones(64, dtype=bool)))
    _, _, after = coarse_decision(probe, protected, thresholds, step)
    assert np.all(after)
    _, _, upper = coarse_decision(np.full(64, 5.0), protected, thresholds, step)
    assert not np.any(upper)


def test_output_saturation_is_explicit_and_keeps_20bit_range():
    """最终码饱和必须逐笔报告；它不改变或修复前级与 ADC2 的状态。"""
    v = np.array([-5.001, -5.0, 0.0, np.nextafter(5.0, -np.inf), 5.0, 5.001])
    codes, flags = encode_signed20_with_saturation(v)
    np.testing.assert_array_equal(flags, [True, False, False, False, True, True])
    assert codes[0] == codes[1] == -(1 << 19)
    assert codes[-1] == codes[-2] == (1 << 19) - 1
    with pytest.raises(ValueError):
        encode_signed20_with_saturation(np.array([np.nan]))
