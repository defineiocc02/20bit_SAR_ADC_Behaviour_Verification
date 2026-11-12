"""检查显式解析导数与物理切片编号对应的采样时偏，不隐式回退到 FFT 求导。"""

import numpy as np
import pytest

from adi_model.behavioral.sampler import (
    AnalyticInput,
    dc_input,
    input_derivative,
    sine_input,
    spectral_derivative,
)
from adi_model.simulation.pipeline import run_pipeline
from adi_model.simulation.slice_pool import PhysicalSlicePool


@pytest.mark.parametrize("frequency", [1e3, 1e6, 19e6])
def test_analytic_derivative_is_exact(frequency):
    amplitude, phase = 1.3, 0.2
    time = np.arange(4000) / 40e6
    actual = input_derivative(sine_input(amplitude, frequency, phase), time)
    expected = amplitude * 2 * np.pi * frequency * np.cos(2 * np.pi * frequency * time + phase)
    np.testing.assert_allclose(actual, expected, rtol=0, atol=1e-7)


def test_explicit_periodic_spectral_derivative_beats_central_difference():
    fs, frequency, n = 40e6, 19e6, 4000
    time = np.arange(n) / fs
    values = np.sin(2 * np.pi * frequency * time)
    expected = 2 * np.pi * frequency * np.cos(2 * np.pi * frequency * time)
    actual = spectral_derivative(values, 1 / fs)
    np.testing.assert_allclose(actual, expected, rtol=0, atol=0.002)
    wrong = (np.roll(values, -1) - np.roll(values, 1)) * fs / 2
    ratio = np.linalg.norm(wrong) / np.linalg.norm(expected)
    assert ratio == pytest.approx(0.0524, abs=0.0005)
    assert 20 * np.log10(ratio) == pytest.approx(-25.6, abs=0.2)


def test_derivative_is_invariant_to_frequency_scaling():
    time = np.arange(4000) / 40e6
    first = input_derivative(sine_input(1.0, 1e6, 0.0), time)
    second = input_derivative(sine_input(1.0, 2e6, 0.0), time)
    assert np.std(second) / np.std(first) == pytest.approx(2.0, rel=1e-12)


def test_dc_input_has_zero_slope():
    np.testing.assert_array_equal(input_derivative(dc_input(0.3), np.arange(16) / 40e6), 0)


def test_plain_callable_requires_a_declared_derivative_instead_of_automatic_fallback():
    with pytest.raises(ValueError, match="derivative"):
        input_derivative(lambda t: np.sin(t), np.arange(16) / 40e6)


@pytest.mark.parametrize("bad", [np.nan, np.inf, -np.inf])
def test_nonfinite_declared_derivative_is_rejected(bad):
    source = AnalyticInput(lambda t: np.zeros_like(t), lambda t: np.full_like(t, bad))
    with pytest.raises(ValueError, match="finite"):
        input_derivative(source, np.arange(16) / 40e6)


def _with_skew(cfg, controls):
    original = controls["pool"]
    skew = np.linspace(-10e-12, 10e-12, 18)
    pool = PhysicalSlicePool(
        cfg,
        chip=original.chip,
        nominal_codebook=original.nominal_codebook,
        mask_binding=original.mask_binding,
        acquisition_binding=original.acquisition_binding,
        schedule_binding=original.schedule_binding,
        t_skew=skew,
    )
    return {**controls, "pool": pool}, skew


def test_skew_enters_the_pipeline_at_each_actual_physical_aperture(
    explicit_pipeline_factory,
):
    cfg, controls = explicit_pipeline_factory(n_samples=16)
    source = sine_input(1.0, 19e6, 0.2)
    baseline = run_pipeline(cfg, source, 16, **controls)
    changed, skew = _with_skew(cfg, controls)
    result = run_pipeline(cfg, source, 16, **changed)
    time = result.sample.aperture_s
    exact_values = source(time[:, None] + skew[result.acq_slice_ids])
    expected = 32 * np.mean(exact_values - source(time)[:, None], axis=1)
    first_order = 32 * np.mean(skew[result.acq_slice_ids], axis=1) * input_derivative(source, time)
    assert np.max(abs(expected - first_order)) > 1e-6
    assert np.max(abs(expected)) > 1e-4
    np.testing.assert_allclose(result.vra - baseline.vra, expected, rtol=0, atol=5e-12)
    np.testing.assert_array_equal(result.coarse, baseline.coarse)
    assert not result.analog_overflow.any()
    assert not result.digital_observation.static_charge_domain


def test_skew_path_can_evaluate_a_plain_callable_at_actual_apertures(explicit_pipeline_factory):
    cfg, controls = explicit_pipeline_factory(n_samples=16)
    controls, _ = _with_skew(cfg, controls)
    source = sine_input(1.0, 19e6, 0.2)
    analytic = run_pipeline(cfg, source, 16, **controls)
    plain = run_pipeline(
        cfg, lambda t: np.sin(2 * np.pi * 19e6 * np.asarray(t) + 0.2), 16, **controls
    )
    np.testing.assert_allclose(plain.vra, analytic.vra, rtol=0, atol=1e-14)


def test_response_approximation_still_requires_a_declared_analytic_derivative(
    explicit_pipeline_factory,
):
    cfg, controls = explicit_pipeline_factory(
        n_samples=16, config_changes={"sampling_tau_mismatch": 1e-9}
    )
    with pytest.raises(ValueError, match="derivative"):
        run_pipeline(cfg, lambda t: np.sin(2 * np.pi * 19e6 * np.asarray(t)), 16, **controls)
