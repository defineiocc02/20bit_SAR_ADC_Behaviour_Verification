"""Independent perturbations from the September 2026 physical-model review."""

from dataclasses import replace

import numpy as np
import pytest

from adi_model import Config, build_split_chip, sine_input
from adi_model.config import ConfigError
from adi_model.pipeline import run_pipeline
from adi_model.sadc import SADC
from adi_model.sim_split import run_sim_split

BASE = Config(
    dac_arch="split",
    mismatch_enable=False,
    enable_sampling_noise=False,
    ra_enable_noise=False,
    sadc_rdac_gain_mismatch=0.0,
)
N = 2048
INPUT = sine_input(2.7, BASE.fs * 251 / N)


@pytest.mark.parametrize("runner", [run_sim_split, run_pipeline])
@pytest.mark.parametrize(
    "change",
    [
        {"sadc_offset": 0.001},
        {"sadc_rdac_gain_mismatch": 0.001},
        {"sadc_mismatch_enable": True, "sadc_mismatch_sigma": 0.001},
    ],
)
def test_sadc_changes_decisions_consistently(runner, change):
    baseline = runner(BASE, INPUT, N)
    cfg = replace(BASE, **change)
    changed = runner(cfg, INPUT, N)
    other = (run_pipeline if runner is run_sim_split else run_sim_split)(cfg, INPUT, N)
    assert np.count_nonzero(changed.coarse != baseline.coarse) > 0
    np.testing.assert_array_equal(changed.coarse, other.coarse)
    np.testing.assert_array_equal(changed.out, other.out)


@pytest.mark.parametrize("bank", ["main", "sub"])
@pytest.mark.parametrize("runner", [run_sim_split, run_pipeline])
@pytest.mark.parametrize("discrete", [False, True])
def test_dither_bank_units_cancel_in_ideal_chain(bank, runner, discrete):
    cfg = replace(BASE, dither_mode="sampling", dither_split_bank=bank, dither_discrete=discrete)
    result = runner(cfg, INPUT, N)
    # Independent bank weight from the declared 64+8 topology, beta=1/8.
    step = 6.0 / 65.0 * (1.0 if bank == "main" else 1 / 8)
    np.testing.assert_allclose(
        result.sample.dither, result.sample.dither_bank_code * step, atol=1e-15
    )
    assert not np.any(result.adc2_over)
    assert np.max(np.abs(result.err)) <= cfg.delta2 / (2 * cfg.g0 * cfg.dither_alpha) + 1e-14


@pytest.mark.parametrize("bank", ["main", "sub"])
def test_mask_removes_real_input_charge(bank):
    cfg = replace(BASE, dither_mode="sampling", dither_split_bank=bank, dither_discrete=True)
    chip = build_split_chip(cfg)
    caps = chip.C_main if bank == "main" else chip.C_sub
    caps[-1] *= 1.01
    result = run_sim_split(cfg, INPUT, N, chip=chip)
    # Eliminate the floating subnode from its charge equation, then form the
    # signal charge directly from connected caps (not the injection helper).
    beta = chip.C_bridge / (chip.C_bridge + chip.C_sub.sum() + chip.c_p_sub)
    c_signal = chip.C_main.sum() + beta * chip.C_sub.sum()
    q_signal = (c_signal - caps[-4:].sum() * (beta if bank == "sub" else 1)) * result.sample.x1
    np.testing.assert_allclose(
        result.sample.x_rdac - result.sample.dither,
        q_signal / c_signal,
        atol=1e-14,
    )
    assert np.all(np.abs(result.sample.signal_alpha - cfg.dither_alpha) > 1e-7)


@pytest.mark.parametrize("thresholds", [[0, 1, 0.5], [0, np.nan, 1], [0, 0, 1]])
def test_invalid_threshold_arrays_cannot_reach_searchsorted(thresholds):
    with pytest.raises(ConfigError, match="thresholds"):
        SADC(BASE, thresholds=np.asarray(thresholds))


def test_mismatch_is_fixed_and_nominal_input_is_not_mutated():
    cfg = replace(BASE, sadc_mismatch_enable=True, sadc_mismatch_sigma=0.001)
    thresholds = np.linspace(-3, 3, 129)
    original = thresholds.copy()
    a, b = SADC(cfg, thresholds=thresholds), SADC(cfg, thresholds=thresholds)
    np.testing.assert_array_equal(a.thresholds, b.thresholds)
    np.testing.assert_array_equal(thresholds, original)
    assert not np.array_equal(a.thresholds, original)


def test_feedback_override_and_run_contract_are_observable():
    import json

    chip = build_split_chip(BASE)
    cfg = replace(
        BASE,
        split_feedback_cap_f=2 * chip.C_feedback_nominal,
        c_feedback0=2 * BASE.c_feedback0,
        calibration="gain",
    )
    result = run_sim_split(cfg, INPUT, N)
    np.testing.assert_allclose(result.g_vec, BASE.g0 / 2)
    metadata = result.effective_config
    assert metadata["feedback_cap_f"] == pytest.approx(2 * chip.C_feedback_nominal)
    assert metadata["calibration_pending"]
    assert "c_feedback0" in metadata["inactive_overrides"]
    json.dumps(metadata, allow_nan=False)


def test_sampling_conversion_is_idempotent():
    from adi_model.dac_arch import SplitDAC
    from adi_model.sampling_charge import sampling_dither_injection

    cfg = replace(BASE, dither_mode="sampling", dither_split_bank="main", dither_discrete=True)
    result = run_sim_split(cfg, INPUT, N)
    before = result.sample.x_rdac.copy()
    codes = result.sample.dither_code.copy()
    dac = SplitDAC(cfg, result.chip)
    lo, hi = dac._nominal_endpoints()
    sampling_dither_injection(
        cfg, result.chip, result.sample, (hi - lo) / (dac.levels - 1), result.chip.c_sig_true()
    )
    np.testing.assert_allclose(result.sample.x_rdac, before, atol=1e-15)
    np.testing.assert_array_equal(result.sample.dither_code, codes)


@pytest.mark.parametrize("D", [-1, 0.5, 5, float("nan")])
def test_invalid_sampling_masks_are_rejected(D):
    cfg = replace(BASE, dither_mode="sampling", dither_units_range=D)
    with pytest.raises(ConfigError, match="dither"):
        run_sim_split(cfg, INPUT, N)
