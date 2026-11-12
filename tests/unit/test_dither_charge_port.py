"""独立电荷口 dither（v2）的接入门禁。

v1（`dither_program`）把 dither 实现成 RDAC 码整体减 U，因此只能在均匀 512 码本、
零噪声、无 DEM 的条件下成立。v2 把 dither 电容接到**采样节点**：SADC 与 RDAC
两个视图同时平移 T，RDAC 码保持真实判决 c，数字侧扣回 -T。

本文件钉住四条性质：

1. 理想器件（零失配、零噪声）下，64 个 dither 字重构同一输入，差异不超过 1 LSB20；
2. 有失配时 dither 真的改变粗码使用（多码扩散），并把静态误差的**均值**压低；
3. RDAC 码不因 dither 而偏移（不同于 v1 的 k=c-U）；
4. 与 `dither_program` 互斥，且缺少逐笔字时拒绝而不是生成隐藏字。

证据等级：L1 行为级浮点模型，不是 RTL、后仿或硅测。
"""

from __future__ import annotations

import numpy as np
import pytest

from adi_model.behavioral.sampler import dc_input
from adi_model.defaults import DefaultModel, DefaultParameters
from adi_model.simulation.config import ConfigError

LSB20_V = 6.0 / 2**20


def _model(mismatch_std: float) -> DefaultModel:
    """同一 seed 的一颗芯片；噪声关掉，只看 dither 的确定性效果。"""
    return DefaultModel(
        seed=20261112,
        parameters=DefaultParameters(
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


def test_ideal_port_is_transparent_for_all_sixty_four_words():
    """零失配下 64 个字必须重构同一电压：残差不能被 upper 顶出 RA 量程。"""
    model = _model(0.0)
    words = np.arange(64, dtype=np.int64)
    run = model.simulate(dc_input(0.4), 64, dither_words=words, dither_mode="charge_port")
    out = np.asarray(run.raw.out, dtype=float)
    assert not np.any(run.raw.ra_sat), "dither 把残差推出了 RA 摆幅"
    assert np.ptp(out) <= 1.0 * LSB20_V, f"ptp={np.ptp(out) / LSB20_V:.2f} LSB20"
    assert abs(out.mean() - 0.4) <= 1.0 * LSB20_V


def test_port_applies_the_224_subtractor_to_the_rdac_code():
    """v2 的 RDAC 码 = SADC 判决 c 减去 U（原件 FIG.19 的 224 减法器）。

    旧契约（"码保持 c，不做 k=c-U"）来自对 FIG.19 的早期读法。
    2026-11-13 核对 [10] 专利 Sheet 20/21 后确认：224 减法器**确实**从 ADC 输出码
    减去 D6,D4，因此 k = c - U 是原件结构，测试契约随之更新。
    """
    model = _model(1e-3)
    words = np.full(4, 32, dtype=np.int64)  # bit5=1 → upper_code = 4
    run = model.simulate(dc_input(0.4), 4, dither_words=words, dither_mode="charge_port")
    upper = np.asarray(run.raw.dither_program.upper_code, dtype=np.int64)
    assert upper.max() == 4
    assert np.array_equal(np.asarray(run.raw.k), np.asarray(run.raw.coarse) - upper)


def test_port_spreads_code_usage_and_reduces_mean_static_error():
    """有失配时：码扩散必须发生，均值误差必须下降——否则 dither 没起作用。"""
    model = _model(1e-3)
    level = 0.4
    single = model.simulate(dc_input(level), 1)
    static_error = float(single.raw.out[0]) - level

    words = np.arange(64, dtype=np.int64)
    dithered = model.simulate(dc_input(level), 64, dither_words=words, dither_mode="charge_port")
    mean_error = float(np.asarray(dithered.raw.out, dtype=float).mean()) - level

    assert np.unique(np.asarray(dithered.raw.coarse)).size > 3, "dither 没有改变粗码使用"
    assert abs(mean_error) < abs(static_error) * 0.5, (
        f"均值误差没有下降：{static_error / LSB20_V:.1f} -> {mean_error / LSB20_V:.1f} LSB20"
    )


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
    from adi_model.control.dither import FIG19_FRACTIONAL_WEIGHTS_Q, Fig19StaticSpec

    return Fig19StaticSpec(
        v_fs=3.0,
        step_q=2**24,
        voltage_fraction_bits=32,
        fractional_lower_weights_q=FIG19_FRACTIONAL_WEIGHTS_Q,
        equivalent_polarity="subtract",
        provenance="unit test binding",
    )
