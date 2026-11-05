"""检查显式有限频带高斯功率谱状态及独立的协方差积分。"""

import numpy as np
import pytest
from scipy.special import sici

from adi_model.config import Config
from adi_model.low_frequency_noise import BandLimitedFlicker
from adi_model.ra import ResidueAmplifier


def _state(seed):
    return BandLimitedFlicker.create(
        1e-12,
        40.0,
        0.1,
        np.random.default_rng(seed),
        high_hz=20.0,
        modes=256,
        reference_node="synthetic RA-input-equivalent V",
        distribution="gaussian",
    )


def test_chunked_and_decimated_queries_retain_one_physical_clock_state():
    state = _state(17)
    fs = 40e6
    index = np.arange(64, dtype=np.int64)
    full = state.at_adc_indices(index, fs)
    parts = np.r_[state.at_adc_indices(index[:31], fs), state.at_adc_indices(index[31:], fs)]
    np.testing.assert_allclose(parts, full, rtol=1e-12, atol=1e-20)
    np.testing.assert_allclose(
        state.at_adc_indices(index[::8], fs), full[::8], rtol=1e-12, atol=1e-20
    )
    long_index = np.arange(4096, dtype=np.int64) * 625000
    long_record = state.at_adc_indices(long_index, fs)
    assert long_index[-1] / fs > 63
    assert np.std(long_record) > 100 * np.std(full)
    assert np.all((state.frequency_hz > 0.1) & (state.frequency_hz < 20.0))


def test_ensemble_covariance_and_difference_variance_follow_band_integrals():
    lag = 0.01
    values = np.array([_state(seed).at_times(np.array([0.0, lag])) for seed in range(512)])
    variance = 1e-12 * 40 * np.log(20 / 0.1)
    covariance = 1e-12 * 40 * (sici(2 * np.pi * 20 * lag)[1] - sici(2 * np.pi * 0.1 * lag)[1])
    assert np.var(values[:, 0]) == pytest.approx(variance, rel=0.15)
    assert np.mean(values[:, 0] * values[:, 1]) == pytest.approx(covariance, rel=0.15)
    assert np.var(values[:, 1] - values[:, 0]) == pytest.approx(
        2 * (variance - covariance), rel=0.15
    )
    assert _state(17).expected_variance_v2 == pytest.approx(variance, rel=1e-14)


def test_supplied_low_frequency_input_does_not_redraw_ra_white_noise():
    cfg = Config(
        ra_v_clip=2.0,
        ra_enable_noise=True,
        ra_out_noise_rms=0.001,
        ra_noise_distribution="gaussian_white",
        seed=37,
    )
    amplifier = ResidueAmplifier(cfg)
    count = 64
    error = _state(19).at_adc_indices(np.arange(count), 40e6)
    gain = np.full(count, 32.0)
    baseline, _ = amplifier.evaluate(np.zeros(count), np.random.default_rng(37), gain)
    supplied, _ = amplifier.evaluate(
        np.zeros(count), np.random.default_rng(37), gain, input_error_v=error
    )
    np.testing.assert_allclose(supplied - baseline, gain * error, rtol=0, atol=1e-18)


@pytest.mark.parametrize("missing", ["high_hz", "modes", "reference_node", "distribution"])
def test_process_band_node_distribution_and_resolution_have_no_hidden_defaults(missing):
    keywords = {
        "high_hz": 20.0,
        "modes": 16,
        "reference_node": "synthetic node V",
        "distribution": "gaussian",
    }
    keywords.pop(missing)
    with pytest.raises(TypeError):
        BandLimitedFlicker.create(1e-12, 40, 0.1, np.random.default_rng(1), **keywords)


@pytest.mark.parametrize(
    "change",
    [
        {"white_density_v2_hz": -1e-12},
        {"corner_hz": np.nan},
        {"low_hz": 0.0},
        {"high_hz": 50.0},
        {"modes": 3},
        {"reference_node": ""},
        {"distribution": "unspecified"},
    ],
)
def test_invalid_process_definition_is_rejected(change):
    inputs = {
        "white_density_v2_hz": 1e-12,
        "corner_hz": 40.0,
        "low_hz": 0.1,
        "rng": np.random.default_rng(1),
        "high_hz": 20.0,
        "modes": 16,
        "reference_node": "synthetic node V",
        "distribution": "gaussian",
    }
    inputs.update(change)
    with pytest.raises(ValueError):
        BandLimitedFlicker.create(**inputs)


def test_zero_declared_psd_is_zero_and_does_not_invent_extra_noise():
    state = BandLimitedFlicker.create(
        0.0,
        40.0,
        0.1,
        np.random.default_rng(5),
        high_hz=20.0,
        modes=16,
        reference_node="synthetic node V",
        distribution="gaussian",
    )
    np.testing.assert_array_equal(state.at_times(np.array([0.0, 1.0, 2.0])), 0.0)
    assert state.expected_variance_v2 == 0.0


@pytest.mark.parametrize("time", [[np.nan], [np.inf], [1 + 1j]])
def test_nonfinite_or_complex_physical_timestamps_are_rejected(time):
    with pytest.raises(ValueError):
        _state(5).at_times(time)
