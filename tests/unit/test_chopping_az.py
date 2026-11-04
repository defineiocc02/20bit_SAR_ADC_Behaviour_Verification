"""Isolated sample-domain CAAZ contract tests, not device-performance claims."""

from dataclasses import replace

import numpy as np
import pytest

from adi_model import Config, dc_input, sine_input
from adi_model.chopping_az import ChoppingAZParameters, pair_readout, run_caaz_pipeline
from adi_model.fixed_point import CodeStream
from adi_model.pipeline import run_pipeline


def ideal(**kw):
    return Config.paper_literal(
        mismatch_enable=False,
        enable_sampling_noise=False,
        ra_enable_noise=False,
        sadc_rdac_gain_mismatch=0,
        **kw,
    )


def test_default_off_preserves_ordinary_az_raw_numbers_exactly():
    cfg = Config.paper_literal(ra_autozero=True, seed=31)
    fn = sine_input(0.8, cfg.fs * 31 / 1024)
    before = run_pipeline(cfg, fn, 1024)
    after = run_caaz_pipeline(cfg, fn, 1024)
    np.testing.assert_array_equal(before.adc2_code, after.raw.adc2_code)
    np.testing.assert_array_equal(before.out, after.raw.out)
    np.testing.assert_array_equal(before.to_codes().code, after.output_code)
    assert after.output_sample_rate_hz == cfg.fs == 40e6


def test_full_adc_input_is_chopped_and_adc2_window_is_not_changed():
    cfg = ideal()
    fn = dc_input(0.7)
    result = run_caaz_pipeline(
        cfg, fn, 128, az=ChoppingAZParameters(mode="chopping_assisted", az_noise_rms_v=0.002)
    )
    np.testing.assert_array_equal(result.raw.sample.x1_clean, 0.7 * result.chop_sign)
    np.testing.assert_array_equal(result.raw.sample.x2_clean, 0.7 * result.chop_sign)
    assert result.output_sample_rate_hz == 20e6
    assert np.std(result.output_v - 0.7) < 2 * cfg.lsb_target
    assert not result.invalid.any()
    assert result.raw.cfg.adc2_v_min == cfg.adc2_v_min
    assert result.raw.cfg.adc2_v_max == cfg.adc2_v_max
    assert np.all(result.az_refresh[::2]) and not np.any(result.az_refresh[1::2])
    np.testing.assert_array_equal(result.az_sample_error_v[::2], result.az_sample_error_v[1::2])


def test_injection_is_before_real_ra_clip_and_adc2_quantization():
    cfg = ideal()
    result = run_caaz_pipeline(
        cfg,
        dc_input(0.2),
        64,
        az=ChoppingAZParameters(mode="chopping_assisted", post_az_offset_v=0.7),
    )
    assert result.raw.ra_sat.any()
    assert result.raw.adc2_over.any()
    assert result.invalid.any()
    assert len(result.output_code) == 32
    assert np.max(result.raw.vra) <= cfg.ra_v_clip


def test_exact_bin_center_integer_pair_math_and_invalid_propagation():
    code = np.array([0, 1, 2**20 - 1, 0, 300, 300], np.int64)
    flags = np.array([False, True, False, False, False, False])
    words = CodeStream(code, np.zeros(6, bool), np.zeros(6, bool), flags, 3.0, 20, 80)
    for difference in [False, True]:
        out, voltage, exact, invalid = pair_readout(words, difference=difference)
        expected = (
            (words.voltage[::2] - words.voltage[1::2]) / 2
            if difference
            else (words.voltage[::2] + words.voltage[1::2]) / 2
        )
        np.testing.assert_array_equal(exact, expected)
        expected_code = np.floor((expected + 3) / (6 / 2**20)).astype(np.int64)
        np.testing.assert_array_equal(out, expected_code)
        np.testing.assert_array_equal(invalid, [True, False, False])


