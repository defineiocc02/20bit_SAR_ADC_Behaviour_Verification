"""检查 CAAZ 整数配对与显式 RA 输入记录，不预测器件噪声。"""

from dataclasses import replace

import numpy as np
import pytest

from adi_model.chopping_az import ChoppingAZParameters, pair_readout, run_caaz_pipeline
from adi_model.fixed_point import CodeStream
from adi_model.pipeline import run_pipeline
from adi_model.sampler import dc_input, sine_input
from adi_model.weight_calibration import DigitalObservation, fit_unit_weights


@pytest.fixture(scope="module")
def calibrated_source(explicit_pipeline_factory):
    # 一次受控静态回归识别 181 个系数；不测频谱或器件性能，
    # 物理电容真值不参与拟合数据。
    n = 512
    cfg, controls = explicit_pipeline_factory(n_samples=n)
    known = np.random.default_rng(917).uniform(-2.8, 2.8, n)

    def source(time):
        return known[np.rint(np.asarray(time) * cfg.fs).astype(int)]

    training = run_pipeline(cfg, source, n, **controls)
    assert not training.analog_overflow.any()
    data = DigitalObservation.from_result(training)
    model = fit_unit_weights(data, known)
    assert model.rank == 18 * 10 + 1
    return cfg, controls, model


def _controls(case, n):
    return {
        **case[1],
        "sample_ids": np.arange(n, dtype=np.int64),
        "dem_states": np.zeros(n, dtype=np.int64),
    }


def _run(case, fmt, *, n, az, samples=None, transfer=None, source=None, controls=None):
    return run_caaz_pipeline(
        case[0],
        dc_input(0.2) if source is None else source,
        n,
        format=fmt,
        calibration=case[2],
        az=az,
        az_input_samples_v=samples,
        az_transfer_to_input=transfer,
        **(_controls(case, n) if controls is None else controls),
    )


def test_off_preserves_raw_integer_stream_and_full_rate(calibrated_source, explicit_format):
    case = calibrated_source
    ordinary = run_pipeline(case[0], dc_input(0.2), 16, **_controls(case, 16))
    result = _run(case, explicit_format, n=16, az=ChoppingAZParameters(mode="off"))
    np.testing.assert_array_equal(
        result.output_code, ordinary.to_codes(format=explicit_format, calibration=case[2]).code
    )
    assert result.output_sample_rate_hz == case[0].fs
    assert result.az_input_evidence == "NOT_APPLIED"


def test_input_chop_pair_ownership_rate_and_ideal_held_error_cancellation(
    calibrated_source, explicit_format
):
    case = calibrated_source
    samples = np.linspace(-1e-3, 1e-3, 8)
    result = _run(
        case,
        explicit_format,
        n=16,
        az=ChoppingAZParameters(mode="chopping_assisted", odd_hold_retention=1.0),
        samples=samples,
        transfer=np.ones(16),
    )
    assert result.output_sample_rate_hz == case[0].fs / 2
    assert not result.invalid.any()
    np.testing.assert_array_equal(result.raw.sample.input_clean_v, 0.2 * result.chop_sign)
    np.testing.assert_allclose(result.output_v, 0.2, rtol=0, atol=2 * 6 / 2**20)
    np.testing.assert_array_equal(result.az_sample_input_v, np.repeat(samples, 2))
    np.testing.assert_array_equal(result.injected_ra_input_error_v, np.repeat(samples, 2))
    assert result.az_input_evidence == "EXPLICIT_INPUT_RECORD"


@pytest.mark.parametrize("retention,odd_transfer", [(0.9, 1.0), (1.0, 0.8)])
def test_hold_or_transfer_mismatch_leaves_the_predicted_pair_residual(
    calibrated_source, explicit_format, retention, odd_transfer
):
    case = calibrated_source
    transfer = np.ones(16)
    transfer[1::2] = odd_transfer
    result = _run(
        case,
        explicit_format,
        n=16,
        az=ChoppingAZParameters(mode="chopping_assisted", odd_hold_retention=retention),
        samples=np.full(8, 1e-3),
        transfer=transfer,
    )
    predicted = 0.2 + 0.5 * (1 - retention * odd_transfer) * 1e-3
    np.testing.assert_allclose(result.output_v, predicted, rtol=0, atol=2 * 6 / 2**20)
    assert abs(predicted - 0.2) > 4 * 6 / 2**20


