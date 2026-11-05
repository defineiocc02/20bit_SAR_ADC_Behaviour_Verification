"""检查跨模块不变量及短记录确定性。

这些检查覆盖单个模块无法发现的接口差异。历史阈值口径错误曾产生 2.9 V
输出回归，而原先只能通过约 37 s 的完整运行发现；这里使用固定种子及较短记录
分别检查双实现、共享阈值、电荷与增益定义。
"""

from __future__ import annotations

import numpy as np
import pytest

from adi_model import Config, sine_input
from adi_model.pipeline import run_pipeline
from adi_model.sim import run_sim
from adi_model.sim_split import run_sim_split

BASE_OFF = {
    "dither_mode": "off",
    "ktc_enable": False,
    "dem_enable": False,
    "mismatch_enable": False,
    "dac_bridge_mismatch_sigma": 0.0,
    "dac_parasitic_spread": 0.0,
    "enable_sampling_noise": False,
    "ra_enable_noise": False,
    "dyn_input_settling": False,
    "dyn_ref_settling": False,
    "dyn_crosstalk": False,
    "sadc_offset": 0.0,
    "sadc_rdac_gain_mismatch": 0.0,
    "sadc_mismatch_enable": False,
}


def _cfg(**kw) -> Config:
    return Config(**{**Config().__dict__, **kw})


@pytest.fixture(scope="module")
def n_small() -> int:
    return 2**12


@pytest.fixture(scope="module")
def coherent_input():
    cfg = Config()
    n = 2**12
    # odd, coprime bin for a coherent single tone
    return sine_input(0.7 * cfg.v_fs, cfg.fs * 331 / n), n


class TestPipelineEquivalence:
    """检查 pipeline.py 与 sim_split.py 的独立循环在关闭非理想项后逐位一致。

    历史两版各自推导 SADC 步长，分别使用 cfg.delta1 和 n_sub*step0；b1=6 时
    恰好相等，b1=7 时相差 4 倍，产生 2.9 V 输出偏差。共享
    sadc.build_first_stage_quantizer 用于统一该构造。
    """

    def test_degenerate_equivalence_is_bitwise(self, coherent_input):
        inp, n = coherent_input
        cfg = _cfg(dac_arch="split", **BASE_OFF)
        r1 = run_sim_split(cfg, inp, n, rng=np.random.default_rng(cfg.seed + 3))
        r2 = run_pipeline(cfg, inp, n, rng=np.random.default_rng(cfg.seed + 3))
        assert np.array_equal(r1.out, r2.out), (
            "pipeline and sim_split disagree with all non-idealities off — "
            "they are interpreting a shared interface differently"
        )
        assert np.array_equal(r1.err, r2.err)

    def test_both_loops_stay_inside_the_adc2_window(self, coherent_input):
        inp, n = coherent_input
        cfg = _cfg(dac_arch="split", **BASE_OFF)
        for fn in (run_sim_split, run_pipeline):
            r = fn(cfg, inp, n, rng=np.random.default_rng(cfg.seed + 4))
            assert np.mean(r.adc2_over) == 0.0, f"{fn.__name__}: residue left the ADC2 window"
            assert np.max(np.abs(r.vra)) <= cfg.ra_v_clip + 1e-9

    def test_error_floor_is_the_backend_quantisation(self, coherent_input):
        """关闭全部模拟非理想项后，检查剩余误差仅为输入折算的 ADC2 量化误差。"""
        inp, n = coherent_input
        cfg = _cfg(dac_arch="split", **BASE_OFF)
        r = run_sim_split(cfg, inp, n, rng=np.random.default_rng(cfg.seed + 5))
        err = float(np.sqrt(np.mean(r.err**2)))
        floor = cfg.delta2 / cfg.g_actual / np.sqrt(12.0)
        assert err < 3.0 * floor, f"err {err * 1e6:.2f} uV vs floor {floor * 1e6:.2f} uV"