def test_same_independent_white_noise_and_held_noise_cancellation():
    cfg = Config.paper_literal(
        mismatch_enable=False,
        enable_sampling_noise=False,
        ra_enable_noise=True,
        ra_out_noise_rms=0.0032,
        sadc_rdac_gain_mismatch=0,
        seed=77,
    )
    n = 8192
    fn = dc_input(0.3)
    results = {}
    for mode in ["baseline", "conventional_az", "chopping_assisted"]:
        results[mode] = run_caaz_pipeline(
            cfg,
            fn,
            n,
            az=ChoppingAZParameters(mode=mode, az_noise_rms_v=0 if mode == "baseline" else 0.0032),
        )
    base, conv, caaz = (results[k] for k in ["baseline", "conventional_az", "chopping_assisted"])
    for r in results.values():
        assert not r.invalid.any()
        # Signal and explicit error removed only for diagnostics: the remaining
        # white source must be the same physical realization in all three runs.
        other = r.raw.vra - r.raw.g_vec * r.raw.residue - r.injected_ra_error_v
        first = base.raw.vra - base.raw.g_vec * base.raw.residue
        np.testing.assert_allclose(other, first, atol=2e-16, rtol=0)
    assert np.std(caaz.output_v - 0.3) < 0.8 * np.std(conv.output_v - 0.3)
    assert np.std(caaz.output_v - 0.3) == pytest.approx(np.std(base.output_v - 0.3), rel=0.03)
    assert abs(np.corrcoef(conv.az_sample_error_v[::2], conv.az_sample_error_v[1::2])[0, 1]) < 0.05


def test_hold_transfer_mismatch_leaves_predicted_pair_residual():
    cfg = ideal(seed=9)
    n = 4096
    az = ChoppingAZParameters(
        mode="chopping_assisted",
        az_noise_rms_v=0.004,
        odd_hold_retention=0.9,
        odd_transfer_ratio=1.02,
    )
    result = run_caaz_pipeline(cfg, dc_input(0.3), n, az=az)
    predicted = (result.injected_ra_error_v[::2] - result.injected_ra_error_v[1::2]) / (2 * cfg.g0)
    assert not result.invalid.any()
    assert np.std((result.prequantized_output_v - 0.3) - predicted) < cfg.lsb_target
    assert np.std(result.output_v - 0.3) > 4e-6


def test_paired_difference_suppresses_defined_slow_post_az_error():
    cfg = ideal()
    n = 1024
    error = np.linspace(-0.002, 0.002, n)
    result = run_caaz_pipeline(
        cfg,
        dc_input(0.3),
        n,
        az=ChoppingAZParameters(mode="chopping_assisted"),
        post_az_error_v=error,
    )
    expected = (error[::2] - error[1::2]) / (2 * cfg.g0)
    assert np.max(abs(expected)) < 1e-7
    assert np.max(abs(result.prequantized_output_v - 0.3 - expected)) < cfg.lsb_target


@pytest.mark.parametrize(
    "kw",
    [
        {"ra_autozero": True},
        {"dyn_input_settling": True},
        {"flicker_corner_hz": 40},
        {"sampling_tau_mismatch": 1e-12},
        {"driver_noise_rms": 1e-6},
    ],
)
def test_unbuilt_combinations_are_rejected(kw):
    with pytest.raises(ValueError, match="does not implement or combine"):
        run_caaz_pipeline(
            ideal(**kw), dc_input(0.1), 32, az=ChoppingAZParameters(mode="chopping_assisted")
        )


@pytest.mark.parametrize("mode", ["off", "baseline", "conventional_az"])
def test_inactive_odd_transfer_is_rejected(mode):
    with pytest.raises(ValueError, match="odd transfer ratio"):
        ChoppingAZParameters(mode=mode, odd_transfer_ratio=0.9).validate()


def test_unsigned_pair_words_widen_before_difference():
    code = np.array([0, 2**20 - 1, 2**20 - 1, 0], dtype=np.uint32)
    flags = np.zeros(4, bool)
    words = CodeStream(code, flags, flags, flags, 3.0, 20, 80)
    output, _, _, _ = pair_readout(words, difference=True)
    np.testing.assert_array_equal(output, [0, 2**20 - 1])


@pytest.mark.parametrize(
    "code,bits", [([0.0, 1.0], 20), ([-1, 0], 20), ([0, 2**20], 20), ([0, 1], 0), ([0, 1], 31)]
)
def test_pair_rejects_invalid_external_words_and_widths(code, bits):
    flags = np.zeros(2, bool)
    with pytest.raises(ValueError, match="(offset-binary integers|output_bits)"):
        pair_readout(
            CodeStream(np.asarray(code), flags, flags, flags, 3.0, bits, 80), difference=True
        )


@pytest.mark.parametrize("ids", [[1, 2, 3, 4], [0, 1, 3, 4], [0, 0, 1, 2]])
def test_fresh_record_rejects_wrong_ids(monkeypatch, ids):
    import adi_model.chopping_az as module

    cfg = ideal()
    raw = run_pipeline(cfg, dc_input(0.1), 4)
    raw = replace(raw, sample_id=np.array(ids))
    monkeypatch.setattr(module, "run_pipeline", lambda *a, **kw: raw)
    with pytest.raises(ValueError, match="continuous IDs"):
        run_caaz_pipeline(cfg, dc_input(0.1), 4, az=ChoppingAZParameters(mode="chopping_assisted"))


