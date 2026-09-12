"""Architecture candidates are checked against charge and range, not labels."""

from dataclasses import replace

import numpy as np
import pytest

from adi_model import Config, build_split_chip, sine_input
from adi_model.dac_arch import SplitDAC
from adi_model.pipeline import run_pipeline
from adi_model.sadc import build_first_stage_quantizer
from adi_model.sim import run_sim
from adi_model.sim_split import run_sim_split


def ideal_literal(**overrides):
    return Config.paper_literal(
        mismatch_enable=False,
        enable_sampling_noise=False,
        ra_enable_noise=False,
        sadc_rdac_gain_mismatch=0,
        **overrides,
    )


def test_literal_decisions_and_dither_ratio_are_independent():
    cfg = ideal_literal()
    cfg.check_legal()
    dac = SplitDAC(cfg, build_split_chip(cfg))
    q = build_first_stage_quantizer(cfg, dac)
    assert q.n_code == 512
    assert cfg.dither_rdac_ratio == 4
    assert cfg.adc2_n_bits == 12
    assert cfg.adc2_v_max == pytest.approx(0.4125)
    assert "dither 增强位数 == log2(DAC 电平数 / 2**b1)" not in cfg.validate(False)
    other = replace(cfg, dither_enhancement_bits=3)
    assert other.dither_rdac_ratio == 8
    np.testing.assert_array_equal(q.thresholds, build_first_stage_quantizer(other, dac).thresholds)


def test_complete_count_topology_covers_full_scale_with_real_capacitors():
    cfg = ideal_literal()
    chip = build_split_chip(cfg)
    dac = SplitDAC(cfg, chip)
    k = np.arange(512)
    actual = dac.evaluate_physical(k, np.zeros_like(k))
    # Independent bottom-plate charge sum; 63 main caps plus 8 attenuated sub.
    cm, cs = k // 8, k % 8
    expected = 3 * ((2 * cm - 63) + (2 * cs - 8) / 8) / 64
    np.testing.assert_allclose(actual, expected, atol=2e-15)
    np.testing.assert_allclose(actual, -3 + k * 6 / 512, atol=2e-15)
    assert chip.A + chip.B + chip.C_bridge == pytest.approx(cfg.c_total0, rel=1e-14)
    assert cfg.nominal_rdac_step == pytest.approx(6 / 512)


@pytest.mark.parametrize("runner", [run_sim_split, run_pipeline])
@pytest.mark.parametrize("mode", ["off", "sampling", "quantizer"])
def test_nine_bit_chain_and_dither_ports_reconstruct(runner, mode):
    cfg = ideal_literal(dither_mode=mode)
    n = 2048
    result = runner(cfg, sine_input(2.7, cfg.fs * 251 / n), n)
    assert not np.any(result.rdac_over)
    assert not np.any(result.adc2_over)
    assert np.max(np.abs(result.err)) <= cfg.delta2 / (2 * cfg.g0 * cfg.dither_alpha) + 1e-13
    if mode == "quantizer":
        np.testing.assert_allclose(result.sample.rdac_dither, 4 * result.sample.dither)
        np.testing.assert_allclose(
            result.sample.x_rdac - result.sample.x1, 4 * result.sample.dither, atol=1e-15
        )


def test_full_input_dc_boundaries_and_clipping_are_observable():
    cfg = ideal_literal()
    # Sweep just above and below every coarse boundary, including both rails.
    values = np.ravel(
        np.column_stack((np.linspace(-3, 3, 513) - 1e-8, np.linspace(-3, 3, 513) + 1e-8))
    )
    result = run_sim_split(cfg, lambda t: values[: len(t)], len(values))
    in_range = (values >= -3) & (values < 3)
    assert not np.any(result.adc2_over[in_range])
    assert np.max(np.abs(result.err[in_range])) < cfg.delta2 / (2 * cfg.g0) + 1e-13
    # A deliberately excessive dither amplitude consumes real command range.
    stressed = replace(cfg, dither_mode="quantizer", dither_amplitude_lsb1=64)
    r = run_pipeline(stressed, sine_input(2.9, cfg.fs * 101 / 1024), 1024)
    assert np.any(r.rdac_over)
    assert r.effective_config["rdac_overflow_count"] > 0


def test_unary_quantizer_dither_uses_the_same_two_port_contract():
    cfg = replace(ideal_literal(dither_mode="quantizer"), dac_arch="unary")
    n = 2048
    r = run_sim(cfg, sine_input(2.5, cfg.fs * 251 / n), n)
    assert not np.any(r.adc2_over)
    assert np.max(np.abs(r.err)) < cfg.delta2 / (2 * cfg.g0) + 1e-13
