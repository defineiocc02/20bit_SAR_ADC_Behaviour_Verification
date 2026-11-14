# =============================================================================
# 文件名     : test_timing.py
# 模块名     : src.tests.test_timing
# 版本       : v1.0
# 功能       : M5a 判据——专利数值链、15×Nyquist 极点与旧时偏路径的可复现性。
#
# 设计说明   :
#   * 采样抖动那条链（2.5 V / 10 MHz / 1 ps / 157 µV / 8.3 LSB18）是专利原文
#     逐句给出的，这里把它当**回归锚点**：任何一处系数写错都会同时破坏链上的
#     好几个数，单点看不出来，整条链一起比才看得出来。
#   * deterministic 模式的 delays 必须与旧写法逐位相同，否则 task1/evidence/effects
#     的归档结果不可复现。这是「重构不改行为」的硬约束，不是风格问题。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，M5a。
# =============================================================================

"""M5a 判据：专利数值链、15×Nyquist 极点与旧时偏路径的可复现性。"""

from __future__ import annotations

import numpy as np
import pytest

from adi_model.behavioral.timing_jitter import (
    DAC_BANDWIDTH_MULTIPLE_OF_NYQUIST,
    PATENT_CHAIN_BITS,
    PATENT_CHAIN_DR_V,
    PATENT_CHAIN_FREQ_HZ,
    PATENT_CHAIN_JITTER_S,
    PATENT_CHAIN_PEAK_V,
    RESIDUAL_RMS_TIMING_MISMATCH_S,
    TimingConfig,
    aperture_jitter,
    dac_pole_attenuation,
    jitter_limited_snr,
    pole_from_bandwidth_multiple,
    sine_slope,
)


def test_patent_timing_chain_reproduces_157_microvolt_and_8_3_lsb():
    """专利链：2.5 V / 10 MHz ⇒ 157 V/µs；1 ps ⇒ 157 µV；18b 下是 8.3 LSB。"""
    slope = sine_slope(PATENT_CHAIN_PEAK_V, PATENT_CHAIN_FREQ_HZ)
    assert slope == pytest.approx(157.0796e6, rel=1e-5)

    error = aperture_jitter(slope, PATENT_CHAIN_JITTER_S)
    assert error == pytest.approx(157.0796e-6, rel=1e-5)

    lsb18 = PATENT_CHAIN_DR_V / (1 << PATENT_CHAIN_BITS)
    assert lsb18 == pytest.approx(19.0735e-6, rel=1e-6)
    assert error / lsb18 == pytest.approx(8.3, rel=0.01)


def test_jitter_limited_snr_does_not_depend_on_amplitude():
    """抖动 SNR 上限与幅度无关：信噪同比例缩放，幅度在比值里抵消。"""
    small = jitter_limited_snr(2.5, 1.05e6, RESIDUAL_RMS_TIMING_MISMATCH_S)
    large = jitter_limited_snr(5.0, 1.05e6, RESIDUAL_RMS_TIMING_MISMATCH_S)
    assert small == pytest.approx(large, rel=1e-15)
    assert small == pytest.approx(108.04, rel=1e-3)


def test_zero_jitter_or_zero_frequency_is_unlimited():
    """没有抖动或没有输入频率时上限是无穷，不是 0 也不是除零。"""
    assert jitter_limited_snr(2.5, 1.05e6, 0.0) == float("inf")
    assert jitter_limited_snr(2.5, 0.0, 1e-12) == float("inf")


def test_fifteen_times_nyquist_pole_loses_only_two_hundredths_of_a_db():
    """讲稿的 15×f_Nyq：在 Nyquist 处一阶极点只衰减 0.019 dB。"""
    nyquist = 2.5e6
    pole = pole_from_bandwidth_multiple(DAC_BANDWIDTH_MULTIPLE_OF_NYQUIST, nyquist)
    assert pole == pytest.approx(15 * nyquist, rel=0)
    gain = dac_pole_attenuation(nyquist, pole)
    assert gain == pytest.approx(0.997787, rel=1e-5)
    assert 20 * np.log10(gain) == pytest.approx(-0.0193, rel=1e-2)


def test_pole_attenuation_hits_its_limits():
    """f→0 时衰减趋 1，f 远大于极点时趋 0，极点为 0 表示无限带宽。"""
    assert dac_pole_attenuation(0.0, 1e6) == 1.0
    assert dac_pole_attenuation(1e12, 1e6) < 1e-5
    assert dac_pole_attenuation(3e6, 0.0) == 1.0


def test_deterministic_delays_reproduce_the_legacy_pattern_bit_for_bit():
    """默认配置必须与旧写法逐位相同，否则 effects 归档不可复现。"""
    legacy = np.repeat((5e-12, -5e-12, 0.0), (8, 8, 2))
    np.testing.assert_array_equal(TimingConfig().delays(18), legacy)


def test_rms_delays_recover_the_sigma_and_repeat_with_one_seed():
    """按 RMS 模式抽样时，逐片时偏要落在设定 sigma 上，且同种子逐位复现。"""
    cfg = TimingConfig(mode="rms")
    first = cfg.delays(4096)
    second = cfg.delays(4096)
    np.testing.assert_array_equal(first, second)
    assert np.std(first) == pytest.approx(RESIDUAL_RMS_TIMING_MISMATCH_S, rel=0.03)
    assert not np.array_equal(first, TimingConfig(mode="rms", seed=1).delays(4096))


def test_unknown_timing_mode_is_rejected():
    """写错模式名必须报错，不能默默走 deterministic。"""
    with pytest.raises(ValueError, match="unknown timing mode"):
        TimingConfig(mode="rms2")