def test_fresh_record_supports_continuous_uint32_wrap(monkeypatch):
    import adi_model.chopping_az as module

    cfg = ideal()
    raw = replace(
        run_pipeline(cfg, dc_input(0.1), 4),
        sample_id=np.array([2**32 - 2, 2**32 - 1, 0, 1], dtype=np.uint32),
    )
    monkeypatch.setattr(module, "run_pipeline", lambda *a, **kw: raw)
    result = run_caaz_pipeline(
        cfg, dc_input(0.1), 4, az=ChoppingAZParameters(mode="chopping_assisted")
    )
    np.testing.assert_array_equal(result.source_sample_ids, [[2**32 - 2, 2**32 - 1], [0, 1]])


def test_off_and_baseline_do_not_consume_inactive_az_rng():
    for mode in ["off", "baseline"]:
        generator = np.random.default_rng(82)
        before = generator.bit_generator.state
        run_caaz_pipeline(
            ideal(), dc_input(0.1), 4, az=ChoppingAZParameters(mode=mode), az_rng=generator
        )
        assert generator.bit_generator.state == before


def test_sine_pair_reference_cosine_response_and_midpoint_timestamp():
    """CAAZ-16: independent sine identity and output-rate time reference."""
    cfg = ideal()
    amplitude, phase, count = 0.7, 0.31, 2048
    frequency = cfg.fs * 211 / count
    fn = sine_input(amplitude, frequency, phase)
    result = run_caaz_pipeline(cfg, fn, count, az=ChoppingAZParameters(mode="chopping_assisted"))
    assert not result.invalid.any()
    # Each raw tick is one Ts apart. A pair belongs half a raw period after
    # the even aperture, independent of the implementation's timestamp formula.
    midpoint = result.raw.sample.t1[::2] + 0.5 / cfg.fs
    np.testing.assert_allclose(result.output_time_s, midpoint, atol=1e-20, rtol=0)
    assert result.output_sample_rate_hz == cfg.fs / 2
    np.testing.assert_allclose(np.diff(midpoint), 2 / cfg.fs, atol=1e-20, rtol=0)
    raw_reference = fn(result.raw.sample.t1)
    pair_reference = (raw_reference[::2] + raw_reference[1::2]) / 2
    expected_amplitude = amplitude * np.cos(np.pi * frequency / cfg.fs)
    analytic_reference = expected_amplitude * np.sin(2 * np.pi * frequency * midpoint + phase)
    np.testing.assert_allclose(pair_reference, analytic_reference, atol=2e-13, rtol=0)
    # RA/ADC2 quantization and the two 20-bit bin-center stages define this
    # bound; no fitted sine or endpoint truth is substituted for pair truth.
    adc2_step = (cfg.adc2_v_max - cfg.adc2_v_min) / 2**cfg.adc2_n_bits
    bound_v = adc2_step / (2 * cfg.g0) + cfg.lsb_target
    assert np.max(np.abs(result.output_v - pair_reference)) < bound_v
    basis = np.column_stack(
        (
            np.sin(2 * np.pi * frequency * midpoint),
            np.cos(2 * np.pi * frequency * midpoint),
            np.ones(len(midpoint)),
        )
    )
    fit = np.linalg.lstsq(basis, result.output_v, rcond=None)[0]
    assert np.hypot(fit[0], fit[1]) == pytest.approx(expected_amplitude, abs=bound_v)


def test_full_rate_sliding_demodulation_retains_cross_generation_az_noise():
    """CAAZ-10: only aligned odd windows cancel the same held AZ sample."""
    cfg = ideal(seed=1801)
    level = 0.3
    result = run_caaz_pipeline(
        cfg,
        dc_input(level),
        2048,
        az=ChoppingAZParameters(mode="chopping_assisted", az_noise_rms_v=0.004),
    )
    assert not result.invalid.any()
    demodulated = result.chop_sign * result.raw_words.voltage
    sliding = (demodulated[1:] + demodulated[:-1]) / 2
    # Sliding outputs have endpoint indices 1,2,... . Odd endpoints are the
    # true pairs; even endpoints join two separately refreshed AZ generations.
    aligned, across_generation = sliding[::2] - level, sliding[1::2] - level
    expected_across = (result.injected_ra_error_v[2::2] - result.injected_ra_error_v[1:-1:2]) / (
        2 * cfg.g0
    )
    adc2_step = (cfg.adc2_v_max - cfg.adc2_v_min) / 2**cfg.adc2_n_bits
    raw_pair_bound_v = adc2_step / (2 * cfg.g0) + cfg.lsb_target / 2
    assert np.max(np.abs(aligned)) < raw_pair_bound_v
    assert np.max(np.abs(across_generation - expected_across)) < raw_pair_bound_v
    assert np.std(expected_across) > 10 * raw_pair_bound_v
    assert np.std(across_generation) > 10 * np.std(aligned)