def test_conventional_az_uses_independent_explicit_records_not_an_implicit_noise_rms(
    calibrated_source, explicit_format
):
    error = np.random.default_rng(8).normal(0, 1e-3, 16)
    result = _run(
        calibrated_source,
        explicit_format,
        n=16,
        az=ChoppingAZParameters(mode="conventional_az"),
        samples=error,
        transfer=np.ones(16),
    )
    expected = 0.2 + (error[::2] + error[1::2]) / 2
    np.testing.assert_allclose(result.output_v, expected, rtol=0, atol=2 * 6 / 2**20)
    assert result.az_refresh.all()


def test_az_input_is_applied_before_actual_ra_clip_and_backend_flags(
    calibrated_source, explicit_format
):
    result = _run(
        calibrated_source,
        explicit_format,
        n=16,
        az=ChoppingAZParameters(mode="chopping_assisted", odd_hold_retention=1.0),
        samples=np.full(8, 0.1),
        transfer=np.ones(16),
    )
    assert result.raw.ra_sat.any() and result.raw.adc2_over.any()
    assert result.invalid.any() and len(result.output_code) == 8
    assert np.max(abs(result.raw.vra)) <= calibrated_source[0].ra_v_clip


@pytest.mark.parametrize(
    "which",
    ["missing_samples", "missing_transfer", "scalar_transfer", "short_samples", "nonfinite"],
)
def test_active_az_inputs_are_not_broadcast_or_generated(calibrated_source, explicit_format, which):
    samples, transfer = np.zeros(8), np.ones(16)
    if which == "missing_samples":
        samples = None
    elif which == "missing_transfer":
        transfer = None
    elif which == "scalar_transfer":
        transfer = 1.0
    elif which == "short_samples":
        samples = np.zeros(7)
    else:
        samples[0] = np.nan
    with pytest.raises(ValueError):
        _run(
            calibrated_source,
            explicit_format,
            n=16,
            az=ChoppingAZParameters(mode="chopping_assisted", odd_hold_retention=1.0),
            samples=samples,
            transfer=transfer,
        )


def test_off_and_missing_hold_contract_cannot_ignore_inputs(calibrated_source, explicit_format):
    with pytest.raises(ValueError, match="ignore"):
        _run(
            calibrated_source,
            explicit_format,
            n=16,
            az=ChoppingAZParameters(mode="off"),
            samples=np.zeros(8),
        )
    with pytest.raises(ValueError, match="retention"):
        _run(
            calibrated_source,
            explicit_format,
            n=16,
            az=ChoppingAZParameters(mode="chopping_assisted"),
            samples=np.zeros(8),
            transfer=np.ones(16),
        )
    for old in ("az_noise_rms_v", "post_az_offset_v", "odd_transfer_ratio"):
        with pytest.raises(TypeError):
            ChoppingAZParameters(**{old: 0})


def test_pair_timestamp_and_sine_cosine_response_use_the_two_owned_apertures(
    calibrated_source, explicit_format
):
    cfg = calibrated_source[0]
    source = sine_input(0.6, cfg.fs / 16, 0.2)
    result = _run(
        calibrated_source,
        explicit_format,
        n=16,
        az=ChoppingAZParameters(mode="chopping_assisted", odd_hold_retention=1.0),
        samples=np.zeros(8),
        transfer=np.ones(16),
        source=source,
    )
    at = result.raw.sample.aperture_s
    midpoint = (at[::2] + at[1::2]) / 2
    expected = (source(at[::2]) + source(at[1::2])) / 2
    analytic = 0.6 * np.cos(np.pi / 16) * np.sin(2 * np.pi * (cfg.fs / 16) * midpoint + 0.2)
    np.testing.assert_allclose(result.output_time_s, midpoint, rtol=0, atol=1e-20)
    np.testing.assert_allclose(expected, analytic, rtol=0, atol=1e-14)
    np.testing.assert_allclose(result.output_v, expected, rtol=0, atol=2 * 6 / 2**20)
    assert result.az_input_evidence == "ZERO_INPUT_CONTROL"


