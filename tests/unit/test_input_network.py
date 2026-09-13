"""Independent KCL integration and analytic limits for continuous acquisition."""

import json
from dataclasses import replace

import numpy as np
import pytest

from adi_model import Config, dc_input, sine_input
from adi_model.input_network import (
    InputNetworkParameters,
    PassiveTrackingNetwork,
    track_interval,
)
from adi_model.pipeline import run_pipeline


def kcl_reference(caps, ron, rs, cf, previous, fn, t0, t1, rho=0, scale=3):
    """RK4 directly from branch currents, independently of modal matrices.

    The extra integral state is source current integrated to charge. It is not
    calculated from final capacitor voltages, so charge closure is independent.
    """
    n = len(caps)

    def derivative(t, state):
        vin = float(fn(t))
        r = ron * (1 + rho * (vin / scale) ** 2)
        bus = state[n] if cf else (vin / rs + np.sum(state[:n] / r)) / (1 / rs + sum(1 / r))
        currents = (bus - state[:n]) / r
        source = (vin - bus) / rs
        if cf:
            return np.r_[currents / caps, (source - sum(currents)) / cf, source]
        return np.r_[currents / caps, source]

    y = np.r_[previous, 0.0]
    steps = 4096
    h = (t1 - t0) / steps
    for j in range(steps):
        t = t0 + j * h
        k1 = derivative(t, y)
        k2 = derivative(t + h / 2, y + h * k1 / 2)
        k3 = derivative(t + h / 2, y + h * k2 / 2)
        k4 = derivative(t + h, y + h * k3)
        y += h / 6 * (k1 + 2 * k2 + 2 * k3 + k4)
    return y[:-1], y[-1]


def test_common_source_resistance_is_not_divided_by_slice_count():
    cap, ron, rs, n = 2.5e-12, 20, 30, 8
    dt = 0.6e-9
    net = PassiveTrackingNetwork(np.full(n, cap), np.full(n, ron), rs)
    actual = net.advance(np.zeros(n), 0, dt, dc_input(1))
    expected = 1 - np.exp(-dt / ((n * rs + ron) * cap))
    np.testing.assert_allclose(actual, expected, atol=2e-15)
    incorrect = 1 - np.exp(-dt / ((rs + ron) * cap))
    assert abs(actual[0] - incorrect) > 0.2


@pytest.mark.parametrize("cf", [0.0, 12e-12])
@pytest.mark.parametrize("kind", ["sine", "ramp"])
def test_modal_solution_and_charge_agree_with_independent_branch_currents(cf, kind):
    caps, ron, rs = np.array([2e-12, 3.7e-12]), np.array([37.0, 83.0]), 45.0
    previous = np.array([0.2, -0.3, 0.15]) if cf else np.array([0.2, -0.3])
    fn = sine_input(2.4, 19e6, 0.31) if kind == "sine" else lambda t: 0.3 + 1e8 * t
    net = PassiveTrackingNetwork(caps, ron, rs, cf)
    actual = net.advance(previous, -3.7e-9, 2.9e-9, fn)
    expected, q = kcl_reference(caps, ron, rs, cf, previous, fn, -3.7e-9, 2.9e-9)
    np.testing.assert_allclose(actual, expected, atol=2e-11, rtol=0)
    assert net.charge_change(previous, actual) == pytest.approx(q, abs=2e-23)


def test_nonlinear_ron_converges_to_continuous_ode():
    caps, ron, rs = np.array([2e-12, 3.7e-12]), np.array([37.0, 83.0]), 45.0
    fn = sine_input(2.4, 19e6, 0.31)
    previous = np.array([0.2, -0.3])
    expected, _ = kcl_reference(caps, ron, rs, 0, previous, fn, -3e-9, 3e-9, rho=0.8)
    errors = []
    for steps in (16, 32, 64, 128):
        actual, _ = track_interval(
            caps,
            ron,
            rs,
            previous,
            -3e-9,
            3e-9,
            fn,
            parameters=InputNetworkParameters(integration_steps=steps),
            ron_coefficient=0.8,
            voltage_scale=3,
        )
        errors.append(np.max(abs(actual - expected)))
    assert np.all(np.array(errors[:-1]) / errors[1:] > 3.7)
    assert errors[-1] < 2e-6


def test_high_frequency_tracking_retains_continuous_phase_lag():
    cfg = Config(
        dac_arch="split",
        dyn_input_settling=True,
        dyn_ron_code_coeff=0,
        mismatch_enable=False,
        sadc_rdac_gain_mismatch=0,
        ra_enable_noise=False,
        enable_sampling_noise=False,
    )
    fn = sine_input(1.7, 19e6, 0.2)
    r = run_pipeline(cfg, fn, 100)
    coeff = r.pool.split_coefficients(r.conv_slice_ids)
    caps = np.r_[coeff["load_slice"][0], cfg.c_sadc]
    omega = 2 * np.pi * 19e6
    # Independent AC solution: capacitor transfer from the bus, then bus
    # voltage from the shared source current. Long acquisition suppresses
    # the reset transient sufficiently for this steady-state comparison.
    branch = 1 / (1 + 1j * omega * cfg.dyn_r_on * caps)
    hbus = 1 / (1 + cfg.dyn_r_source * np.sum(1j * omega * caps * branch))
    expected = np.real(-1j * 1.7 * np.exp(1j * (omega * r.sample.t1 + 0.2)) * hbus * branch[0])
    np.testing.assert_allclose(r.acquisition_voltage[:, 0], expected, atol=5e-7, rtol=0)
    # An endpoint-only exponential wrongly predicts practically zero error.
    assert np.sqrt(np.mean(r.acquisition_error**2)) > 0.05
    assert not np.any(r.adc2_over)


def test_filter_capacitor_has_persistent_state_and_changes_acquisition():
    cfg = Config(
        dac_arch="split",
        dyn_input_settling=True,
        dyn_ron_code_coeff=0,
        mismatch_enable=False,
        sadc_rdac_gain_mismatch=0,
        ra_enable_noise=False,
        enable_sampling_noise=False,
    )
    fn = sine_input(1.0, 7e6)
    a = run_pipeline(cfg, fn, 64)
    b = run_pipeline(replace(cfg, input_network=InputNetworkParameters(100e-12)), fn, 64)
    assert np.max(abs(a.acquisition_voltage - b.acquisition_voltage)) > 0.1
    assert np.max(abs(b.input_bus_voltage - b.sample.x1)) > 0.05
    assert np.all(np.isfinite(b.input_source_charge_c))


@pytest.mark.parametrize(
    "parameters",
    [InputNetworkParameters(-1), InputNetworkParameters(np.nan), InputNetworkParameters(0, 0)],
)
def test_invalid_network_controls_fail_at_runner_entry(parameters):
    with pytest.raises(ValueError, match="input_network"):
        run_pipeline(replace(Config(), input_network=parameters), dc_input(0), 8)


def test_configuration_json_round_trip_is_reproducible():
    cfg = Config.paper_literal(input_network=InputNetworkParameters(35e-12, 16))
    recovered = Config.from_dict(json.loads(json.dumps(cfg.to_dict())))
    assert recovered == cfg
    with pytest.raises(TypeError):
        Config.from_dict({"input_network": {"filter_cap_ff": 35}})