def test_real_slice_gain_changes_leak_common_ra_output_error_after_frozen_calibration():
    """CAAZ-11: physical selected-slice gain matters without a fake odd ratio."""
    from adi_model.reconstruction import initialize_state
    from adi_model.slice_pool import PhysicalSlicePool
    from adi_model.weight_calibration import DigitalObservation, fit_unit_weights

    # A small legal geometry keeps the test bounded while retaining actual
    # fabrication, causal slice selection, ADC2 codes and frozen integer weights.
    cfg = Config.paper_literal(
        n_slices=4,
        n_active=1,
        mismatch_enable=True,
        mismatch_sigma0=0,
        enable_sampling_noise=False,
        ra_enable_noise=False,
        dem_enable=True,
        dem_mode="permute",
        dither_mode="off",
        sadc_rdac_gain_mismatch=0,
        dac_bridge_mismatch_sigma=0,
        dac_parasitic_spread=0,
        seed=2501,
    )
    pool = PhysicalSlicePool(cfg, np.random.default_rng(889), slice_sigma=0.005, unit_sigma=0)
    training_cfg = replace(
        cfg, dither_mode="sampling", dither_split_bank="sub", dither_units_range=2
    )
    known_input = sine_input(2.0, cfg.fs * 103 / 4096)
    training = run_pipeline(training_cfg, known_input, 4096, pool=pool)
    assert not (training.adc2_over | training.ra_sat | training.rdac_over).any()
    # Only whitelisted digital observations and a separately supplied reference
    # reach this estimator. Physical C/G below are used solely by the oracle.
    fit = fit_unit_weights(
        DigitalObservation.from_result(training), known_input(training.sample.t1)
    )
    assert fit.rank == cfg.n_slices * cfg.dac_n_units + 1
    state = initialize_state(cfg)
    state.weight_calibration = fit
    level, count = 0.3, 1024
    # An explicitly defined independent bounded noise source: the same RA-output
    # voltage is held within each pair. Bounds preserve the original headroom.
    error_v = np.repeat(np.random.default_rng(6421).uniform(-0.12, 0.12, count // 2), 2)
    no_error = run_caaz_pipeline(
        cfg,
        dc_input(level),
        count,
        pool=pool,
        state=state,
        az=ChoppingAZParameters(mode="chopping_assisted"),
    )
    with_error = run_caaz_pipeline(
        cfg,
        dc_input(level),
        count,
        pool=pool,
        state=state,
        az=ChoppingAZParameters(mode="chopping_assisted"),
        post_az_error_v=error_v,
    )
    assert with_error.raw.pool is no_error.raw.pool is training.pool
    assert not with_error.invalid.any() and not no_error.invalid.any()
    np.testing.assert_array_equal(no_error.raw.conv_slice_ids, with_error.raw.conv_slice_ids)
    np.testing.assert_array_equal(no_error.raw.k, with_error.raw.k)
    assert with_error.raw.calibration_applied == ("unit_weights",)
    gain = with_error.raw.g_vec
    assert np.ptp(gain) > 0.3
    np.testing.assert_array_equal(with_error.az_sample_error_v, 0)
    np.testing.assert_array_equal(with_error.injected_ra_error_v, error_v)
    # Physical G changes because selected fabricated slices differ. The paired
    # difference of two otherwise identical captures removes static DAC errors;
    # a shared RA-output error then sees two different input-referred scales.
    expected = error_v[::2] * (1 / gain[::2] - 1 / gain[1::2]) / 2
    observed = with_error.prequantized_output_v - no_error.prequantized_output_v
    final_code_observed = with_error.output_v - no_error.output_v
    adc2_step = (cfg.adc2_v_max - cfg.adc2_v_min) / 2**cfg.adc2_n_bits
    quantization_bound_v = cfg.lsb_target + adc2_step / np.min(gain)
    assert np.sqrt(np.mean(expected**2)) > cfg.lsb_target
    assert np.sqrt(np.mean(observed**2)) > cfg.lsb_target
    assert np.max(np.abs(observed - expected)) < quantization_bound_v
    # The final pair's floor/bin-center adds 0 or +0.5 LSB per output;
    # subtracting the two captures can therefore add at most another half LSB.
    final_quantization_bound_v = quantization_bound_v + cfg.lsb_target / 2
    assert np.max(np.abs(final_code_observed - expected)) < final_quantization_bound_v
