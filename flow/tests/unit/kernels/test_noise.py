# =============================================================================
# 文件名     : test_noise.py
# 模块名     : src.tests.test_noise
# 版本       : v1.0
# 功能       : 噪声内核的四条契约：物理量正确、可复现、源间正交、关闭即全零
#
# 设计说明   :
#   * 这四条契约是 M3 把噪声注进前端之后全部敏感度分析的地基。
#     若「正交性」破了，敏感度曲线上的差异就分不清是「新源的影响」
#     还是「加一个源导致后面的源抽样错位」，整条论证链就废了。
#   * σ 回收对**白噪**用 2^20 样本卡 1%：此时样本标准差的相对涨落约
#     1/sqrt(2N)=0.07%，窗口宽裕，能真正抓出「归一化写错一个 √2」这类错误。
#   * σ 回收对 **1/f** 不能卡 1%：它的功率几乎全落在最低几个频点，有效自由度
#     只有 O(100)，单次实现的标准差本身就有 6-8% 的涨落。故 1/f 分三条验：
#     集成平均回收（64 次实现）、sigma 线性标度（确定性）、斜率 −1（谱形）。
#     这是有意为之，不是把标准放松到能过。
#   * 1/f 只验斜率与总功率，不验具体谱形：真实电路噪声谱不在本模型能断言的范围内。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本。
# =============================================================================

"""噪声内核的物理量、可复现性、正交性与关闭语义。"""

from __future__ import annotations

import itertools

import numpy as np
import pytest

from adi_model.behavioral.noise import (
    TAG_COMPARATOR,
    TAG_FLICKER,
    TAG_REFERENCE,
    NoiseConfig,
    combine,
    flicker,
    thermal_sigma,
    white,
)

SAMPLES = 1 << 20


def test_default_config_is_fully_disabled() -> None:
    """默认配置必须全关，否则挂进 Capacitors 后既有实验数值会变。"""
    cfg = NoiseConfig()
    assert cfg.enabled is False
    assert cfg.comparator_sigma_v == 0.0
    assert cfg.reference_sigma_v == 0.0
    assert cfg.adc2_sigma_v == 0.0
    assert cfg.ra_offset_v == 0.0
    assert cfg.flicker_corner_hz == 0.0


def test_thermal_sigma_matches_analytic_value() -> None:
    """20.5 pF / 300 K 的单侧 kT/C 应为 14.214 µV，差分乘 √2 得 20.10 µV。"""
    single = thermal_sigma(20.5e-12, 300.0)
    assert single == pytest.approx(14.214e-6, rel=1e-3)
    assert single * np.sqrt(2.0) == pytest.approx(20.10e-6, rel=1e-3)


def test_thermal_sigma_rejects_nonpositive_inputs() -> None:
    """零或负的电容/温度不是物理量，必须抛而不是返回 nan。"""
    with pytest.raises(ValueError):
        thermal_sigma(0.0, 300.0)
    with pytest.raises(ValueError):
        thermal_sigma(1e-12, 0.0)


def test_white_recovers_sigma_within_one_percent() -> None:
    """2^20 样本的标准差与标称值相差应在 1% 以内。"""
    cfg = NoiseConfig(enabled=True)
    sigma = 20e-6
    sample = white(cfg, TAG_REFERENCE, sigma, SAMPLES)
    assert float(np.std(sample)) == pytest.approx(sigma, rel=0.01)
    assert abs(float(np.mean(sample))) < 0.01 * sigma


def test_white_is_reproducible_for_the_same_seed() -> None:
    """同一 seed 同一 tag 必须逐位相同，否则任何对比都不可复现。"""
    cfg = NoiseConfig(enabled=True)
    first = white(cfg, TAG_REFERENCE, 1e-6, 4096)
    second = white(cfg, TAG_REFERENCE, 1e-6, 4096)
    np.testing.assert_array_equal(first, second)


def test_white_is_orthogonal_across_tags() -> None:
    """加第二个源不得挪动第一个源的抽样。"""
    cfg = NoiseConfig(enabled=True)
    alone = white(cfg, TAG_REFERENCE, 1e-6, 4096)
    _ = white(cfg, TAG_COMPARATOR, 1e-6, 4096)
    again = white(cfg, TAG_REFERENCE, 1e-6, 4096)
    np.testing.assert_array_equal(alone, again)


def test_white_is_uncorrelated_across_tags() -> None:
    """不同 tag 之间的相关系数应接近 0，否则两个源会部分抵消或叠加异常。"""
    cfg = NoiseConfig(enabled=True)
    a = white(cfg, TAG_REFERENCE, 1.0, SAMPLES)
    b = white(cfg, TAG_COMPARATOR, 1.0, SAMPLES)
    correlation = float(np.corrcoef(a, b)[0, 1])
    assert abs(correlation) < 0.01


