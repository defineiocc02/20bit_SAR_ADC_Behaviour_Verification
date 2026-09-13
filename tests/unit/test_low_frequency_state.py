"""Physical-time, stationarity, covariance and long-record 1/f checks."""

import numpy as np
import pytest
from scipy.special import sici

from adi_model.low_frequency_noise import BandLimitedFlicker
from adi_model.ra import flicker_series


def test_chunked_and_decimated_queries_retain_one_physical_40msps_state():
    fs = 40e6
    state = BandLimitedFlicker.create((8.8e-9) ** 2, 40, 0.1, np.random.default_rng(7))
    index = np.arange(32768)
    together = state.at_adc_indices(index, fs)
    pieces = np.concatenate(
        [state.at_adc_indices(index[:13317], fs), state.at_adc_indices(index[13317:], fs)]
    )
    np.testing.assert_allclose(pieces, together, rtol=1e-12, atol=1e-20)
    np.testing.assert_allclose(
        state.at_adc_indices(index[::128], fs), together[::128], rtol=1e-12, atol=1e-20
    )
    assert np.std(together) > 0
    # Long-time observations refer to the same 40 MHz clock, spaced 156250
    # physical samples apart (256 Hz observation rate), not a redefined ADC fs.
    long_indices = np.arange(16384, dtype=np.int64) * 156250
    long_record = state.at_adc_indices(long_indices, fs)
    assert long_indices[-1] / fs > 63.99
    assert np.std(long_record) > 10 * np.std(together)


def test_ensemble_covariance_and_drift_follow_band_integrals():
    s0, fc, fl, lag = 1e-16, 40.0, 0.1, 0.003
    target_var = s0 * fc * np.log(fc / fl)
    target_cov = s0 * fc * (sici(2 * np.pi * fc * lag)[1] - sici(2 * np.pi * fl * lag)[1])
    samples = []
    for seed in range(2048):
        state = BandLimitedFlicker.create(s0, fc, fl, np.random.default_rng(seed))
        samples.append(state.at_times([0, lag, 10, 10 + lag]))
    data = np.asarray(samples)
    assert np.var(data[:, 0]) == pytest.approx(target_var, rel=0.09)
    assert np.var(data[:, 2]) == pytest.approx(target_var, rel=0.09)
    assert np.mean(data[:, 0] * data[:, 1]) == pytest.approx(target_cov, rel=0.09)
    assert np.var(data[:, 1] - data[:, 0]) == pytest.approx(2 * (target_var - target_cov), rel=0.09)
    assert np.var(data[:, 3] - data[:, 2]) == pytest.approx(2 * (target_var - target_cov), rel=0.09)


def test_short_record_backfill_has_slow_physical_drift_not_a_full_noise_ramp():
    x = flicker_series(32768, 40e6, 40, 1e-6, np.random.default_rng(0), t_obs=10)
    assert np.count_nonzero(x) == len(x)
    # An arbitrary var/2 linear ramp used to place unresolved power at the
    # record's own frequency. Actual 40 Hz states vary little over 819 us.
    assert np.std(x) < 0.1 * np.sqrt(2e-12 / 40e6 * 40 * np.log(400))
    assert np.max(np.abs(np.diff(x, n=2))) < 1e-17
    no_drift = flicker_series(32768, 40e6, 40, 1e-6, np.random.default_rng(0), include_drift=False)
    assert not np.any(no_drift)


def test_flicker_toggle_retains_identical_white_noise_for_mechanism_comparisons():
    from dataclasses import replace

    from adi_model import Config
    from adi_model.ra import ResidueAmplifier

    cfg = Config(ra_enable_noise=True, flicker_corner_hz=0)
    samples = np.zeros(32768)
    base = ResidueAmplifier(cfg).evaluate(samples, np.random.default_rng(751), g=32)[0]
    slow = ResidueAmplifier(replace(cfg, flicker_corner_hz=40)).evaluate(
        samples, np.random.default_rng(751), g=32
    )[0]
    difference = slow - base
    assert np.max(abs(difference)) > 0
    assert np.std(difference) < np.std(base) * 1e-3
    assert np.max(abs(np.diff(difference, n=2))) < 1e-14
