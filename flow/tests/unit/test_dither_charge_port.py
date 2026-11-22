# =============================================================================
# 文件名     : test_dither_charge_port.py
# 模块名     : 行为模型采样节点扰动
# 版本       : v1.0
# 功能       : 检查六位扰动字对残差、命令和重构输出的可见影响。
# 设计说明   :
#   * 固定直流可将跨字误差与频谱、输入边沿分离。
#   * 直流、制造种子和片组一致时，数字扣回可与失配造成的偏差分别测量。
# 变更记录   : 核对 Fig.19 两个采样端和 RDAC 码，保留单点误差未减半的对照。
# =============================================================================
from __future__ import annotations

import numpy as np
import pytest

from adi_model.behavioral.sampler import dc_input
from experiments.fig11 import Fig11Model, Fig11Parameters
from adi_model.simulation.config import ConfigError

LSB20_V = 6.0 / 2**20


# =============================================================================
# 1. 固定一颗器件与同一组转换条件
# =============================================================================
def _model(mismatch_std: float) -> Fig11Model:
    """同一 seed 的一颗芯片；噪声关掉，只看 dither 的确定性效果。"""
    return Fig11Model(
        seed=20261112,
        parameters=Fig11Parameters(
            unit_mismatch_std=mismatch_std,
            bridge_mismatch_std=mismatch_std,
            feedback_mismatch_std=0.0,
        ),
        config_overrides={
            "ra_enable_noise": False,
            "ra_out_noise_rms": 0.0,
            "enable_sampling_noise": False,
        },
    )


# =============================================================================
# 2. 逐字重构与失配反例
# =============================================================================
def test_ideal_port_is_transparent_for_all_sixty_four_words():
    """零失配下 64 个字必须重构同一电压：残差不能被 upper 顶出 RA 量程。"""
    model = _model(0.0)
    # 穷举原码使各上段组合收到相同次数，避免从随机流遗漏最坏字。
    words = np.arange(64, dtype=np.int64)
    run = model.simulate(dc_input(0.4), 64, dither_words=words, dither_mode="charge_port")
    out = np.asarray(run.raw.out, dtype=float)
    assert not np.any(run.raw.ra_sat), "dither 把残差推出了 RA 摆幅"
    assert not np.any(run.codes.analog_overflow)
    assert np.ptp(np.asarray(run.codes.code, dtype=np.int64)) <= 1
    assert np.ptp(out) <= 1.0 * LSB20_V, f"ptp={np.ptp(out) / LSB20_V:.2f} LSB20"
    assert abs(out.mean() - 0.4) <= 1.0 * LSB20_V


def test_port_applies_the_224_subtractor_to_the_rdac_code():
    """RDAC 码取前级判决 c 减去上段扰动码 U，公式对应专利图 19 的减法器 224。"""
    model = _model(1e-3)
    # 两种字的 U 同为 4，低段分别为 0 和正值。核对两端能捕获把 RDAC 误接到低段的错误。
    words = np.array([32, 53, 32, 53], dtype=np.int64)
    run = model.simulate(dc_input(0.4), 4, dither_words=words, dither_mode="charge_port")
    upper = np.asarray(run.raw.dither_program.upper_code, dtype=np.int64)
    observed = run.raw.sample
    program = run.raw.dither_program
    np.testing.assert_array_equal(observed.x_sadc, observed.input_v - program.lower_v)
    np.testing.assert_array_equal(observed.rdac_input_v, observed.input_v - program.total_v)
    exported = run.raw.binding_metadata["dither"]
    assert "k = c - U" in exported["topology"]
    assert exported["stored_rdac_input"] == "sample.rdac_input_v = input_v - T"
    assert "applied common voltage term = -total_v" in exported["digital_known_common_q"]
    assert upper.max() == 4
    assert np.array_equal(np.asarray(run.raw.k), np.asarray(run.raw.coarse) - upper)


