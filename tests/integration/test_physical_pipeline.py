"""检查显式小型流水线夹具；其参数和结果不代表原芯片或器件性能。"""

from dataclasses import replace

import numpy as np
import pytest

from adi_model.pipeline import run_pipeline
from adi_model.sampler import AnalyticInput, dc_input, sine_input
from adi_model.slice_pool import PhysicalSlicePool


def test_nominal_signal_matches_independent_charge_and_backend_limits(explicit_pipeline_factory):
    cfg, controls = explicit_pipeline_factory(n_samples=32)
    source = sine_input(2.0, cfg.fs * 3 / 32, 0.2)
    result = run_pipeline(cfg, source, 32, **controls)
    nominal = -3 + 6 * result.coarse / 512
    np.testing.assert_allclose(result.vd_true, nominal, atol=2e-14, rtol=0)
    np.testing.assert_allclose(
        result.vra, 32 * (result.sample.input_v - nominal), atol=2e-12, rtol=0
    )
    assert not result.analog_overflow.any()
    assert result.nominal_diagnostic_available
    assert np.max(abs(result.err)) <= cfg.delta2 / (2 * 32) + 2e-12
    assert result.effective_config["output_kind"] == "nominal_scalar_diagnostic"
    assert "not independently proved" in result.binding_metadata["source_alignment"]


def test_actual_acquisition_is_bound_to_ids_and_the_previous_control_group(
    explicit_pipeline_factory,
):
    cfg, controls = explicit_pipeline_factory(n_samples=32)
    result = run_pipeline(cfg, dc_input(0.2), 32, **controls)
    np.testing.assert_array_equal(result.conv_slice_ids, result.acq_slice_ids)
    np.testing.assert_array_equal(
        np.sort(result.conv_slice_ids[1:], axis=1), np.sort(result.next_acq_slice_ids[:-1], axis=1)
    )
    np.testing.assert_array_equal(
        result.held_sample_ids, np.broadcast_to(result.sample_id[:, None], (32, 8))
    )
    assert {16, 17} <= set(result.conv_slice_ids.ravel())
    assert result.sample.acquisition_start_s[0] == pytest.approx(-1 / cfg.fs)
    assert result.pool.n_conversions.sum() == 32 * 8


def test_physical_perturbation_moves_only_samples_using_that_slice(explicit_pipeline_factory):
    cfg, controls = explicit_pipeline_factory(n_samples=32)
    source = sine_input(1.5, cfg.fs * 3 / 32, 0.2)
    before = run_pipeline(cfg, source, 32, **controls)
    old_pool = controls["pool"]
    caps = old_pool.chip.C_main.copy()
    caps[4, 0] *= 1.02
    new_pool = PhysicalSlicePool(
        cfg,
        chip=replace(old_pool.chip, C_main=caps),
        nominal_codebook=old_pool.nominal_codebook,
        mask_binding=old_pool.mask_binding,
        acquisition_binding=old_pool.acquisition_binding,
        schedule_binding=old_pool.schedule_binding,
    )
    after = run_pipeline(cfg, source, 32, **{**controls, "pool": new_pool})
    uses = np.any(before.conv_slice_ids == 4, axis=1)
    assert uses.any() and not uses.all()
    assert np.any(after.vra[uses] != before.vra[uses])
    np.testing.assert_array_equal(after.vra[~uses], before.vra[~uses])
    np.testing.assert_array_equal(after.coarse, before.coarse)
    np.testing.assert_array_equal(after.main_masks, before.main_masks)


def test_sadc_only_response_error_changes_coarse_but_self_heals_inside_explicit_window(
    explicit_pipeline_factory,
):
    cfg, controls = explicit_pipeline_factory(n_samples=32)
    slope = (6 / 512) * cfg.fs
    source = AnalyticInput(
        lambda t: -1.5 + 0.0002 + slope * np.asarray(t),
        lambda t: np.zeros_like(np.asarray(t), dtype=float) + slope,
    )
    before = run_pipeline(cfg, source, 32, **controls)
    after = run_pipeline(replace(cfg, sampling_tau_mismatch=1e-9), source, 32, **controls)
    assert np.any(before.coarse != after.coarse)
    assert not after.analog_overflow.any()
    assert after.digital_observation.static_charge_domain
    assert not after.static_domain_reasons
    assert np.max(abs(after.err)) <= cfg.delta2 / (2 * 32) + 2e-12


def test_noise_does_not_refabricate_capacitors_or_reassign_samples(explicit_pipeline_factory):
    cfg, controls = explicit_pipeline_factory(n_samples=32)
    source = dc_input(0.2)
    base = run_pipeline(cfg, source, 32, **controls)
    noisy_cfg = replace(
        cfg,
        ra_enable_noise=True,
        ra_out_noise_rms=0.0032,
        ra_noise_distribution="gaussian_white",
        seed=9,
    )
    noisy = run_pipeline(noisy_cfg, source, 32, **controls)
    assert noisy.pool.chip is base.pool.chip
    np.testing.assert_array_equal(noisy.conv_slice_ids, base.conv_slice_ids)
    np.testing.assert_array_equal(noisy.main_masks, base.main_masks)
    np.testing.assert_array_equal(noisy.coarse, base.coarse)
    assert np.std(noisy.vra - base.vra) == pytest.approx(0.0032, rel=0.4)


def test_missing_physical_binding_is_rejected_without_synthetic_fallback(explicit_pipeline_factory):
    cfg, controls = explicit_pipeline_factory(n_samples=4)
    pool = controls["pool"]
    missing = PhysicalSlicePool(
        cfg,
        chip=pool.chip,
        nominal_codebook=pool.nominal_codebook,
        acquisition_binding=pool.acquisition_binding,
        schedule_binding=pool.schedule_binding,
    )
    with pytest.raises(ValueError, match="pool.mask_binding"):
        run_pipeline(cfg, dc_input(0.2), 4, **{**controls, "pool": missing})


def test_raw_result_cannot_generate_frozen_integer_coefficients(
    explicit_pipeline_factory, explicit_format
):
    cfg, controls = explicit_pipeline_factory(n_samples=4)
    result = run_pipeline(cfg, dc_input(0.2), 4, **controls)
    with pytest.raises(TypeError):
        result.to_codes()
    with pytest.raises(ValueError, match="frozen effective"):
        result.to_codes(format=explicit_format)
