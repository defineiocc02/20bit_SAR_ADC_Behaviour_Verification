"""Integer bit-width, rounding, endpoints and independent raw-code oracles."""

import json
from dataclasses import replace

import numpy as np
import pytest

from adi_model import Config, FixedPointFormat, FixedPointReconstructor, sine_input
from adi_model.fixed_point import round_even_divide
from adi_model.pipeline import run_pipeline
from adi_model.weight_calibration import CalibrationSpec, DigitalObservation


@pytest.mark.parametrize(
    "n, expected", [(-7, -4), (-5, -2), (-3, -2), (-1, 0), (1, 0), (3, 2), (5, 2), (7, 4)]
)
def test_signed_ties_to_even(n, expected):
    assert round_even_divide(n, 2) == expected


def straight_backend(nbits=20):
    # All RDAC plates at -Vfs; sum(weights)=1. Thus x=fine-Vfs.
    # An independent backend transfer produces every final code exactly once.
    spec = CalibrationSpec(
        1, 2, 1, True, False, False, 1, 3.0, nbits, 0.0, 6.0, "off", "sub", 0, True
    )
    count = 2**nbits
    data = DigitalObservation(
        spec,
        np.zeros((count, 1), np.int64),
        np.zeros(count),
        np.zeros(count, np.int64),
        np.arange(count, dtype=np.int64),
        np.zeros(count),
        np.zeros(count),
        np.zeros(count, bool),
        True,
    )
    model = FixedPointReconstructor.from_weights(spec, [[0.5, 0.25, 0.25]])
    return data, model


def test_every_twenty_bit_word_and_code_width_without_a_huge_analog_record():
    data, model = straight_backend()
    result = model.reconstruct(data)
    np.testing.assert_array_equal(result.code, np.arange(2**20))
    assert np.all(np.diff(result.voltage) == 6 / 2**20)
    assert result.voltage[0] == -3 + 3 / 2**20
    assert result.voltage[-1] == 3 - 3 / 2**20
    assert not np.any(result.clipped_low | result.clipped_high | result.analog_overflow)
    assert result.peak_accumulator_bits <= 96


def test_output_saturation_is_separate_from_analog_saturation():
    data, model = straight_backend(4)
    # Known digital injection shifts corrected input beyond both endpoints.
    inj = np.zeros(16)
    inj[0], inj[-1] = 0.4, -0.4
    flags = np.zeros(16, bool)
    flags[8] = True
    result = model.reconstruct(replace(data, common_injection_v=inj, overflow=flags))
    assert np.flatnonzero(result.clipped_low).tolist() == [0]
    assert np.flatnonzero(result.clipped_high).tolist() == [15]
    assert np.flatnonzero(result.analog_overflow).tolist() == [8]
    assert result.code[0] == 0 and result.code[-1] == 2**20 - 1


def test_accumulator_overflow_is_an_error_instead_of_a_wrapped_word():
    data, model = straight_backend(4)
    with pytest.raises(OverflowError, match="accumulator"):
        replace(model, format=FixedPointFormat(accumulator_bits=64)).reconstruct(data)


def test_register_image_roundtrip_and_illegal_widths():
    data, model = straight_backend(4)
    restored = FixedPointReconstructor.from_dict(
        json.loads(json.dumps(model.to_dict(), allow_nan=False))
    )
    np.testing.assert_array_equal(restored.reconstruct(data).code, model.reconstruct(data).code)
    with pytest.raises(ValueError):
        restored.weights_q.setflags(write=True)
    with pytest.raises(ValueError):
        FixedPointFormat(weight_fraction_bits=48)
    with pytest.raises(ValueError, match="weights"):
        replace(model, weights_q=np.zeros((1, 3), dtype=np.int64))


@pytest.mark.parametrize("mode", ["off", "sampling", "quantizer"])
def test_production_raw_codes_agree_with_independent_float_reconstruction(mode):
    cfg = Config.paper_literal(
        dither_mode=mode,
        dither_discrete=True,
        mismatch_sigma0=0,
        dac_bridge_mismatch_sigma=0,
        dac_parasitic_spread=0,
        sadc_rdac_gain_mismatch=0,
    )
    r = run_pipeline(cfg, sine_input(2.4, cfg.fs * 73 / 2048), 2048)
    baseline = r.out.copy()
    # Prove that neither ideal input nor the precomputed floating reconstruction
    # is a dependency of the integer digital core.
    r.out[:] = np.nan
    r.x_ref[:] = np.nan
    stream = r.to_codes()
    assert np.max(np.abs(stream.voltage - baseline)) < 0.501 * cfg.lsb_target
    assert not np.any(stream.clipped_low | stream.clipped_high | stream.analog_overflow)


def test_observer_and_fractional_masks_require_an_explicit_digital_interface():
    cfg = Config.paper_literal(ktc_enable=True)
    r = run_pipeline(cfg, sine_input(1, 1e6), 16)
    with pytest.raises(ValueError, match="KTC observer"):
        r.to_codes()
    data, model = straight_backend(4)
    with pytest.raises(ValueError, match="discrete RDAC"):
        model.reconstruct(replace(data, rdac_code=np.full(16, 0.5)))


def test_all_coarse_carries_preserve_monotonicity_and_bounded_code_width():
    cfg = Config.paper_literal(
        dither_mode="off",
        dem_enable=False,
        mismatch_enable=False,
        enable_sampling_noise=False,
        ra_enable_noise=False,
        sadc_mismatch_enable=False,
        sadc_rdac_gain_mismatch=0,
    )
    lsb = cfg.lsb_target
    # Dense local ramps around every 9-bit boundary, 1/16 final LSB spacing.
    centers = -cfg.v_fs + np.arange(1, 512) * (2 * cfg.v_fs / 512)
    values = (centers[:, None] + np.arange(-64, 65)[None, :] * lsb / 16).ravel()

    def waveform(t):
        return np.interp(t * cfg.fs, np.arange(values.size), values)

    result = run_pipeline(cfg, waveform, len(values))
    words = result.to_codes().code.reshape(511, -1)
    increments = np.diff(words, axis=1)
    assert np.all((increments >= 0) & (increments <= 1))
    # This is a local backend/coarse-carry test, not a full-chip code-density INL.
    for row in words:
        _, widths = np.unique(row, return_counts=True)
        assert np.min(widths[1:-1]) >= 8
        assert np.max(widths[1:-1]) <= 24
