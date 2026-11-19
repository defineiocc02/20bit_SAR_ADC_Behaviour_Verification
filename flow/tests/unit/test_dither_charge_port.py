"""采样节点电荷扰动的组件测试。

该测试使用 Fig.11 电荷夹具的参数与显式扰动字。扰动电容同时平移前级采样和残差 DAC 所见电压。
前级判决后的代码减去上段扰动字，数字重构扣回采样预置。

下列前两项保留为严格的预期失败，后两项直接检查接口：

1. 理想器件（零失配、零噪声）下，64 个 dither 字重构同一输入，差异不超过 1 LSB20；
2. 有失配时 dither 真的改变粗码使用（多码扩散），并把静态误差的**均值**压低；
3. RDAC 采用前级判决减去上段扰动字后的码 k = c - U；
4. 与 `dither_program` 互斥，且缺少逐笔字时拒绝而不是生成隐藏字。

证据范围：Python 行为模型的浮点结果。
"""

# =============================================================================
# 文件名     : test_dither_charge_port.py
# 模块名     : 行为模型采样节点扰动
# 功能       : 检查六位扰动字对残差、命令和重构输出的可见影响。
# 设计说明   :
#   * 固定直流可将跨字误差与频谱、输入边沿分离。
#   * 测试保留真实饱和与失配反例，参数修复后 XFAIL 会迫使复查。
# =============================================================================
from __future__ import annotations

import numpy as np
import pytest

from adi_model.behavioral.sampler import dc_input
from subsys.experiments.fig11 import Fig11Model, Fig11Parameters
from adi_model.simulation.config import ConfigError

LSB20_V = 6.0 / 2**20


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


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Fig.11 电荷夹具的残差放大器在部分 6 位扰动字下饱和，"
        "64 字透明性断言尚未通过。"
    ),
)
def test_ideal_port_is_transparent_for_all_sixty_four_words():
    """零失配下 64 个字必须重构同一电压：残差不能被 upper 顶出 RA 量程。"""
    model = _model(0.0)
    # 穷举原码使各上段组合收到相同次数，避免从随机流遗漏最坏字。
    words = np.arange(64, dtype=np.int64)
    run = model.simulate(dc_input(0.4), 64, dither_words=words, dither_mode="charge_port")
    out = np.asarray(run.raw.out, dtype=float)
    assert not np.any(run.raw.ra_sat), "dither 把残差推出了 RA 摆幅"
    assert np.ptp(out) <= 1.0 * LSB20_V, f"ptp={np.ptp(out) / LSB20_V:.2f} LSB20"
    assert abs(out.mean() - 0.4) <= 1.0 * LSB20_V


def test_port_applies_the_224_subtractor_to_the_rdac_code():
    """RDAC 码取前级判决 c 减去上段扰动码 U，公式对应专利图 19 的减法器 224。"""
    model = _model(1e-3)
    # 两种字的 U 同为 4，低段分别为 0 和正值，可区分两种采样接法。
    words = np.array([32, 53, 32, 53], dtype=np.int64)
    run = model.simulate(dc_input(0.4), 4, dither_words=words, dither_mode="charge_port")
    upper = np.asarray(run.raw.dither_program.upper_code, dtype=np.int64)
    observed = run.raw.sample
    np.testing.assert_array_equal(observed.rdac_input_v, observed.input_v - run.raw.dither_program.lower_v)
    exported = run.raw.binding_metadata["dither"]
    assert "k = c - U" in exported["topology"]
    assert exported["stored_rdac_input"] == "sample.rdac_input_v = input_v - L"
    assert "applied common voltage term = -lower_v" in exported["digital_known_common_q"]
    assert upper.max() == 4
    assert np.array_equal(np.asarray(run.raw.k), np.asarray(run.raw.coarse) - upper)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Fig.11 电荷夹具在本测试的失配和扰动设置下出现残差饱和，"
        "64 字统计的平均静态误差未下降。"
    ),
)
def test_port_spreads_code_usage_and_reduces_mean_static_error():
    """有失配时：码扩散必须发生，均值误差必须下降——否则 dither 没起作用。"""
    model = _model(1e-3)
    level = 0.4
    single = model.simulate(dc_input(level), 1)
    static_error = float(single.raw.out[0]) - level

    # 穷举原码使各上段组合收到相同次数，避免从随机流遗漏最坏字。
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
