# =============================================================================
# 文件名     : test_noise_injection.py
# 模块名     : task2.eight.tests.test_noise_injection
# 版本       : v1.1
# 功能       : M3 判据——前端噪声按节点注入，且默认关闭时逐位不变。
#
# 设计说明   :
#   * 这里验的是**接线**，不是噪声的统计性质（后者在 src/tests/test_noise.py）。
#     接线错的两个典型后果：比较器噪声漏进残差（被后级重构吸收，等于消掉一个源）、
#     采样噪声只进判决不进残差（两个节点看到不同电压，闭合变差）。这两条都
#     无法靠看 SNDR 发现，必须逐位比对 residue 与 command。
#   * 「逐位相同」用 assert_array_equal 而非 allclose：默认关闭时噪声路径
#     必须完全不被执行，不是「差异足够小」。
#   * 名义网络与 capture() 在 conftest.py，两个注入测试文件共用一份。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# v1.0 初始版本，M3 判据。
# v1.1 名义网络抽到 conftest.py，供 M4b 的参考注入测试共用。
# =============================================================================

"""M3 判据：前端噪声按节点注入，且默认关闭时逐位不变。"""

from __future__ import annotations

import numpy as np
import pytest

from adi_model.noise import NoiseConfig, front_end_noise, thermal_sigma
from task2.eight.experiment import STREAM_TONE, STREAM_TRAIN
from task2.eight.tests.conftest import SAMPLES


def test_differential_thermal_sigma_matches_the_20p5pf_budget():
    """单侧 14.214 µV、差分 20.102 µV，与论文 20.5 pF 口径一致。"""
    assert thermal_sigma(20.5e-12, 300.0) == pytest.approx(14.2143e-6, rel=1e-4)
    assert thermal_sigma(20.5e-12, 300.0) * np.sqrt(2.0) == pytest.approx(20.1021e-6, rel=1e-4)


def test_disabled_noise_is_quiet_and_consumes_no_random_numbers():
    """默认配置不产生任何分量，调用方可以据此整条跳过噪声路径。"""
    quiet = front_end_noise(NoiseConfig(), SAMPLES)
    assert quiet.is_quiet
    assert not np.any(quiet.sampling_v)
    assert not np.any(quiet.comparator_v)
    assert not np.any(quiet.conversion_v)
    assert quiet.offset_v == 0.0


def test_quiet_noise_reproduces_the_no_noise_capture_bit_for_bit(rig):
    """把全零分量传进去，结果必须与不传 noise 完全一致。"""
    quiet = front_end_noise(NoiseConfig(), SAMPLES)
    base_state, base_coarse, base_residue, base_code = rig["capture"]()
    quiet_state, quiet_coarse, quiet_residue, quiet_code = rig["capture"](noise=quiet)
    np.testing.assert_array_equal(quiet_residue, base_residue)
    np.testing.assert_array_equal(quiet_code, base_code)
    np.testing.assert_array_equal(quiet_coarse, base_coarse)
    np.testing.assert_array_equal(quiet_state.switches.codes, base_state.switches.codes)


def test_sampling_noise_moves_both_the_decision_and_the_residue(rig):
    """采样噪声必须原样进残差：未跳档样本的残差变化逐点等于 g × 采样噪声。

    只断言「残差变了」不够——跳档也会让残差变，那样即使噪声只进了判决路径
    测试照样通过。等式口径能同时排除两种错：噪声没进残差（差为零）、
    噪声被二次缩放（差与 g·v 不成比例）。
    """
    base_state, _, base_residue, _ = rig["capture"]()
    noise = front_end_noise(NoiseConfig(enabled=True), SAMPLES, stream=STREAM_TONE)
    state, _, residue, _ = rig["capture"](noise=noise)
    same = state.switches.codes == base_state.switches.codes
    gain, _ = rig["physical"].affine(rig["plan"], state.features)
    np.testing.assert_allclose(
        residue[same] - base_residue[same],
        gain[same] * noise.sampling_v[same],
        rtol=0,
        atol=1e-12,
    )
    # 输入等效幅度落在 20.5 pF 差分预算上（4096 点的标准差估计本身有约 1.1% 涨落）。
    assert np.std(noise.sampling_v) == pytest.approx(20.102e-6, rel=0.03)


def test_comparator_noise_only_reaches_the_residue_through_the_coarse_code(rig):
    """比较器噪声不得直接叠在残差上，只能经由粗码跳档改变转换状态。

    这两种情况在波形上分不开，但在逐位比对上完全不同：直接叠加会让**每个**
    样本的残差都平移一点；经由状态改变时，只有跳档的那几个样本变。若把
    comparator_v 也写进 residue（这看起来很自然），就会被后级重构吸收，
    等于凭空消掉一个噪声源——本测试就是拦这个。

    注意跳档本身会改变 `g` 与 `b`，所以跳档样本的残差变化量不是整步长倍数，
    这里不对它做定量断言，只断言「没跳档的样本一位不动」。
    """
    base_state, _, base_residue, base_code = rig["capture"]()
    for sigma in (20e-6, 200e-6, 2e-3):
        noise = front_end_noise(
            NoiseConfig(enabled=True, include_thermal=False, comparator_sigma_v=sigma),
            SAMPLES,
            stream=STREAM_TONE,
        )
        state, _, residue, code = rig["capture"](noise=noise)
        same = state.switches.codes == base_state.switches.codes
        np.testing.assert_array_equal(residue[same], base_residue[same])
        np.testing.assert_array_equal(code[same], base_code[same])
        # 判决路径确实是活的：2 mV 噪声下必然出现粗码跳档。
        if sigma == 2e-3:
            assert not same.all()


def test_conversion_noise_never_reaches_the_decision(rig):
    """ADC2 噪声在判决时还不存在，粗码与命令必须逐位不变。"""
    base_state, base_coarse, base_residue, _ = rig["capture"]()
    noise = front_end_noise(
        NoiseConfig(enabled=True, include_thermal=False, adc2_sigma_v=3e-6),
        SAMPLES,
        stream=STREAM_TONE,
    )
    state, coarse, residue, _ = rig["capture"](noise=noise)
    np.testing.assert_array_equal(coarse, base_coarse)
    np.testing.assert_array_equal(state.switches.codes, base_state.switches.codes)
    assert not np.array_equal(residue, base_residue)


def test_offset_shifts_the_residue_by_a_constant(rig):
    """RA 失调是残差域常数，差分后恰好等于设定值。"""
    noise = front_end_noise(
        NoiseConfig(enabled=True, include_thermal=False, ra_offset_v=80e-6), SAMPLES
    )
    _, _, base_residue, _ = rig["capture"]()
    _, _, residue, _ = rig["capture"](noise=noise)
    np.testing.assert_allclose(residue - base_residue, 80e-6, rtol=0, atol=1e-15)


def test_stream_repeats_within_a_sequence_and_separates_across_sequences():
    """同一 stream 逐位复现，不同 stream 互相独立。"""
    cfg = NoiseConfig(enabled=True)
    tone_a = front_end_noise(cfg, SAMPLES, stream=STREAM_TONE)
    tone_b = front_end_noise(cfg, SAMPLES, stream=STREAM_TONE)
    train = front_end_noise(cfg, SAMPLES, stream=STREAM_TRAIN)
    # 同一序列重复调用必须拿到同一份噪声：off/on 与 shared/old_off 靠这个才可比。
    np.testing.assert_array_equal(tone_a.sampling_v, tone_b.sampling_v)
    assert not np.array_equal(tone_a.sampling_v, train.sampling_v)
