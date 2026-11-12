"""检查显式信号响应方程和因果电气状态。

这些测试不推断噪声折叠、芯片带宽或 ADC 性能。
"""

from dataclasses import replace

import numpy as np
import pytest
from scipy.integrate import solve_ivp

from adi_model.behavioral.conversion import (
    ConversionEngine,
    ConversionParameters,
    response_interval,
)
from adi_model.simulation.config import Config


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


def _parameters():
    # 这是完整声明的简化信号响应夹具，不是硅片时序表。
    return ConversionParameters(
        quantizer_time_s=3e-9,
        ra_duration_s=10e-9,
        ra_response_model="single_pole",
        ra_bandwidth_hz=120e6,
        ra_dc_gain="ideal",
        ra_slew_v_s="unlimited",
        adc2_response_model="single_pole",
        adc2_wide_bandwidth_hz=80e6,
    )


def test_conversion_consumer_does_not_infer_unknown_time_or_ideal_models():
    with pytest.raises(ValueError, match="conversion parameters"):
        ConversionEngine(Config(ra_v_clip=2.0), 2)
    with pytest.raises(ValueError, match="quantizer_time_s|ra_response_model"):
        ConversionEngine(Config(ra_v_clip=2.0, conversion=ConversionParameters()), 2)


@pytest.mark.parametrize("missing", ["initial_ra_output_v", "initial_adc2_input_v"])
def test_active_stateful_nodes_require_explicit_initial_conditions(missing):
    values = {"initial_ra_output_v": 0.0, "initial_adc2_input_v": 0.0}
    values[missing] = None
    with pytest.raises(ValueError, match=missing):
        ConversionEngine(Config(ra_v_clip=2.0, conversion=_parameters()), 2, **values)


def test_shared_ra_history_and_actual_aperture_times_are_retained():
    cfg = Config(ra_v_clip=2.0, conversion=_parameters())
    engine = ConversionEngine(cfg, 2, initial_ra_output_v=0.0, initial_adc2_input_v=0.0)
    samples = [1e-6, 1e-6 + 40e-9]
    for i, at_s in enumerate(samples):
        engine.step(
            i,
            program=None,
            sample_time_s=at_s,
            residue_v=0.01,
            gain=32.0,
            reference_to_residue_scale=None,
            feedback_noise_gain=None,
        )
    trace = engine.result
    np.testing.assert_allclose(trace.adc2_times_s, np.array(samples) + 13e-9, rtol=0, atol=1e-21)
    entry = trace.phase_names.index("ra_entry")
    assert trace.phase_ra_v[1, entry] == trace.ra_v[0] != 0.0
    assert trace.adc2_times_s[1] - trace.adc2_times_s[0] == pytest.approx(40e-9)


def test_adc2_narrow_switch_is_an_explicit_time_not_a_default_fraction():
    parameters = _parameters()
    cfg = Config(ra_v_clip=2.0, conversion=parameters)
    wide = ConversionEngine(cfg, 1, initial_ra_output_v=0.0, initial_adc2_input_v=0.0)
    narrow_parameters = replace(
        parameters,
        adc2_response_model="wide_narrow",
        adc2_narrow_bandwidth_hz=10e6,
        adc2_narrow_start_s=6e-9,
    )
    narrow = ConversionEngine(
        replace(cfg, conversion=narrow_parameters),
        1,
        initial_ra_output_v=0.0,
        initial_adc2_input_v=0.0,
    )
    for engine in (wide, narrow):
        engine.step(
            0,
            program=None,
            sample_time_s=0.0,
            residue_v=0.01,
            gain=32.0,
            reference_to_residue_scale=None,
            feedback_noise_gain=None,
        )
    np.testing.assert_array_equal(wide.result.ra_v, narrow.result.ra_v)
    assert abs(wide.result.adc2_v[0] - narrow.result.adc2_v[0]) > 1e-4
    mark = narrow.result.phase_names.index("adc2_narrow")
    assert narrow.result.phase_times_s[mark] == pytest.approx(9e-9)
    assert parameters.adc2_narrow_start_s is None


def test_reference_dynamics_requires_actual_initial_rails_events_and_switch_time():
    cfg = Config(
        ra_v_clip=2.0,
        dyn_ref_settling=True,
        dyn_c_decouple=100e-9,
        dyn_tau_ref=5e-9,
        conversion=_parameters(),
    )
    with pytest.raises(ValueError, match="rdac_event_times_s|reference_accurate_start_s"):
        ConversionEngine(cfg, 1, initial_ra_output_v=0.0, initial_adc2_input_v=0.0)
    p = replace(
        _parameters(),
        rdac_event_times_s=(0.0, 2e-9),
        reference_accurate_start_s=4e-9,
        fine_reference_tau_s=3e-9,
    )
    with pytest.raises(ValueError, match="initial_reference_error_v"):
        ConversionEngine(
            replace(cfg, conversion=p), 1, initial_ra_output_v=0.0, initial_adc2_input_v=0.0
        )


def test_overlapping_shared_intervals_and_out_of_order_calls_are_refused():
    engine = ConversionEngine(
        Config(ra_v_clip=2.0, conversion=_parameters()),
        2,
        initial_ra_output_v=0.0,
        initial_adc2_input_v=0.0,
    )
    kwargs = {
        "program": None,
        "residue_v": 0.01,
        "gain": 32.0,
        "reference_to_residue_scale": None,
        "feedback_noise_gain": None,
    }
    engine.step(0, sample_time_s=0.0, **kwargs)
    with pytest.raises(ValueError, match="consecutively"):
        engine.step(0, sample_time_s=25e-9, **kwargs)
    with pytest.raises(ValueError, match="overlapping"):
        engine.step(1, sample_time_s=10e-9, **kwargs)


def test_illegal_phase_budget_is_rejected_before_response_execution():
    cfg = Config(ra_v_clip=2.0, conversion=replace(_parameters(), quantizer_time_s=20e-9))
    with pytest.raises(ValueError, match="exceed one sample period"):
        ConversionEngine(cfg, 1, initial_ra_output_v=0.0, initial_adc2_input_v=0.0)


def test_finite_open_loop_error_uses_actual_feedback_noise_gain_not_signal_gain():
    # 显式比例夹具：Csignal/Cf=32，计入寄生后 Ceff/Cf=40。
    parameters = ConversionParameters(
        quantizer_time_s=3e-9,
        ra_duration_s=10e-9,
        ra_response_model="ideal",
        adc2_response_model="ideal",
        ra_dc_gain=100.0,
        ra_slew_v_s="unlimited",
    )
    cfg = Config(ra_v_clip=2.0, conversion=parameters)
    engine = ConversionEngine(cfg, 1)
    arguments = {
        "program": None,
        "sample_time_s": 0.0,
        "residue_v": 0.01,
        "gain": 32.0,
        "reference_to_residue_scale": None,
    }
    with pytest.raises(ValueError, match="feedback_noise_gain"):
        engine.step(0, feedback_noise_gain=None, **arguments)
    engine.step(0, feedback_noise_gain=41.0, **arguments)
    expected = 32 * 0.01 / (1 + 41 / 100)
    assert engine.result.ra_v[0] == pytest.approx(expected, rel=1e-14)
    wrong_signal_feedback = 32 * 0.01 / (1 + 33 / 100)
    assert abs(engine.result.ra_v[0] - wrong_signal_feedback) > 1e-3