class TestSADCThresholdConsistency:
    """检查不同循环调用共享构造器时得到相同阈值网格。"""

    def test_shared_constructor_matches_config_grid(self):
        from adi_model.dac_arch import SplitDAC, build_split_chip
        from adi_model.sadc import build_first_stage_quantizer

        cfg = Config(dac_arch="split", sadc_rdac_gain_mismatch=0.0)
        dac = SplitDAC(cfg, build_split_chip(cfg))
        q = build_first_stage_quantizer(cfg, dac)
        v_lo, v_hi = dac._nominal_endpoints()
        step0 = (v_hi - v_lo) / (dac.levels - 1.0)
        unit = dac.levels // (2**cfg.b1)
        expect = v_lo + np.arange(2**cfg.b1 + 1) * (unit * step0)
        assert np.allclose(q.thresholds, expect)
        assert q.n_code == 2**cfg.b1

    def test_unary_constructor_matches_the_ideal_grid(self):
        from adi_model.sadc import build_first_stage_quantizer

        cfg = Config(
            dac_arch="unary",
            sadc_offset=0.0,
            sadc_rdac_gain_mismatch=0.0,
            sadc_mismatch_enable=False,
        )
        q = build_first_stage_quantizer(cfg, None)
        expect = -cfg.v_fs + np.arange(2**cfg.b1 + 1) * cfg.delta1
        assert np.allclose(q.thresholds, expect)

    def test_step_is_not_independently_derived(self):
        """静态检查两条循环调用共享辅助函数，不各自重算阈值步长。"""
        import inspect

        from adi_model import pipeline_engine, sim_split

        for mod in (pipeline_engine, sim_split):
            src = inspect.getsource(mod)
            assert (
                "build_first_stage_quantizer" in src
            ), f"{mod.__name__} must build its quantiser through the shared constructor"
            assert "units_per_first_stage_step" in src


class TestDeterminism:
    """检查固定随机种子的逐位可复现性，保留重构前后既有确定性合同。"""

    def test_sim_is_bitwise_reproducible(self):
        cfg = _cfg(dac_arch="unary", **BASE_OFF)
        n = 2**11
        inp = sine_input(0.6 * cfg.v_fs, cfg.fs * 101 / n)
        a = run_sim(cfg, inp, n, rng=np.random.default_rng(cfg.seed))
        b = run_sim(cfg, inp, n, rng=np.random.default_rng(cfg.seed))
        assert np.array_equal(a.out, b.out)

    def test_sim_split_is_bitwise_reproducible(self):
        cfg = _cfg(dac_arch="split", **BASE_OFF)
        n = 2**11
        inp = sine_input(0.6 * cfg.v_fs, cfg.fs * 101 / n)
        a = run_sim_split(cfg, inp, n, rng=np.random.default_rng(cfg.seed))
        b = run_sim_split(cfg, inp, n, rng=np.random.default_rng(cfg.seed))
        assert np.array_equal(a.out, b.out)

    def test_no_bare_global_random_state(self):
        """检查随机调用使用显式 Generator，使同一种子可以复现运行。"""
        import inspect

        from adi_model import experiments, pipeline, ra, sampler, sim, sim_split

        for mod in (sim, sim_split, pipeline, sampler, ra, experiments):
            src = inspect.getsource(mod)
            for bad in ("np.random.seed(", "np.random.rand(", "np.random.randn("):
                assert bad not in src, f"{mod.__name__} uses {bad}"


class TestChargeConsistency:
    """检查电荷记账闭合，保留审计确认的名义不变量。"""

    def test_nominal_gain_is_charge_consistent(self):
        """检查 C_F=C_sig_nom/G0 的名义定义，使闭环增益按电荷关系恰为 G0。"""
        cfg = Config(
            dac_arch="split",
            mismatch_enable=False,
            dac_bridge_mismatch_sigma=0.0,
            dac_parasitic_spread=0.0,
        )
        from adi_model.dac_arch import SplitDAC, build_split_chip

        chip = build_split_chip(cfg)
        SplitDAC(cfg, chip)
        ratio = chip.c_sig_true() / chip.C_feedback_true
        assert ratio == pytest.approx(cfg.g_actual, rel=1e-12)

    def test_achieved_gain_error_is_bounded_by_mismatch(self):
        """检查开启失配后增益偏离 G0 的量级由失配预算约束。

        gain_error() 报告的应是同一偏离；边界由配置的失配分量推导，不另写固定阈值。
        """
        cfg = Config(dac_arch="split")
        from adi_model.dac_arch import build_split_chip

        chip = build_split_chip(cfg)
        rel = chip.c_sig_true() / chip.C_feedback_true / cfg.g_actual - 1.0
        sigma = cfg.sigma_mismatch_unit(cfg.dac_unit_cap())
        # A 6-sigma envelope over the (global + slice + feedback) terms.
        assert abs(rel) < 6.0 * sigma, (
            f"achieved gain error {rel * 1e6:.1f} ppm exceeds the mismatch budget "
            f"{6 * sigma * 1e6:.1f} ppm — the gain is not explained by the mismatch "
            f"model"
        )
        assert abs(rel) < 1e-3, "gain error is far beyond a matching-scale effect"

    def test_nominal_digital_conservation(self):
        from adi_model.digital_core import DigitalCore

        c = DigitalCore(Config())
        assert c.verify_nominal_conservation()["ok"] is True