def test_pair_ids_wrap_without_crossing_a_missing_record(calibrated_source, explicit_format):
    controls = _controls(calibrated_source, 4)
    ids = np.array([2**32 - 2, 2**32 - 1, 0, 1], np.uint32)
    controls["sample_ids"] = ids
    result = _run(
        calibrated_source,
        explicit_format,
        n=4,
        az=ChoppingAZParameters(mode="chopping_assisted", odd_hold_retention=1.0),
        samples=np.zeros(2),
        transfer=np.ones(4),
        controls=controls,
    )
    np.testing.assert_array_equal(result.source_sample_ids, ids.reshape(-1, 2))
    controls["sample_ids"] = np.array([0, 1, 3, 4])
    with pytest.raises(ValueError, match="continuous"):
        _run(
            calibrated_source,
            explicit_format,
            n=4,
            az=ChoppingAZParameters(mode="chopping_assisted", odd_hold_retention=1.0),
            samples=np.zeros(2),
            transfer=np.ones(4),
            controls=controls,
        )


def test_integer_pair_math_widens_unsigned_words_and_propagates_all_flags():
    words = np.array([0, 2**20 - 1, 2**20 - 1, 0], np.uint32)
    stream = CodeStream(
        words,
        np.array([False, False, True, False]),
        np.zeros(4, bool),
        np.array([False, True, False, False]),
        3.0,
        20,
        32,
    )
    code, voltage, exact, invalid = pair_readout(stream, difference=True)
    expected = (words[::2].astype(np.int64) - words[1::2].astype(np.int64)) * 3 / 2**20
    np.testing.assert_allclose(exact, expected, rtol=0, atol=0)
    np.testing.assert_array_equal(invalid, [True, True])
    assert code[0] < code[1] and np.isfinite(voltage).all()


@pytest.mark.parametrize("field", ["dyn_input_settling", "dyn_ref_settling", "dyn_crosstalk"])
def test_unbuilt_dynamic_chopping_combinations_are_rejected(
    calibrated_source, explicit_format, field
):
    case = (replace(calibrated_source[0], **{field: True}), *calibrated_source[1:])
    with pytest.raises(ValueError, match="does not implement or combine"):
        _run(
            case,
            explicit_format,
            n=16,
            az=ChoppingAZParameters(mode="chopping_assisted", odd_hold_retention=1.0),
            samples=np.zeros(8),
            transfer=np.ones(16),
        )


def test_active_chopping_does_not_fabricate_nominal_frozen_coefficients(
    calibrated_source, explicit_format
):
    with pytest.raises(ValueError, match="frozen effective"):
        run_caaz_pipeline(
            calibrated_source[0],
            dc_input(0.2),
            4,
            format=explicit_format,
            az=ChoppingAZParameters(mode="chopping_assisted", odd_hold_retention=1.0),
            az_input_samples_v=np.zeros(2),
            az_transfer_to_input=np.ones(4),
            **_controls(calibrated_source, 4),
        )


@pytest.mark.parametrize("bits", [1, 3, 20])
@pytest.mark.parametrize("difference", [True, False])
def test_generic_pair_bin_center_oracle_is_explicit_at_each_word_width(bits, difference):
    count = 2**bits
    values = np.array([0, count - 1, count - 1, 0], np.uint32)
    stream = CodeStream(
        values, np.zeros(4, bool), np.zeros(4, bool), np.zeros(4, bool), 3.0, bits, 32
    )
    code, _, exact, _ = pair_readout(stream, difference=difference)
    if difference:
        expected_words = np.array([0, count - 1])
        expected_voltage = np.array([-(count - 1) * 3 / count, (count - 1) * 3 / count])
    else:
        expected_words = np.array([count // 2, count // 2])
        expected_voltage = np.zeros(2)
    np.testing.assert_array_equal(code, expected_words)
    np.testing.assert_allclose(exact, expected_voltage, rtol=0, atol=1e-14)


@pytest.mark.parametrize(
    "values,bits",
    [([-1, 0], 20), ([2**20, 0], 20), ([0.5, 1.5], 20), ([0, 1], 0), ([0, 1], 31), ([0, 1], True)],
)
def test_pair_rejects_invalid_external_words_or_widths(values, bits):
    stream = CodeStream(
        np.asarray(values), np.zeros(2, bool), np.zeros(2, bool), np.zeros(2, bool), 3.0, bits, 32
    )
    with pytest.raises(ValueError):
        pair_readout(stream, difference=True)


def test_pair_does_not_hide_bad_flags_or_full_scale():
    stream = CodeStream(
        np.array([0, 1]), np.zeros(2, bool), np.zeros(2, bool), np.zeros(2, bool), 3.0, 20, 32
    )
    with pytest.raises(ValueError, match="Boolean"):
        pair_readout(replace(stream, analog_overflow=np.array([0, 1])), difference=True)
    with pytest.raises(ValueError, match="positive"):
        pair_readout(replace(stream, v_fs=0), difference=True)
