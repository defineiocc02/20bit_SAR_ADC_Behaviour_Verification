"""Convolution, independent ODE and causal pipeline tests for the joint chain."""

from dataclasses import replace

import numpy as np
import pytest
from scipy.integrate import solve_ivp

from adi_model import Config, dc_input, sine_input
from adi_model.conversion import ConversionParameters, response_interval
from adi_model.pipeline import run_pipeline


@pytest.mark.parametrize("bandwidth", [50e6, 100e6, 200e6])
def test_reference_history_survives_its_endpoint_recovery(bandwidth):
    dt = 15e-9
    tau = 0.65 * dt / np.log(32)
    a, b = 1 / tau, 2 * np.pi * bandwidth
    initial = 6 / 2**15
    y, z, saturated = response_interval(
        0, 0, 0, initial, tau, dt, ra_bw_hz=bandwidth, adc_bw_hz=None, clip_v=2
    )
    expected = initial * b / (b - a) * (np.exp(-a * dt) - np.exp(-b * dt))
    assert y == pytest.approx(expected, rel=2e-13)
    assert z == y and not saturated
    assert y > initial * np.exp(-dt / tau)


def test_repeated_poles_do_not_suffer_division_by_zero():
    tau, dt, initial = 3e-9, 15e-9, 0.1
    y, _, _ = response_interval(
        0,
        0,
        0,
        initial,
        tau,
        dt,
        ra_bw_hz=1 / (2 * np.pi * tau),
        adc_bw_hz=None,
        clip_v=2,
    )
    assert y == pytest.approx(initial * dt / tau * np.exp(-dt / tau), rel=1e-13)


def test_joint_linear_cascade_matches_independent_time_domain_integration():
    dt, tau = 12e-9, 2e-9
    b, c = 2 * np.pi * 120e6, 2 * np.pi * 35e6
    y, z, _ = response_interval(
        -0.1,
        0.12,
        0.8,
        -0.035,
        tau,
        dt,
        ra_bw_hz=b / (2 * np.pi),
        adc_bw_hz=c / (2 * np.pi),
        clip_v=2,
    )

    def ode(s, state):
        return (
            np.array(
                [
                    b * (0.8 - 0.035 * np.exp(-dt * s / tau) - state[0]),
                    c * (state[0] - state[1]),
                ]
            )
            * dt
        )

    oracle = solve_ivp(ode, (0, 1), [-0.1, 0.12], method="DOP853", rtol=1e-12, atol=1e-14)
    np.testing.assert_allclose([y, z], oracle.y[:, -1], atol=1e-12, rtol=0)


def test_slew_and_swing_limits_act_before_adc2():
    y, z, clipped = response_interval(
        0,
        0,
        1.0,
        0,
        3e-9,
        15e-9,
        ra_bw_hz=1e9,
        adc_bw_hz=None,
        clip_v=2,
        slew_v_s=10e6,
    )
    assert y == pytest.approx(0.15, abs=1e-12)
    assert z == y and not clipped
    y, z, clipped = response_interval(
        0,
        0,
        2.0,
        0,
        3e-9,
        50e-9,
        ra_bw_hz=100e6,
        adc_bw_hz=20e6,
        clip_v=1,
    )
    assert y == 1.0 and clipped
    assert 0.9 < z <= 1.0


def ideal(**overrides):
    return replace(
        Config(
            dac_arch="split",
            mismatch_enable=False,
            enable_sampling_noise=False,
            ra_enable_noise=False,
            sadc_rdac_gain_mismatch=0,
        ),
        **overrides,
    )


def test_ideal_joint_limit_and_real_adc2_aperture_are_distinct():
    cfg = ideal()
    fn = sine_input(1.8, cfg.fs * 37 / 256)
    baseline = run_pipeline(cfg, fn, 256)
    fast_cfg = replace(cfg, conversion=ConversionParameters(ra_bandwidth_hz=2e9))
    fast = run_pipeline(fast_cfg, fn, 256)
    np.testing.assert_allclose(fast.out, baseline.out, atol=cfg.delta2 / cfg.g0 + 1e-14)
    narrow = run_pipeline(
        replace(
            fast_cfg,
            conversion=ConversionParameters(
                ra_bandwidth_hz=2e9,
                adc2_wide_bandwidth_hz=200e6,
                adc2_narrow_bandwidth_hz=10e6,
            ),
        ),
        fn,
        256,
    )
    np.testing.assert_allclose(narrow.vra, fast.vra, atol=1e-14)
    assert np.max(abs(narrow.adc2_input_voltage - narrow.vra)) > 1e-4
    assert np.max(abs(narrow.out - fast.out)) > cfg.lsb_target
    np.testing.assert_allclose(
        fast.conversion_trace.adc2_times_s - fast.sample.t1,
        fast_cfg.conversion.quantizer_time_s + cfg.dyn_t_conv_frac / cfg.fs,
        atol=1e-21,
    )


def test_reference_state_and_loading_are_in_the_production_signal_path():
    cfg = ideal(
        dyn_ref_settling=True,
        dyn_c_decouple=100e-9,
        rdac_bitwise_loading=True,
        conversion=ConversionParameters(fine_reference_tau_s=3e-9, ra_bandwidth_hz=150e6),
    )
    r = run_pipeline(cfg, sine_input(1.3, cfg.fs * 17 / 128), 128)
    trace = r.conversion_trace
    assert trace.reference_charge_c.shape == (128, 2 * cfg.b1 + 1, 2)
    assert np.any(trace.reference_charge_c > 0) and np.any(trace.reference_charge_c < 0)
    assert np.max(trace.reference_peak_v) > 1e-5
    assert np.max(trace.reference_peak_v) / cfg.v_fs < 0.01
    assert np.max(abs(r.vra - cfg.g0 * r.residue)) > 1e-5
    same = run_pipeline(cfg, sine_input(1.3, cfg.fs * 17 / 128), 128)
    np.testing.assert_array_equal(r.out, same.out)
    assert r.effective_config["inactive_overrides"]["dyn_ref_dynamic_ratio"]


def test_reference_error_updates_slice_state_before_its_next_acquisition():
    cfg = ideal(
        dyn_input_settling=True,
        dyn_ron_code_coeff=0,
        dyn_t_sample_frac=0.04,
        dyn_ref_settling=True,
        dyn_c_decouple=100e-9,
    )
    a = run_pipeline(cfg, dc_input(0.5), 8)
    b = run_pipeline(replace(cfg, dyn_c_decouple=200e-9), dc_input(0.5), 8)
    # First use of A and B cannot depend on either group's future conversion.
    np.testing.assert_array_equal(a.acquisition_voltage[:2], b.acquisition_voltage[:2])
    assert np.max(abs(a.acquisition_voltage[2:] - b.acquisition_voltage[2:])) > 1e-7


def test_illegal_phase_budget_is_rejected_before_simulation():
    with pytest.raises(ValueError, match="fit within one sample period"):
        run_pipeline(
            ideal(conversion=ConversionParameters(quantizer_time_s=20e-9, ra_bandwidth_hz=100e6)),
            dc_input(0),
            8,
        )
