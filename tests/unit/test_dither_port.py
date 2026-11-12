"""dither 电荷口、码偏移残余与范围预算的单元测试。

用例覆盖三种几何：等间距 63+8 池（残余应为 0）、Fig.11 17/5 池（残余必须非零）、
以及任务树里那个下界数字的复原式。
"""

from __future__ import annotations

import numpy as np
import pytest

from adi_model.control.dem3d import fig11_masks, unary_rotation_masks
from adi_model.control.dither import FIG19_FRACTIONAL_WEIGHTS_Q, Fig19DitherProgram, Fig19StaticSpec
from adi_model.control.dither_port import (
    DitherPort,
    attenuated_dither_bound,
    code_shift_residual,
    digital_descramble,
    range_ledger,
)
from adi_model.simulation.config import ConfigError
from adi_model.simulation.slice_pool import PhysicalSlicePool
from adi_model.topology.dac_arch import NominalCodebook, SplitChip


def _spec(v_fs: float = 3.0) -> Fig19StaticSpec:
    return Fig19StaticSpec(
        v_fs=v_fs,
        step_q=2**24,
        voltage_fraction_bits=32,
        fractional_lower_weights_q=FIG19_FRACTIONAL_WEIGHTS_Q,
        equivalent_polarity="subtract",
        provenance="unit-test explicit binding",
    )


def _uniform_pool():
    from adi_model.simulation.config import Config

    cap = 4.00390625e-14
    chip = SplitChip(
        C_main=np.full((18, 63), cap),
        C_sub=np.full((18, 8), cap),
        C_bridge=np.full(18, 8 * cap / 7),
        c_p_sub=np.zeros(18),
        c_p_main=np.zeros(18),
        C_feedback_true=640.625e-15,
    )
    book = NominalCodebook(0, 511, -3 + 6 * np.arange(512) / 512)
    return PhysicalSlicePool(
        Config(), chip=chip, nominal_codebook=book, mask_binding=unary_rotation_masks
    )


def _fig11_pool():
    cap = 1e-15
    weights_main = np.array([2.0] * 15 + [1.0, 1.0]) * cap
    weights_sub = np.array([8.0, 4.0, 2.0, 1.0, 1.0]) * cap
    chip = SplitChip(
        C_main=np.tile(weights_main, (18, 1)),
        C_sub=np.tile(weights_sub, (18, 1)),
        C_bridge=np.full(18, cap),
        c_p_sub=np.zeros(18),
        c_p_main=np.zeros(18),
        C_feedback_true=300 * cap,
    )
    book = NominalCodebook(0, 511, -3 + 6 * np.arange(512) / 512)
    return PhysicalSlicePool(
        object.__new__(type("Cfg", (), {"n_slices": 18, "n_active": 8})),
        chip=chip,
        nominal_codebook=book,
        mask_binding=fig11_masks,
    )


def test_port_charge_is_monotone_in_word():
    spec = _spec()
    port = DitherPort(spec, 1e-15 / 8, 6.0)
    words = np.arange(64)
    charge = port.charge_c(words)
    lsb = port.lsb_equivalent(words)
    assert charge[0] == 0.0
    assert charge[-1] > 0
    assert np.all(np.diff(lsb[::9]) >= 0)
    assert charge[-1] == pytest.approx(lsb[-1] * 1e-15 / 8 * 6.0)


def test_port_rejects_bad_geometry():
    with pytest.raises(ConfigError):
        DitherPort(_spec(), 0.0, 6.0)
    with pytest.raises(ConfigError):
        DitherPort("not a spec", 1e-15, 6.0)


def test_code_shift_is_exact_on_uniform_array():
    pool = _uniform_pool()
    spec = _spec()
    codes = np.array([100, 200, 300, 400, 511])
    words = np.array([0, 5, 21, 42, 63])
    c_signal = float(pool.signal_capacitance(np.arange(8)[None, :])[0])
    report = code_shift_residual(
        pool, codes, words, spec=spec,
        reference_high_v=3.0, reference_low_v=-3.0, c_signal_f=c_signal,
    )
    assert report["code_shift_exact"], report
    assert report["max_abs_residual_lsb20"] < 1e-6
    assert report["illegal_shifted_codes"] == 0