def test_disabled_config_returns_zeros_and_never_raises() -> None:
    """关闭时即使给了非零 sigma 也只能返回全零，不能抛也不能悄悄加噪。"""
    cfg = NoiseConfig(enabled=False)
    np.testing.assert_array_equal(white(cfg, TAG_REFERENCE, 30e-6, 1024), np.zeros(1024))
    np.testing.assert_array_equal(flicker(cfg, TAG_FLICKER, 1e-6, 1024, 40e6), np.zeros(1024))


def test_flicker_recovers_sigma_on_ensemble_average() -> None:
    """多次实现的方差平均后，1/f 分量的总功率应回到标称 sigma²。

    单次实现不行：1/f 的功率几乎全部落在最低的几个频点上，有效自由度只有
    ``(sum 1/k)^2 / sum 1/k^2`` 量级（2^16 样本约 73），所以单次实现的
    标准差本身就有约 8% 的涨落。拿单次实现去卡 1% 只会得到一个随机变红的测试。
    这里用 64 个不同 tag 的实现取方差平均，涨落降到约 1%，3% 的窗口才有意义。
    """
    cfg = NoiseConfig(enabled=True, flicker_corner_hz=40.0)
    sigma = 5e-6
    count = 1 << 16
    variances = [float(np.var(flicker(cfg, tag, sigma, count, 40e6))) for tag in range(1, 65)]
    recovered = float(np.sqrt(np.mean(variances)))
    assert recovered == pytest.approx(sigma, rel=0.03)


def test_flicker_scales_linearly_with_sigma() -> None:
    """把 sigma 加倍时，输出必须逐点加倍。

    这条是确定性的，能直接抓出「少开一个根号」「把方差当标准差」这类错误，
    而那类错误恰恰是单次实现的统计检验最容易放过的。
    """
    cfg = NoiseConfig(enabled=True, flicker_corner_hz=40.0)
    base = flicker(cfg, TAG_REFERENCE, 1e-6, 4096, 40e6)
    doubled = flicker(cfg, TAG_REFERENCE, 2e-6, 4096, 40e6)
    np.testing.assert_allclose(doubled, 2.0 * base, rtol=1e-12, atol=0.0)


def test_flicker_normalisation_is_within_gross_error_bound() -> None:
    """单次实现的方差不应偏离解析预测太远，用来抓归一化量级写错。

    带宽按有效自由度给：2^20 样本时约 115 个自由度，标准差涨落约 6.6%，
    故 25% 的窗口约等于 3.8 sigma，正常情况下不会误报；
    但若把 ``1/sqrt(gain)`` 写成 ``1/gain`` 之类的量级错误，这里必然红。
    """
    cfg = NoiseConfig(enabled=True, flicker_corner_hz=40.0)
    sigma = 5e-6
    sample = flicker(cfg, TAG_REFERENCE, sigma, SAMPLES, 40e6)
    assert float(np.std(sample)) == pytest.approx(sigma, rel=0.25)


def test_flicker_spectrum_falls_as_one_over_f() -> None:
    """整形后的功率谱在可用频段内的对数斜率应接近 −1。"""
    cfg = NoiseConfig(enabled=True, flicker_corner_hz=40.0)
    fs_hz = 40e6
    sample = flicker(cfg, TAG_REFERENCE, 1e-6, SAMPLES, fs_hz)

    spectrum = np.abs(np.fft.rfft(sample)) ** 2
    freqs = np.fft.rfftfreq(SAMPLES, d=1.0 / fs_hz)

    # 在 10 kHz–1 MHz 之间按倍频程平均，压低单 bin 的指数分布涨落。
    edges = 10e3 * 2.0 ** np.arange(0, 8)
    centres = []
    powers = []
    for low, high in itertools.pairwise(edges):
        window = (freqs >= low) & (freqs < high)
        centres.append(float(np.sqrt(low * high)))
        powers.append(float(np.mean(spectrum[window])))

    slope = float(np.polyfit(np.log(centres), np.log(powers), 1)[0])
    assert slope == pytest.approx(-1.0, abs=0.15)


def test_flicker_is_zero_without_a_corner_frequency() -> None:
    """未给拐点时不产生 1/f 分量，避免把白噪误标成有 1/f。"""
    cfg = NoiseConfig(enabled=True, flicker_corner_hz=0.0)
    np.testing.assert_array_equal(flicker(cfg, TAG_REFERENCE, 1e-6, 1024, 40e6), np.zeros(1024))


def test_combine_adds_variance_of_independent_parts() -> None:
    """独立分量相加后总方差应等于分量方差之和。"""
    cfg = NoiseConfig(enabled=True)
    first = white(cfg, TAG_REFERENCE, 3.0, SAMPLES)
    second = white(cfg, TAG_COMPARATOR, 4.0, SAMPLES)
    total = combine(first, second)
    assert float(np.var(total)) == pytest.approx(9.0 + 16.0, rel=0.02)


def test_combine_rejects_shape_mismatch() -> None:
    """长度不一致的分量不能相加，必须抛。"""
    with pytest.raises(ValueError):
        combine(np.zeros(4), np.zeros(5))