def test_port_spreads_coarse_codes_and_preserves_the_measured_bias_counterexample():
    """0.4 V、σ=0.001 的固定器件。扰动应摊开粗码，单点平均误差由实际输出检验。"""
    model = _model(1e-3)
    level = 0.4
    words = np.arange(64, dtype=np.int64)
    off = model.simulate(dc_input(level), len(words))
    on = model.simulate(dc_input(level), len(words), dither_words=words, dither_mode="charge_port")
    # 两组按相同采样序号使用相同物理片。用一笔无扰动结果与 64 笔均值相比会混入片选差异。
    np.testing.assert_array_equal(off.raw.conv_slice_ids, on.raw.conv_slice_ids)
    assert not off.codes.analog_overflow.any() and not on.codes.analog_overflow.any()
    assert np.unique(on.raw.coarse).size > 3 and np.unique(off.raw.coarse).size == 1

    # 输入、采样预置和实际 DAC 码给出独立的电压账。失配允许留下名义 DAC、RA 增益与
    # ADC2 量化误差；每笔都必须能解释，不能只挑均值好看的扰动字。
    for run in (off, on):
        raw = run.raw
        preset = level - raw.sample.rdac_input_v
        expected = raw.vd0 + raw.fine / raw.state.estimated_gain + preset
        np.testing.assert_allclose(raw.out, expected, rtol=0, atol=1e-14)

    # 保留原来“至少减半”的反例。这颗器件开、关各 64 笔时实际偏差分别约 +21.88、+17.74。
    # 另记原基线的首笔 −8.55，今后更改片组、制造流或电荷模型时须能解释差异。
    off_errors = (off.raw.out - level) / LSB20_V
    on_errors = (on.raw.out - level) / LSB20_V
    np.testing.assert_allclose(
        [off_errors[0], off_errors.mean(), on_errors.mean()],
        [-8.55, 17.74, 21.88], rtol=0, atol=0.02,
    )
    assert abs(on_errors.mean()) >= 0.5 * abs(off_errors.mean())


def test_port_and_program_are_mutually_exclusive():
    """两条拓扑不能同时接；静默拼在一起会得到没有物理意义的电荷账。"""
    from adi_model.behavioral.sampler import capture
    from adi_model.control.dither import Fig19DitherProgram
    from adi_model.control.dither_port import DitherPort

    words = np.zeros(4, dtype=np.int64)
    spec = _spec()
    program = Fig19DitherProgram.from_words(words, spec=spec)
    cfg = _model(0.0).cfg
    with pytest.raises(ConfigError, match="mutually exclusive"):
        capture(
            cfg,
            dc_input(0.0),
            4,
            sample_ids=np.arange(4, dtype=np.int64),
            dither_program=program,
            dither_sample_v=np.zeros(4),
        )

    # 没有逐笔字的电荷口必须拒绝，而不是生成隐藏字。
    with pytest.raises(ConfigError, match="no per-raw words"):
        DitherPort(spec, 1e-14, 6.0).require_words()


def _spec():
    from adi_model.control.dither import (
        FIG19_FRACTIONAL_WEIGHTS_Q,
        FIG19_STEP_Q,
        Fig19StaticSpec,
    )

    return Fig19StaticSpec(
        v_fs=3.0,
        step_q=FIG19_STEP_Q,
        voltage_fraction_bits=32,
        fractional_lower_weights_q=FIG19_FRACTIONAL_WEIGHTS_Q,
        equivalent_polarity="subtract",
        provenance="unit test binding",
    )


@pytest.mark.parametrize("with_reference", [False, True])
def test_charge_port_keeps_sample_preset_on_skewed_signal_and_excludes_reference(
    monkeypatch, with_reference
):
    """让实际片池重采偏移的孔径。信号端保存 T，接低参考的电容只保留轨电压。"""
    from functools import partial

    from adi_model.control.dem import REF_LOW, SamplingConnections
    from experiments import fig11

    model = _model(0.0)
    delay_s = 0.75e-9
    slope_v_per_s = 1e5
    # 给每片相同的非零时偏可独立预测采样电荷，同时确实走逐片重采样分支。
    pool = partial(fig11.PhysicalSlicePool, t_skew=np.full(model.cfg.n_slices, delay_s))
    monkeypatch.setattr(fig11, "PhysicalSlicePool", pool)
    if with_reference:
        all_signal = fig11._all_signal

        def acquisition(*args):
            original = all_signal(*args)
            main = original.main.copy()
            main[..., 0] = REF_LOW
            return SamplingConnections(main, original.sub)

        monkeypatch.setattr(fig11, "_all_signal", acquisition)

    def ramp(t):
        return 0.4 + slope_v_per_s * np.asarray(t)

    raw = model.simulate(ramp, 2, dither_words=[0, 32], dither_mode="charge_port").raw
    total = raw.dither_program.total_v
    if with_reference:
        # 接参考轨和接信号两组分别核对，全部使用本次实际的采样与转换开关。
        observed = raw.digital_observation
        connected = observed.signal_mask
        masks = np.concatenate((raw.main_masks, raw.sub_masks), axis=-1)
        switch_v = np.where(masks, model.cfg.v_fs, -model.cfg.v_fs)
        expected = np.where(connected, -total[:, None, None], -model.cfg.v_fs) - switch_v
        assert np.any(~connected) and np.any(connected)
        np.testing.assert_allclose(observed.known_voltage_term_v, expected, rtol=0, atol=1e-14)
    else:
        expected_sample = ramp(raw.sample.aperture_s + delay_s) - total
        held_v = -raw.sampled_effective_charge_c / raw.normalization_capacitance_f
        np.testing.assert_allclose(held_v, expected_sample, rtol=0, atol=1e-14)
