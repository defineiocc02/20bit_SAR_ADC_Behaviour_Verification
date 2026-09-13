"""Noisy estimation, physical holdout and immutable digital calibration contracts."""

import json
from dataclasses import FrozenInstanceError, fields, replace

import numpy as np
import pytest

from adi_model import Config, sine_input
from adi_model.pipeline import run_pipeline
from adi_model.reconstruction import initialize_state
from adi_model.weight_calibration import (
    CalibrationUnidentifiableError,
    DigitalObservation,
    FrozenCalibration,
    fit_unit_weights,
    run_with_split_calibration,
)


@pytest.fixture(scope="module")
def noisy_fit():
    cfg = Config.paper_literal(
        n_slices=4,
        n_active=1,
        dem_enable=True,
        dem_mode="permute",
        dither_mode="sampling",
        mismatch_sigma0=0.001,
        sadc_rdac_gain_mismatch=0,
    )
    fn = sine_input(2.4, cfg.fs * 103 / 4096)
    training = run_pipeline(cfg, fn, 4096)
    data = DigitalObservation.from_result(training)
    model = fit_unit_weights(data, fn(training.sample.t1))
    return cfg, training, data, model, fn


def test_training_really_contains_noise_and_reports_identifiability(noisy_fit):
    cfg, training, _, model, _ = noisy_fit
    assert cfg.enable_sampling_noise and cfg.ra_enable_noise
    assert np.std(training.sample.n_R) > 10e-6
    assert model.rank == cfg.n_slices * cfg.dac_n_units + 1
    assert 1e-4 < model.residual_rms_v < 3e-3
    assert np.all(model.standard_error > 0)
    assert 1 < model.condition < 1000


@pytest.mark.parametrize("dither", ["sampling", "off", "quantizer"])
def test_independent_same_chip_validation_uses_frozen_coefficients_in_the_runner(noisy_fit, dither):
    cfg, training, _, model, _ = noisy_fit
    validation_cfg = replace(cfg, dither_mode=dither)
    fn = sine_input(2.2, cfg.fs * 307 / 8192, 0.5)
    before = run_pipeline(
        validation_cfg, fn, 8192, pool=training.pool, rng=np.random.default_rng(893)
    )
    state = initialize_state(validation_cfg)
    state.weight_calibration = model
    coefficients_before = model.weights.tobytes()
    after = run_pipeline(
        validation_cfg, fn, 8192, pool=training.pool, state=state, rng=np.random.default_rng(893)
    )
    assert after.pool is training.pool
    assert model.weights.tobytes() == coefficients_before
    assert after.calibration_applied == ("unit_weights",)
    np.testing.assert_array_equal(after.adc2_code, before.adc2_code)
    assert np.std(after.err) < 60e-6
    assert np.std(after.err) < 0.3 * np.std(before.err)


def test_estimator_record_does_not_include_physical_truth(noisy_fit):
    _, training, data, _, _ = noisy_fit
    assert {f.name for f in fields(data.spec)}.isdisjoint(
        {"gain_error", "mismatch_sigma0", "ra_out_noise_rms", "c_feedback0", "C_true"}
    )
    # Poison diagnostics without changing any digital observation.
    old_error, old_gain = training.e_dac, training.g_vec
    training.e_dac, training.g_vec = np.full(4096, np.nan), np.full(4096, np.nan)
    extracted = DigitalObservation.from_result(training)
    np.testing.assert_array_equal(extracted.adc2_code, data.adc2_code)
    np.testing.assert_array_equal(extracted.common_injection_v, data.common_injection_v)
    training.e_dac, training.g_vec = old_error, old_gain
    assert not hasattr(extracted, "pool") and not hasattr(extracted, "x_known")


def test_frozen_coefficients_remain_immutable_after_standard_json_round_trip(noisy_fit):
    _, _, data, model, _ = noisy_fit
    restored = FrozenCalibration.from_dict(json.loads(json.dumps(model.to_dict(), allow_nan=False)))
    np.testing.assert_array_equal(restored.reconstruct(data), model.reconstruct(data))
    with pytest.raises(ValueError):
        restored.weights[0, 0] = 0
    with pytest.raises(ValueError):
        restored.weights.setflags(write=True)
    with pytest.raises(FrozenInstanceError):
        restored.offset_v = 0


def test_missing_excitation_is_rejected_instead_of_hidden_by_nominal_priors(noisy_fit):
    cfg, _, _, _, _ = noisy_fit
    cfg = replace(cfg, dem_enable=False, dem_mode="rotate", dither_mode="off")
    fn = sine_input(2.4, cfg.fs * 101 / 1024)
    r = run_pipeline(cfg, fn, 1024)
    with pytest.raises(CalibrationUnidentifiableError, match="rank.*change DEM"):
        fit_unit_weights(DigitalObservation.from_result(r), fn(r.sample.t1))


def test_dynamic_training_cannot_be_mislabeled_as_static_weights(noisy_fit):
    _, _, data, _, fn = noisy_fit
    with pytest.raises(ValueError, match="controlled static"):
        fit_unit_weights(replace(data, static_charge_domain=False), fn(np.arange(4096) / 40e6))


def test_engineered_workflow_records_the_actual_training_setup(noisy_fit):
    cfg = replace(noisy_fit[0], dither_mode="off", dem_enable=False, dem_mode="rotate")
    r = run_with_split_calibration(cfg, sine_input(2.1, cfg.fs * 127 / 2048), 2048, n_cal=4096)
    report = r.calibration_report
    assert report["same_physical_pool"]
    assert report["sampling_noise_retained"] and report["ra_noise_retained"]
    assert report["training_config"]["dither_mode"] == "sampling"
    assert r.cfg.dither_mode == "off"
    assert r.calibration_applied == ("unit_weights",)
    assert not r.effective_config["calibration_pending"]
    assert r.uncalibrated_out is not None
    assert np.std(r.err) < 60e-6


def test_legacy_gain_training_does_not_silently_disable_sampling_noise(monkeypatch):
    import adi_model.sim as sim_module

    seen = []
    real = sim_module.run_sim

    def inspect(*args, **kwargs):
        r = real(*args, **kwargs)
        seen.append((r.cfg.enable_sampling_noise, float(np.std(r.sample.n_R))))
        return r

    monkeypatch.setattr(sim_module, "run_sim", inspect)
    cfg = Config(calibration="gain", dem_enable=True)
    sim_module.run_with_calibration(cfg, sine_input(1.2, cfg.fs * 17 / 128), 128, n_cal=128)
    assert len(seen) == 10
    assert all(enabled and sigma > 0 for enabled, sigma in seen)


def test_quantized_frozen_weights_preserve_the_holdout_calibration_benefit(noisy_fit):
    cfg, training, _, model, _ = noisy_fit
    state = initialize_state(cfg)
    state.weight_calibration = model
    r = run_pipeline(
        cfg,
        sine_input(2.2, cfg.fs * 307 / 8192, 0.5),
        8192,
        pool=training.pool,
        state=state,
        rng=np.random.default_rng(893),
    )
    stream = r.to_codes()
    assert np.max(np.abs(stream.voltage - r.out)) < 0.51 * cfg.lsb_target
    assert np.std(stream.voltage - r.x_ref) < 60e-6
    assert np.std(stream.voltage - r.x_ref) < 0.3 * np.std(r.uncalibrated_out - r.x_ref)
