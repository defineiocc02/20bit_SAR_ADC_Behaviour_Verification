# =============================================================================
# 文件名     : test_fig19_seven.py
# 模块名     : test_fig19_seven
# 版本       : v1.0
# 功能       : 七片采样、分级越界和解析码密度的独立数值检查。
#
# 设计说明   :
#   * 承担 八片模型与归档审计 中的一环；输入输出的物理口径与单位以同目录 SPEC/README 为准。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# =============================================================================

"""七片采样、分级越界和解析码密度的独立数值检查。"""

from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from task2.model.fig19_seven import (
    COARSE_LSB_V,
    DEFAULT_CONFIG,
    INPUT_MIN_V,
    OUTPUT_CODES,
    OUTPUT_LSB_V,
    ModelConfig,
    code_density,
    convert,
    decode_words,
)


# =========================================================================
# 1. 函数组：_word_reference / test_fractional_word_alone_reproduces_archived_ideal_experiment / test_all_words_and_the_patent_example
# =========================================================================
# -------------------------------------------------------------------------
# 核验理想七片实验中分数扰动对单笔输出和平均码宽的贡献。
# 按独立逐位表达式核对全字空间，以及原文给出的 2 档与 3 档示例。
# -------------------------------------------------------------------------
def _word_reference(word: int) -> tuple[int, int, int]:
    upper = 4 * (word // 32) + word // 8 % 2
    lower_integer = 2 * (word // 16 % 2)
    return upper, lower_integer, word % 8


def test_fractional_word_alone_reproduces_archived_ideal_experiment() -> None:
    """核验理想七片实验中分数扰动对单笔输出和平均码宽的贡献。"""
    data_path = Path(__file__).resolve().parents[1] / "evidence/fig19_seven/fig19_seven_data.npz"
    with np.load(data_path) as data:
        words = data["tone_dither_word_on"] & 0b111
        fractional = convert(data["tone_input_v"], words)
        np.testing.assert_array_equal(fractional.adc2_code, data["tone_on_adc2_code"])
        np.testing.assert_array_equal(fractional.output_code, data["tone_on_code"])
        density = code_density(
            -3.671875, 3.671875, np.arange(64) & 0b111, require_prefix_valid=True
        )
        np.testing.assert_allclose(
            density.width_v, data["linearity_on_width_v"], rtol=0, atol=1e-17
        )


def test_all_words_and_the_patent_example() -> None:
    """按独立逐位表达式核对全字空间，以及原文给出的 2 档与 3 档示例。"""
    terms = decode_words(np.arange(64))
    assert np.unique(terms.total_numerator).size == 57
    for word in range(64):
        upper, lower_integer, count = _word_reference(word)
        base = upper + lower_integer
        assert terms.upper_lsb[word] == upper
        assert terms.lower_numerator[word] == 7 * lower_integer + count
        assert terms.total_numerator[word] == 7 * base + count
        assert (
            sorted(terms.slice_setting_lsb[word].tolist())
            == [base] * (7 - count) + [base + 1] * count
        )

    assert sorted(terms.slice_setting_lsb[0b010101].tolist()) == [2, 2, 3, 3, 3, 3, 3]
    np.testing.assert_array_equal(terms.slice_setting_lsb[63], np.full(7, 8))


# =========================================================================
# 2. 函数组：test_non_six_bit_words_are_rejected / test_charge_path_against_exact_fraction_oracle / test_output_exactly_on_a_code_boundary_belongs_to_that_code
# =========================================================================
# -------------------------------------------------------------------------
# 检查非法字会显式报错。
# 全字空间的两处采样、重构和二十位量化用有理数独立计算。
# ADC2 中点等于二十位阈值时遵循半开码区间。
# -------------------------------------------------------------------------
@pytest.mark.parametrize("invalid", [-1, 64, 1.0, [2, 0.3]])
def test_non_six_bit_words_are_rejected(invalid) -> None:
    """检查非法字会显式报错。"""
    with pytest.raises(ValueError, match="dither words"):
        decode_words(invalid)


def test_charge_path_against_exact_fraction_oracle() -> None:
    """全字空间的两处采样、重构和二十位量化用有理数独立计算。"""
    coarse_step = Fraction(10, 512)
    fine_step = Fraction(1, 20480)
    output_step = Fraction(10, 1 << 20)
    for word in range(64):
        upper, low_integer, count = _word_reference(word)
        lower = (low_integer + Fraction(count, 7)) * coarse_step
        total = upper * coarse_step + lower
        exact_input = -5 + 256 * coarse_step + lower + Fraction(7, 31) * coarse_step
        sample = exact_input - lower
        coarse_code = (sample + 5) // coarse_step
        command = coarse_code - upper
        residue = sample - (-5 + coarse_code * coarse_step)
        fine_code = (residue + Fraction(1, 10)) // fine_step
        quantized = -Fraction(1, 10) + (fine_code + Fraction(1, 2)) * fine_step
        expected_output = -5 + coarse_code * coarse_step + lower + quantized
        output_code = (expected_output + 5) // output_step

        result = convert(float(exact_input), word)
        assert result.accepted_prefix_guard
        assert result.coarse_code == coarse_code
        assert result.command_code == command
        assert result.adc2_code == fine_code
        assert result.output_code == output_code
        assert result.residue_v == pytest.approx(float(residue), abs=1e-16)
        assert result.sadc_sample_v == pytest.approx(float(sample), abs=1e-16)
        assert np.mean(result.slice_sample_v) == pytest.approx(
            float(exact_input - total), abs=1e-16
        )
        assert result.reconstructed_v == pytest.approx(float(expected_output), abs=1e-16)


def test_output_exactly_on_a_code_boundary_belongs_to_that_code() -> None:
    """ADC2 中点等于二十位阈值时遵循半开码区间。"""
    result = convert(12.31 * DEFAULT_CONFIG.adc2_lsb_v, 0)
    assert result.adc2_code == 2060
    assert result.output_code == OUTPUT_CODES // 2 + 64


# =========================================================================
# -------------------------------------------------------------------------
# 分别触发档位、SADC、RDAC、前缀、ADC2 和输出守卫。
# 标称输入范围保留下端点，明确拒绝等于正满量程的电压。
# 等权两字的逐码积分和均匀细步输入相符，误差限于采样网格。
# -------------------------------------------------------------------------
def test_sampling_setting_prefix_and_analog_headroom_fail_independently() -> None:
    """分别触发档位、SADC、RDAC、前缀、ADC2 和输出守卫。"""
    restricted = convert(0, 63, ModelConfig(max_slice_setting_lsb=7))
    assert not restricted.setting_valid
    assert restricted.rdac_valid
    assert restricted.output_code == -1

    rdac_rail = convert(INPUT_MIN_V + 0.25 * COARSE_LSB_V, 32)
    assert rdac_rail.sadc_valid
    assert not rdac_rail.rdac_valid
    assert not rdac_rail.command_valid
    sadc_rail = convert(INPUT_MIN_V + 0.25 * COARSE_LSB_V, 16)
    assert not sadc_rail.sadc_valid

    prefix_only = convert(INPUT_MIN_V + 8.5 * COARSE_LSB_V, 32)
    assert prefix_only.command_code == 4
    np.testing.assert_array_equal(prefix_only.prefix_command_code, [-4, -4, -4, 4, 4, 4, 4])
    assert prefix_only.accepted_direct
    assert not prefix_only.accepted_prefix_guard

    adc2_limited = convert(0.75 * COARSE_LSB_V, 0, ModelConfig(adc2_half_range_v=0.005))
    assert adc2_limited.sadc_valid and adc2_limited.rdac_valid and adc2_limited.command_valid
    assert not adc2_limited.adc2_valid
    assert adc2_limited.adc2_code == -1
    assert adc2_limited.output_code == -1

    out_limited = convert(5 - 0.05 * OUTPUT_LSB_V, 0, ModelConfig(adc2_half_range_v=0.12))
    assert out_limited.adc2_valid
    assert out_limited.reconstructed_v > 5
    assert not out_limited.output_valid
    assert out_limited.output_code == -1


def test_input_range_is_half_open() -> None:
    """标称输入范围保留下端点，明确拒绝等于正满量程的电压。"""
    lower, upper = -5.0, 5.0
    vin = [lower, np.nextafter(upper, lower), upper]
    result = convert(vin, 0)
    np.testing.assert_array_equal(result.input_valid, [True, True, False])
    assert result.coarse_code[1] == 511
    assert result.output_code[2] == -1


def test_analytic_code_width_matches_independent_sample_grid() -> None:
    """等权两字的逐码积分和均匀细步输入相符，误差限于采样网格。"""
    low, high = 0.0123, 0.0223
    steps = 1 << 16
    step_v = (high - low) / steps
    ramp = low + (np.arange(steps) + 0.5) * step_v
    words = [0, 21]
    density = code_density(low, high, words, require_prefix_valid=True)
    brute = np.zeros(OUTPUT_CODES)
    for word in words:
        samples = convert(ramp, word)
        assert np.all(samples.accepted_prefix_guard)
        brute += np.bincount(samples.output_code, minlength=OUTPUT_CODES) * step_v / len(words)
    np.testing.assert_allclose(density.width_v, brute, atol=step_v, rtol=0)
    assert density.width_v.sum() == pytest.approx(high - low, abs=1e-15)


# =========================================================================
# 4. 函数组：test_analytic_density_tracks_rejections_without_moving_the_input_mass
# =========================================================================
# -------------------------------------------------------------------------
# 拒绝段的宽度另计，直接设置与前缀设置的判据分别对拍。
# -------------------------------------------------------------------------
def test_analytic_density_tracks_rejections_without_moving_the_input_mass() -> None:
    """拒绝段的宽度另计，直接设置与前缀设置的判据分别对拍。"""
    lo = INPUT_MIN_V + 8 * COARSE_LSB_V
    hi = lo + COARSE_LSB_V
    direct = code_density(lo, hi, [32])
    prefix = code_density(lo, hi, [32], require_prefix_valid=True)
    assert direct.width_v.sum() == pytest.approx(COARSE_LSB_V)
    assert prefix.pre_adc2_rejected_v == pytest.approx(COARSE_LSB_V)
    assert prefix.width_v.sum() == 0

    small_adc2 = code_density(0.01, 0.015, [0], ModelConfig(adc2_half_range_v=0.005))
    assert small_adc2.adc2_rejected_v == pytest.approx(0.005)
    assert small_adc2.width_v.sum() == 0

    too_high = code_density(5 - 0.05 * OUTPUT_LSB_V, 5, [0], ModelConfig(adc2_half_range_v=0.12))
    assert too_high.output_rejected_v == pytest.approx(0.05 * OUTPUT_LSB_V)
    assert too_high.width_v.sum() == 0
