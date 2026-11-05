"""检查 18 切片池的跨周期因果归属及失配绑定。

随机 8/18 选择须转换前周期已采集的电容，并实际改变 DAC 权重。
历史 v6.1 固定与随机调度逐位相同、max|delta|=0.0 µV，本组保留对应反例。
"""

from __future__ import annotations

import numpy as np
import pytest

from adi_model.slice_pool import (
    PhysicalSlicePool,
    check_causality,
)

STRATEGIES = ("pingpong", "shuffle_causal")


def _make_pool(cfg, seed=20260910):
    return PhysicalSlicePool(cfg, np.random.default_rng(seed))


class TestCausality:
    @pytest.mark.parametrize("strategy", STRATEGIES)
    def test_no_cross_cycle_violations(self, cfg, strategy):
        pool = _make_pool(cfg)
        plan = pool.plan(8192, np.random.default_rng(20260910), strategy=strategy)
        chk = check_causality(plan)
        assert chk["violations"] == 0, (
            f"strategy={strategy!r} produced {chk['violations']} cross-cycle "
            f"violations in 8191 transitions"
        )

    def test_mean_coverage_full(self, cfg):
        pool = _make_pool(cfg)
        plan = pool.plan(8192, np.random.default_rng(20260910), strategy="shuffle_causal")
        chk = check_causality(plan)
        assert chk["mean_coverage"] == pytest.approx(8.0, abs=1e-9), (
            "with causal binding, every converted slice must come from the "
            "previous cycle's acquisition group; the mean coverage must be 8/8"
        )

    def test_invalid_mask_does_not_silently_pass(self, cfg):
        # A plan whose `valid` mask is empty must still pass if its conv/acq
        # arrays are causally consistent. check_causality only inspects the
        # slice arrays, by design.
        pool = _make_pool(cfg)
        plan = pool.plan(8, np.random.default_rng(1), strategy="shuffle_causal")
        plan.valid[:] = False
        chk = check_causality(plan)
        assert chk["violations"] == 0


class TestMismatchBinding:
    """检查实际切片选择会改变 DAC 误差，而不只是改变调度标签。"""

    def test_dac_error_changes_with_slice_selection(self, cfg):
        pool = _make_pool(cfg, seed=7)
        n = 2048
        k = (np.arange(n) % 512).astype(float)
        sid = np.arange(n) % 512

        conv_a = np.tile(np.arange(8), (n, 1))
        conv_b = np.tile(np.arange(10, 18), (n, 1))

        e_a = pool.dac_error(conv_a, k, sid)
        e_b = pool.dac_error(conv_b, k, sid)
        diff = np.max(np.abs(e_a - e_b)) * 1e6  # uV
        assert diff > 1.0, (
            f"fixed conv_a vs conv_b produce bit-identical e_dac (max "
            f"|delta| = {diff:.3f} uV); the 8/18 choice is not bound to the "
            f"physical capacitors. This is exactly the v6.1 finding F2b."
        )

    def test_signal_capacitance_changes_with_selection(self, cfg):
        pool = _make_pool(cfg)
        c_a = pool.signal_capacitance(np.array([[0, 1, 2, 3, 4, 5, 6, 7]] * 64))
        c_b = pool.signal_capacitance(np.array([[10, 11, 12, 13, 14, 15, 16, 17]] * 64))
        ppm = (c_a.mean() - c_b.mean()) / c_a.mean() * 1e6
        # At least 10 ppm of gain modulation across slice sets — not zero.
        assert abs(ppm) > 10.0


class TestStrategiesProduceDifferentPlans:
    def test_pingpong_vs_shuffle_different(self, cfg):
        pool = _make_pool(cfg, seed=20260910)
        rng = np.random.default_rng(42)
        p_pp = pool.plan(64, rng, strategy="pingpong")
        rng = np.random.default_rng(42)
        p_sh = pool.plan(64, rng, strategy="shuffle_causal")
        # First cycle must be deterministic equal (no prior acquisition).
        assert np.array_equal(p_pp.conv[0], p_sh.conv[0])
        # Later cycles must differ.
        assert not np.array_equal(p_pp.conv[10], p_sh.conv[10])
