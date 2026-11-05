"""保留外部审计 A07.6 的干净输入／受驱动输入误差分离回归。

审计当时 driver_noise=1 mV 同时进入参考输入和输出，使返回 err 仍约 1 µV，
但对干净输入的误差约 1 mV，输出 SNDR 约 63.43 dB。ADC 内部误差与整个
信号链误差必须分开报告，不能用含同一噪声的参考相减来证明驱动噪声很小。

本文件保留旧 capture、SampleBatch.x1_clean 和 SimResult.err_vs_clean 接口的
误差参考；不同统计域分别检查，不混用内部误差与整链误差。
"""

from __future__ import annotations

import numpy as np
import pytest

from adi_model import Config
from adi_model.chip import build_chip
from adi_model.pipeline import run_pipeline
from adi_model.sampler import sine_input
from adi_model.sim import initialize_state, run_sim
from adi_model.sim_split import run_sim_split

N = 2**13
DRIVER_NOISE_V = 1e-3


def _base_cfg(driver_noise_rms: float) -> Config:
    return Config(
        driver_noise_rms=driver_noise_rms,
        mismatch_enable=False,
        dem_enable=False,
        enable_sampling_noise=False,
        ra_enable_noise=False,
    )


def _run_sim(cfg, fin):
    return run_sim(
        cfg,
        sine_input(0.9 * cfg.v_fs, fin),
        N,
        chip=build_chip(cfg),
        state=initialize_state(cfg),
        rng=np.random.default_rng(4),
    )


def _run_pipeline(cfg, fin):
    return run_pipeline(cfg, sine_input(0.9 * cfg.v_fs, fin), N, rng=np.random.default_rng(4))


def _run_split(cfg, fin):
    return run_sim_split(cfg, sine_input(0.9 * cfg.v_fs, fin), N, rng=np.random.default_rng(4))


CHAINS = {"sim": _run_sim, "pipeline": _run_pipeline, "sim_split": _run_split}


class TestErrorReferenceSeparation:
    """检查 1 mV 驱动噪声不会在整个信号链误差中被报告成约 1 µV。"""

    @pytest.mark.parametrize("chain", sorted(CHAINS))
    def test_driver_noise_hides_in_err_but_not_in_err_vs_clean(self, chain):
        run = CHAINS[chain]
        cfg0 = _base_cfg(0.0)
        cfg1 = _base_cfg(DRIVER_NOISE_V)
        fin = cfg0.fs / 8

        r0 = run(cfg0, fin)
        r1 = run(cfg1, fin)

        err0 = float(np.std(r0.err))
        err1 = float(np.std(r1.err))
        clean1 = float(np.std(r1.err_vs_clean))

        # 两次运行的内部误差仍处于微伏量级，因为
        # 驱动噪声在 out-x1 中抵消；但不要求逐位相同，
        # 因为驱动噪声抽样会推进随机流，
        # 所以这里检查量级而不是精确相等。
        assert (
            err0 < 5e-6 and err1 < 5e-6
        ), f"{chain}: internal error {err1*1e6:.3f} uV is not at the ADC floor"
        # 整个信号链误差应保留驱动噪声贡献。
        assert clean1 == pytest.approx(DRIVER_NOISE_V, rel=0.02), (
            f"{chain}: err_vs_clean = {clean1*1e6:.1f} uV for a 1 mV driver "
            "noise — the whole-chain error is not being reported"
        )
        assert clean1 / max(err1, 1e-30) > 100.0, (
            f"{chain}: the two quantities are not separated "
            f"({clean1*1e6:.1f} uV vs {err1*1e6:.3f} uV) — audit A07.6"
        )

    @pytest.mark.parametrize("chain", sorted(CHAINS))
    def test_err_vs_clean_equals_err_to_x1_without_driver_noise(self, chain):
        """检查无驱动噪声时两个误差参考逐位相同，保留原分析路径。"""
        run = CHAINS[chain]
        cfg = _base_cfg(0.0)
        r = run(cfg, cfg.fs / 8)
        assert np.array_equal(np.asarray(r.err_vs_clean), np.asarray(r.err_to_x1))

    def test_clean_input_is_recorded_before_the_noise(self):
        """检查历史 SampleBatch 显式记录注入噪声前的输入样本。"""
        from adi_model.sampler import capture

        cfg = _base_cfg(DRIVER_NOISE_V)
        b = capture(cfg, sine_input(0.5, cfg.fs / 16), 64, np.random.default_rng(0))
        assert b.x1_clean is not None and b.x2_clean is not None
        # x1 为驱动后的节点，x1_clean 为理想参考。
        assert not np.allclose(b.x1, b.x1_clean)
        assert float(np.std(b.x1 - b.x1_clean)) == pytest.approx(DRIVER_NOISE_V, rel=0.25)

    def test_whole_chain_sndr_degrades_with_driver_noise(self):
        """检查整个信号链指标体现驱动噪声退化；历史 5 MHz、1 mV 对应约 63.4 dB。"""
        from adi_model.metrics import sine_fit_metrics

        cfg0 = _base_cfg(0.0)
        cfg1 = _base_cfg(DRIVER_NOISE_V)
        fin = cfg0.fs / 8

        r0, r1 = _run_pipeline(cfg0, fin), _run_pipeline(cfg1, fin)
        sndr_int0 = sine_fit_metrics(r0.out, cfg0.fs, fin)["SNDR_dB"]
        sndr_int1 = sine_fit_metrics(r1.out, cfg1.fs, fin)["SNDR_dB"]
        # out 为转换器输出，包含驱动噪声的影响。
        assert sndr_int1 < sndr_int0 - 20.0, "driver noise did not degrade the output spectrum"
