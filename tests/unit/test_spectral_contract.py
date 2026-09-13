"""Independent density, harmonic-rank and endpoint normalization checks."""

import numpy as np
import pytest
from scipy.signal import periodogram

from adi_model.metrics import (
    _bh4,
    integrate_noise_band,
    power_spectral_density,
    sine_fit_metrics,
    spectrum_dbfs,
)


@pytest.mark.parametrize("n", [4095, 4096])
@pytest.mark.parametrize("window", ["boxcar", "hann", "blackman", "blackmanharris"])
def test_density_matches_scipy_and_window_weighted_parseval(n, window):
    x = np.random.default_rng(893).normal(size=n) + 0.75
    windows = {
        "boxcar": np.ones(n),
        "hann": np.hanning(n),
        "blackman": np.blackman(n),
        "blackmanharris": _bh4(n),
    }
    w = windows[window]
    for demean in (False, True):
        ps = power_spectral_density(x, 40e6, window=window, remove_mean=demean)
        f, oracle = periodogram(x, 40e6, window=w, detrend="constant" if demean else False)
        np.testing.assert_array_equal(ps["frequency_hz"], f)
        np.testing.assert_allclose(ps["density_v2_hz"], oracle, atol=1e-20, rtol=1e-10)
        data = x - x.mean() if demean else x
        assert ps["integrated_power_v2"] == pytest.approx(np.dot(data * w, data * w) / np.dot(w, w))
        assert ps["enbw_hz"] == pytest.approx(40e6 * np.dot(w, w) / w.sum() ** 2)


def test_dc_and_nyquist_are_not_doubled_and_noise_band_has_hz_units():
    n, fs = 4096, 4096.0
    x = 2 + 3 * (-1.0) ** np.arange(n)
    ps = power_spectral_density(x, fs, window="boxcar", remove_mean=False)
    assert ps["density_v2_hz"][0] == pytest.approx(4)
    assert ps["density_v2_hz"][-1] == pytest.approx(9)
    assert integrate_noise_band(ps, 0, fs / 2)["power_v2"] == pytest.approx(13)
    _, dbfs = spectrum_dbfs(x, fs, 3, window="boxcar")
    assert dbfs[-1] == pytest.approx(0)


def test_white_noise_density_is_independent_of_record_length():
    sigma, fs = 3e-5, 40e6
    for n in (16384, 65536):
        x = np.random.default_rng(17).normal(0, sigma, n)
        ps = power_spectral_density(x, fs)
        measured = integrate_noise_band(ps, 1e6, 10e6)["power_v2"]
        expected = 2 * sigma**2 / fs * 9e6
        assert measured == pytest.approx(expected, rel=0.06)


@pytest.mark.parametrize("divisor, rank", [(16, 16), (8, 8), (4, 4)])
def test_harmonic_aliases_are_identified_once_with_a_real_nyquist_column(divisor, rank):
    n, fs = 8192, 40e6
    t = np.arange(n) / fs
    x = np.sin(2 * np.pi * fs / divisor * t) + 0.03 * (-1.0) ** np.arange(n)
    x += np.random.default_rng(991).normal(0, 1e-4, n)
    metrics = sine_fit_metrics(x, fs, fs / divisor)
    assert metrics["harmonic_fit_rank"] == metrics["harmonic_fit_columns"] == rank
    assert metrics["noise_fit_reliable"]
    assert metrics["noise_rms"] == pytest.approx(1e-4, rel=0.03)
    assert metrics["harmonic_fit_dropped"]
    assert metrics["harmonic_fit_condition"] < 1.5
    assert metrics["THD_dB"] == pytest.approx(20 * np.log10(0.03 * np.sqrt(2)), abs=0.02)
    assert metrics["SFDR_dB"] == pytest.approx(-20 * np.log10(0.03 * np.sqrt(2)), abs=0.02)


def test_known_tone_harmonics_and_density_have_independent_answers():
    n, fs, k = 32768, 40e6, 173
    phase = 2 * np.pi * k * np.arange(n) / n
    x = 2 * np.sin(phase) + 2e-3 * np.cos(3 * phase)
    x += np.random.default_rng(1).normal(0, 2e-5, n)
    metrics = sine_fit_metrics(x, fs, k * fs / n)
    assert metrics["THD_dB"] == pytest.approx(-60, abs=0.05)
    assert metrics["SFDR_dB"] == pytest.approx(60, abs=0.05)
    assert metrics["SNDR_dB"] == pytest.approx(60, abs=0.05)
    assert metrics["NSD_V_rtHz"] == pytest.approx(2e-5 / np.sqrt(fs / 2), rel=0.03)


@pytest.mark.parametrize("x, fs", [([1, 2], 40e6), ([0, 1, np.nan, 0], 1), ([0, 1, 2, 3], 0)])
def test_invalid_records_fail_explicitly(x, fs):
    with pytest.raises(ValueError):
        power_spectral_density(x, fs)


def test_paper_and_slides_are_separate_rounded_measurement_anchors():
    from adi_model.benchmarks import PAPER_BENCHMARK, SLIDES_BENCHMARK

    for profile, dr, nsd in ((PAPER_BENCHMARK, 94.2, 9.3e-9), (SLIDES_BENCHMARK, 94.6, 8.8e-9)):
        row = profile.to_dict()
        assert row["dr_db"] == dr and row["nsd_v_rthz"] == nsd
        assert row["noise_rms_from_dr_v"] == pytest.approx(
            row["noise_rms_from_flat_nsd_v"], rel=0.01
        )
        cfg = profile.configuration()
        assert cfg.target_dr_db == dr and cfg.b1 == 9
    assert (
        PAPER_BENCHMARK.to_dict()["noise_rms_from_dr_v"]
        > SLIDES_BENCHMARK.to_dict()["noise_rms_from_dr_v"]
    )
