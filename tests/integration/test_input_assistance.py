"""Available-code causality and real auxiliary/precharge electrical effects."""

from dataclasses import replace

import numpy as np
import pytest

from adi_model import Config, dc_input, sine_input
from adi_model.conversion import ConversionParameters
from adi_model.input_network import InputNetworkParameters
from adi_model.pipeline import run_pipeline
from adi_model.pretracking import QuantizedPretracker


def ideal(**overrides):
    return replace(
        Config(
            dac_arch="split",
            mismatch_enable=False,
            enable_sampling_noise=False,
            ra_enable_noise=False,
            sadc_rdac_gain_mismatch=0,
            dyn_input_settling=True,
            dyn_ron_code_coeff=0,
        ),
        **overrides,
    )


def test_future_decision_does_not_enter_a_pretrack_command():
    tracker = QuantizedPretracker(18, 9, -3.0, 6 / 512)
    tracker.record(0, 7e-9, range(8), 320)
    tracker.record(1, 32e-9, range(8, 16), 480)
    prime = tracker.predict(range(8), 6e-9, "latest")
    assert np.all(prime.source_ids == -1)
    early = tracker.predict(range(8), 13e-9, "latest")
    np.testing.assert_array_equal(early.source_ids[:, 0], 0)
    np.testing.assert_allclose(early.voltage_v, -3 + 320.5 * 6 / 512)
    later = tracker.predict(range(8), 33e-9, "latest")
    np.testing.assert_array_equal(later.source_ids[:, 0], 1)
    assert np.all(later.voltage_v > early.voltage_v)


def test_own_and_weighted_histories_are_distinct_and_nominal():
    tracker = QuantizedPretracker(4, 3, -4, 1)
    tracker.record(0, 1, [0, 1], 1, known_injection_v=0.25)
    tracker.record(1, 2, [2, 3], 6)
    own = tracker.predict([0, 2], 3, "own")
    np.testing.assert_allclose(own.voltage_v, [-2.75, 2.5])
    np.testing.assert_array_equal(own.source_ids[:, 0], [0, 1])
    weighted = tracker.predict([0, 2], 3, "weighted")
    np.testing.assert_allclose(weighted.voltage_v, (0.6 * 2.5 + 0.3 * -2.75) / 0.9)
    np.testing.assert_array_equal(weighted.source_ids, [[1, 0, -1], [1, 0, -1]])


@pytest.mark.parametrize("mode", ["latest", "own", "weighted"])
def test_main_runner_only_uses_available_quantized_codes_and_changes_stored_state(mode):
    cfg = ideal(
        dyn_t_sample_frac=0.08,
        input_network=InputNetworkParameters(pretrack_mode=mode),
    )
    fn = sine_input(1.4, 70e3, 0.5)
    assisted = run_pipeline(cfg, fn, 256)
    baseline = run_pipeline(replace(cfg, input_network=InputNetworkParameters()), fn, 256)
    trace = assisted.input_assist_trace
    valid = trace.pretrack_source_ids >= 0
    deadlines = np.broadcast_to(trace.pretrack_start_s[:, None, None], valid.shape)
    assert np.all(trace.pretrack_available_s[valid] <= deadlines[valid])
    output_ids = np.broadcast_to(np.arange(256)[:, None, None], valid.shape)
    assert np.all(trace.pretrack_source_ids[valid] < output_ids[valid])
    assert np.all(trace.pretrack_source_ids[0] == -1)
    assert np.max(abs(assisted.acquisition_voltage - baseline.acquisition_voltage)) > 1e-3
    assert np.max(abs(trace.pretrack_source_charge_c)) > 1e-13
    # Low-frequency input is a useful pretrack case. Success is not asserted
    # for arbitrary high-frequency waveforms with delayed predictions.
    assert np.std(assisted.acquisition_error[16:]) < np.std(baseline.acquisition_error[16:])


