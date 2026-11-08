# =============================================================================
# 文件名     : test_inl_dnl.py
# 模块名     : test_inl_dnl
# 版本       : v1.0
# 功能       : Check code-density integration against closed-form quantizers.
#
# 设计说明   :
#   * 承担 单元与独立电荷参考测试 中的一环；输入输出的物理口径与单位以同目录 SPEC/README 为准。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# =============================================================================

"""Check code-density integration against closed-form quantizers."""

import numpy as np
import pytest

from task1.experiments.inl_dnl import OUTPUT_BITS, Intervals, _fold, code_mass, inl_linearity


# =========================================================================
# 1. 函数组：_one_interval / test_ideal_quantizer_has_uniform_width_and_zero_inl_dnl / test_residue_overrange_and_encoder_clips_are_separate
# =========================================================================
# -------------------------------------------------------------------------
# A linear ideal ADC gives zero DNL and INL at its native bin resolution.
# ADC2 out-of-range input and output-code clipping conserve separate masses.
# -------------------------------------------------------------------------
def _one_interval(residue_gain: float, output_gain: float, base_v: float) -> Intervals:
    def value(number: float) -> np.ndarray:
        return np.array([number])

    return Intervals(
        value(-1),
        value(1),
        value(-residue_gain),
        value(residue_gain),
        value(base_v),
        value(output_gain),
    )


def test_ideal_quantizer_has_uniform_width_and_zero_inl_dnl() -> None:
    """A linear ideal ADC gives zero DNL and INL at its native bin resolution."""
    mass = code_mass(_one_interval(1, 1, 1), adc2_bits=8, half_range_v=1, peak_v=1)
    width = np.bincount(mass.code, weights=mass.width_v, minlength=1 << OUTPUT_BITS)
    grouped = _fold(width, 8)
    np.testing.assert_array_equal(grouped, np.full(256, 2 / 256))
    dnl, inl, stats = inl_linearity(grouped)
    np.testing.assert_array_equal(dnl, 0)
    np.testing.assert_array_equal(inl, 0)
    assert stats["zero_width_interior_codes"] == 0
    assert inl_linearity(width)[2]["zero_width_interior_codes"] == (1 << OUTPUT_BITS) - 258


def test_residue_overrange_and_encoder_clips_are_separate() -> None:
    """ADC2 out-of-range input and output-code clipping conserve separate masses."""
    mass = code_mass(_one_interval(2, 0.5, 0.5), adc2_bits=2, half_range_v=1, peak_v=1)
    assert mass.adc2_below_v == mass.adc2_above_v == 0.5
    assert mass.output_below_v == mass.output_above_v == 0.25
    assert mass.width_v.sum() == 1.0
    assert mass.code[0] == 0
    assert mass.code[-1] == (1 << OUTPUT_BITS) - 1


# =========================================================================
# 2. 函数组：test_affine_residue_requires_positive_slope / test_dense_fine_quantizer_output_aggregation_matches_bin_enumeration
# =========================================================================
# -------------------------------------------------------------------------
# A flat or reversed residue cannot be integrated as a monotonic interval.
# Inverse export boundaries and direct fine-bin enumeration agree with clipping.
# -------------------------------------------------------------------------
def test_affine_residue_requires_positive_slope() -> None:
    """A flat or reversed residue cannot be integrated as a monotonic interval."""
    for gain in (-1.0, 0.0):
        with pytest.raises(ValueError, match="positive gain"):
            code_mass(_one_interval(gain, 1, 1), adc2_bits=8, half_range_v=1, peak_v=1)


def test_dense_fine_quantizer_output_aggregation_matches_bin_enumeration() -> None:
    """Inverse export boundaries and direct fine-bin enumeration agree with clipping."""
    intervals = Intervals(
        np.array([-0.003, -0.002]),
        np.array([0.002, 0.004]),
        np.array([-0.0024, -0.0007]),
        np.array([0.0021, 0.0036]),
        np.array([0.0001, 2.0001]),
        np.array([0.98, 1.0001]),
    )
    enumerated = code_mass(intervals, adc2_bits=16, half_range_v=0.1, peak_v=1, aggregate=False)
    aggregated = code_mass(intervals, adc2_bits=16, half_range_v=0.1, peak_v=1, aggregate=True)
    expected = np.bincount(enumerated.code, weights=enumerated.width_v, minlength=1 << OUTPUT_BITS)
    actual = np.bincount(aggregated.code, weights=aggregated.width_v, minlength=1 << OUTPUT_BITS)
    np.testing.assert_allclose(actual, expected, rtol=0, atol=1e-15)
    for name in ("adc2_below_v", "adc2_above_v", "output_below_v", "output_above_v"):
        assert getattr(aggregated, name) == pytest.approx(getattr(enumerated, name), abs=1e-15)