def test_code_shift_residual_appears_on_segmented_array():
    pool = _fig11_pool()
    spec = _spec()
    codes = np.arange(6, 512, 7, dtype=np.int64)
    words = np.full(codes.size, 48, dtype=np.int64)  # D6=1,D4=1 -> U=5
    c_signal = float(pool.signal_capacitance(np.arange(8)[None, :])[0])
    report = code_shift_residual(
        pool, codes, words, spec=spec,
        reference_high_v=3.0, reference_low_v=-3.0, c_signal_f=c_signal,
    )
    assert not report["code_shift_exact"]
    assert report["max_abs_residual_lsb20"] > 1.0
    assert report["worst_code"] >= 6


def test_illegal_early_codes_are_counted_not_clipped():
    pool = _uniform_pool()
    spec = _spec()
    codes = np.array([0, 1, 2, 5, 6], dtype=np.int64)
    # word 32 = 0b100000 -> D6=1 -> U=4；word 48 = 0b110000 -> D6=1,D4=1 -> U=5
    words = np.full(5, 32, dtype=np.int64)
    c_signal = float(pool.signal_capacitance(np.arange(8)[None, :])[0])
    report = code_shift_residual(
        pool, codes, words, spec=spec,
        reference_high_v=3.0, reference_low_v=-3.0, c_signal_f=c_signal,
    )
    assert report["illegal_shifted_codes"] == 3  # 0,1,2 越界；5-4=1 与 6-4=2 合法
    assert report["worst_upper_code"] == 4
    assert report["lower_lsb"] == pytest.approx([0.0] * 5)


def test_range_ledger_reports_bounds_and_illegal_events():
    spec = _spec()
    ledger = range_ledger(spec, v_high=3.0, v_low=-3.0)
    delta = spec.coarse_step_v
    assert ledger["max_lower_dither_v"] == pytest.approx(3 * delta)
    assert ledger["sadc_lower_bound_v"] == pytest.approx(-3.0 + 3 * delta)
    assert ledger["sadc_upper_bound_v"] == pytest.approx(3.0)
    # 1 个 9bit 粗码 = 2**20/512 = 2048 LSB20
    assert ledger["lower_deficit_lsb20"] == pytest.approx(3 * 2048)
    assert ledger["illegal_early_events"] > 0
    assert not ledger["code_shift_legal"]


def test_range_ledger_rejects_inverted_domain():
    with pytest.raises(ConfigError):
        range_ledger(_spec(), v_high=-3.0, v_low=3.0)


def test_attenuated_bound_reproduces_task_tree_number():
    report = attenuated_dither_bound(5.0)
    assert report["lower_bound_v"] == pytest.approx(-4.855238970588, abs=1e-9)
    assert report["matches_task_tree_number"]
    # 不同 v_fs 不宣称匹配
    other = attenuated_dither_bound(3.0)
    assert not other["matches_task_tree_number"]
    with pytest.raises(ConfigError):
        attenuated_dither_bound(0.0)


def test_digital_descramble_is_single_subtraction():
    total = np.array([0.0, 0.01, 0.02])
    out = digital_descramble(total, reference_high_v=3.0, reference_low_v=-3.0)
    assert np.array_equal(out, -total)
    with pytest.raises(ConfigError):
        digital_descramble(np.array([10.0]), reference_high_v=3.0, reference_low_v=-3.0)


def test_program_total_matches_port_for_all_words():
    spec = _spec()
    port = DitherPort(spec, 1e-15 / 8, 6.0)
    program = Fig19DitherProgram.from_words(np.arange(64), spec=spec)
    assert np.allclose(port.input_referred_v(np.arange(64)), program.total_v)
