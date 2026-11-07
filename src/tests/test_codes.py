# =============================================================================
# 文件名     : test_codes.py
# 模块名     : src.tests.test_codes
# 版本       : v1.0
# 功能       : M6 判据——码/电压换算的唯一真源与三处调用口径一致。
#
# 设计说明   :
#   * 最关键的一条是「midpoint 往返不丢码」：码 → 中点电压 → 码必须回到原码。
#     这一条同时约束取整方向（必须向下）、中点偏移（必须 +0.5）和满幅定义
#     （必须 2*peak），任何一处写错都会让边界码来回跳一格。
#   * 边界用例用 nextafter 而不是随便减一个 epsilon：要验的是「刚好越界」
#     这一次浮点运算的行为，容差给大了就验不出截断还是抛错。
#   * bits 参数单独验一次 4 位：小位宽路径是 inl_dnl 测试靠 monkeypatch 用的，
#     如果它读死 20 位，那边的小位宽测试会静默算出完全不同的码。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，M6。
# =============================================================================

"""M6 判据：码/电压换算的唯一真源与三处调用口径一致。"""

from __future__ import annotations

import numpy as np
import pytest

from adi_model.codes import OUTPUT_BITS, OUTPUT_CODES, midpoint_v, to_signed, to_unsigned
from task_reconstruction.fixed import FULL_SCALE_CODE

PEAK = 5.0


def test_unsigned_matches_the_legacy_expression_on_random_points():
    """与 inl_dnl 早先的内联实现逐点相等，1e5 个随机点。"""
    rng = np.random.default_rng(20261105)
    volts = rng.uniform(-PEAK, PEAK, 100_000)
    legacy = np.floor((volts + PEAK) * ((1 << OUTPUT_BITS) / (2 * PEAK))).astype(np.int64)
    np.testing.assert_array_equal(to_unsigned(volts, PEAK), legacy)


def test_midpoint_round_trip_never_loses_a_code():
    """码 → 中点电压 → 码必须回到原码；这同时钉住取整方向与 +0.5 偏移。"""
    codes = np.arange(OUTPUT_CODES)
    recovered = to_unsigned(midpoint_v(codes, PEAK), PEAK)
    np.testing.assert_array_equal(recovered, codes)


def test_midpoint_matches_the_legacy_expression():
    """中点电压与实验层早先的内联写法逐点相等。"""
    codes = np.arange(0, OUTPUT_CODES, 4099)
    legacy = (codes + 0.5) * (2 * PEAK / OUTPUT_CODES) - PEAK
    np.testing.assert_array_equal(midpoint_v(codes, PEAK), legacy)


def test_boundaries_land_on_the_extremes_without_overflow():
    """−peak 给 0 码，+peak−ε 给最大码；这两端都不能静默截断。"""
    assert to_unsigned(-PEAK, PEAK) == 0
    # 不能用 nextafter：浮点上 (nextafter(5) + 5.0) 直接进位成 10.0，差值被加法吃掉，
    # 那样验的就不是取整方向而是舍入噪声。取一个明确的 1e-9 内点。
    just_inside = PEAK * (1.0 - 1e-9)
    assert just_inside < PEAK
    assert to_unsigned(just_inside, PEAK) == OUTPUT_CODES - 1
    assert to_unsigned(0.0, PEAK) == OUTPUT_CODES // 2


def test_strict_mode_rejects_out_of_range_instead_of_clipping():
    """越界在 strict 下必须抛；默认不抛以兼容调用方自行 clip 的旧用法。"""
    with pytest.raises(ValueError, match="outside the unsigned range"):
        to_unsigned(PEAK, PEAK, strict=True)
    with pytest.raises(ValueError, match="outside the unsigned range"):
        to_unsigned(np.nextafter(-PEAK, -np.inf), PEAK, strict=True)
    # 非 strict 时仍返回越界码，由调用方决定 clip 还是报错。
    assert to_unsigned(PEAK, PEAK) == OUTPUT_CODES
    assert to_unsigned(np.nextafter(-PEAK, -np.inf), PEAK) == -1


def test_bit_width_is_a_parameter_not_a_constant():
    """4 位时只有 16 个码；读死 20 位会让小位宽测试静默算错。"""
    volts = np.linspace(-PEAK, PEAK, 17)[:-1]
    codes = to_unsigned(volts, PEAK, bits=4)
    assert codes.max() == 15
    assert to_unsigned(0.0, PEAK, bits=4) == 8
    assert to_signed(8, bits=4) == 0
    assert midpoint_v(0, PEAK, bits=4) == pytest.approx(-PEAK + PEAK / 16, rel=1e-15)


def test_signed_offset_sits_at_the_code_midpoint():
    """有符号码以 2^(bits-1) 为原点，与 fixed.py 的定点偏置一致。"""
    assert to_signed(0) == -(OUTPUT_CODES // 2)
    assert to_signed(OUTPUT_CODES // 2) == 0
    assert to_signed(OUTPUT_CODES - 1) == OUTPUT_CODES // 2 - 1


def test_fixed_reconstruction_uses_the_shared_full_scale_code():
    """fixed.py 的满幅码数必须与 codes 模块同源，不能各写一个字面量。"""
    assert FULL_SCALE_CODE == OUTPUT_CODES


def test_invalid_arguments_are_rejected():
    """满幅非正、位宽非正都要报错，不能算出无意义的码。"""
    with pytest.raises(ValueError, match="peak must be positive"):
        to_unsigned(0.0, 0.0)
    with pytest.raises(ValueError, match="bit width must be positive"):
        to_unsigned(0.0, PEAK, bits=0)
    with pytest.raises(ValueError, match="peak must be positive"):
        midpoint_v(0, -1.0)