@pytest.mark.parametrize("bypass", [False, True])
def test_auxiliary_load_and_reset_charges_are_accounted_at_their_actual_sources(bypass):
    params = InputNetworkParameters(
        filter_cap_f=40e-12, auxiliary_cap_f=5e-12, auxiliary_bypass=bypass
    )
    cfg = ideal(dyn_r_source=200, input_network=params)
    r = run_pipeline(cfg, dc_input(0.5), 8)
    tr = r.input_assist_trace
    coeff = r.pool.split_coefficients(r.conv_slice_ids)
    # The first aperture has zero initial charge on every branch and the bus.
    q = np.dot(coeff["load_slice"][0], r.acquisition_voltage[0])
    q += cfg.c_sadc * (0.5 + r.sadc_acquisition_error[0])
    q += params.filter_cap_f * r.input_bus_voltage[0]
    if not bypass:
        q += tr.auxiliary_branch_charge_c[0]
    assert r.input_source_charge_c[0] == pytest.approx(q, abs=1e-25)
    expected_aux = tr.auxiliary_branch_charge_c if bypass else np.zeros(8)
    np.testing.assert_allclose(tr.auxiliary_source_charge_c, expected_aux, atol=1e-25)
    np.testing.assert_array_equal(tr.auxiliary_reset_charge_c[:2], 0)
    np.testing.assert_allclose(
        tr.auxiliary_reset_charge_c[2:],
        -params.auxiliary_cap_f * tr.auxiliary_voltage_v[:-2],
        atol=1e-25,
    )


def test_auxiliary_bypass_improves_tracking_by_removing_real_bus_load():
    cfg = ideal(
        dyn_r_source=200,
        input_network=InputNetworkParameters(
            filter_cap_f=40e-12,
            auxiliary_cap_f=5e-12,
        ),
    )
    off = run_pipeline(cfg, dc_input(0.5), 32)
    on = run_pipeline(
        replace(cfg, input_network=replace(cfg.input_network, auxiliary_bypass=True)),
        dc_input(0.5),
        32,
    )
    assert np.mean(abs(on.acquisition_error[4:])) < np.mean(abs(off.acquisition_error[4:]))
    assert np.max(abs(on.input_bus_voltage - off.input_bus_voltage)) > 1e-3


def test_impossible_pretrack_phase_is_refused():
    cfg = ideal(input_network=InputNetworkParameters(pretrack_mode="latest", pretrack_time_s=20e-9))
    with pytest.raises(ValueError, match="pretracking and acquisition"):
        run_pipeline(cfg, dc_input(0), 8)


@pytest.mark.parametrize("dither_mode", ["sampling", "quantizer"])
def test_combined_nine_bit_signal_path_preserves_nominal_digital_provenance(dither_mode):
    cfg = Config.paper_literal(
        dither_mode=dither_mode,
        mismatch_enable=False,
        enable_sampling_noise=False,
        ra_enable_noise=False,
        sadc_rdac_gain_mismatch=0,
        dyn_input_settling=True,
        dyn_ron_code_coeff=0,
        dyn_ref_settling=True,
        rdac_bitwise_loading=True,
        conversion=ConversionParameters(ra_bandwidth_hz=500e6),
        input_network=InputNetworkParameters(
            filter_cap_f=5e-12, auxiliary_cap_f=1e-12, auxiliary_bypass=True, pretrack_mode="latest"
        ),
    )
    r = run_pipeline(cfg, sine_input(1.4, 50e3, 0.2), 128)
    trace = r.input_assist_trace
    sources = trace.pretrack_source_ids[:, 0, 0]
    valid = sources >= 0
    previous = sources[valid]
    nominal = -cfg.v_fs + (r.coarse[previous] + 0.5) * cfg.nominal_rdac_step
    if dither_mode == "quantizer":
        nominal -= r.sample.dither[previous]
    nominal /= cfg.dither_alpha
    np.testing.assert_allclose(trace.pretrack_target_v[valid, 0], nominal, atol=1e-14)
    assert np.all(np.isfinite(r.out))
    assert not np.any(r.adc2_over)
    assert not np.any(r.rdac_over)
