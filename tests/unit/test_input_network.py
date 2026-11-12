"""检查共享源的独立 KCL、连续跟踪及显式输入约束。"""

import json
from dataclasses import replace

import numpy as np
import pytest

from adi_model.simulation.config import Config
from adi_model.behavioral.input_network import InputNetworkParameters, PassiveTrackingNetwork, track_interval
from adi_model.behavioral.sampler import dc_input, sine_input


def kcl_reference(caps, ron, rs, cf, previous, fn, t0, t1, rho=0, scale=3):
    """直接从支路电流作 RK4 积分，独立于模态矩阵。

    额外积分状态累计源电流得到电荷，不从最终电容电压反算，因此电荷闭合检查
    与电压求解具有独立计算路径。
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
    net = PassiveTrackingNetwork(np.full(n, cap), np.full(n, ron), rs, 0.0)
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
            parameters=InputNetworkParameters(filter_cap_f=0.0, integration_steps=steps),
            ron_coefficient=0.8,
            voltage_scale=3,
        )
        errors.append(np.max(abs(actual - expected)))
    assert np.all(np.array(errors[:-1]) / errors[1:] > 3.7)
    assert errors[-1] < 2e-6


def test_high_frequency_tracking_retains_continuous_phase_lag():
    caps = np.r_[np.full(8, 2.5e-12), 1e-12]
    ron, source, frequency, amplitude = 20.0, 30.0, 19e6, 1.7
    omega = 2 * np.pi * frequency
    branch = 1 / (1 + 1j * omega * ron * caps)
    hbus = 1 / (1 + source * np.sum(1j * omega * caps * branch))
    t0, t1 = -25e-9, 0.0
    previous = np.real(-1j * amplitude * np.exp(1j * omega * t0) * hbus * branch)
    expected = np.real(-1j * amplitude * np.exp(1j * omega * t1) * hbus * branch)
    network = PassiveTrackingNetwork(caps, np.full(len(caps), ron), source, 0.0)
    actual = network.advance(previous, t0, t1, sine_input(amplitude, frequency, 0.0))
    np.testing.assert_allclose(actual, expected, rtol=0, atol=5e-13)
    assert abs(actual[0]) > 0.05  # 仅依据端点电压建立会错误地预测零响应。


def test_filter_capacitor_has_persistent_state_and_changes_acquisition():
    network = PassiveTrackingNetwork(np.array([2e-12, 3e-12]), np.array([20.0, 35.0]), 30.0, 35e-12)
    source = sine_input(1.0, 7e6, 0.2)
    first = network.advance(np.zeros(3), -25e-9, 0.0, source)
    # 在此显式 RC 网络消除初始状态影响前观察。
    continued = network.advance(first, 0.0, 0.5e-9, source)
    reset = network.advance(np.zeros(3), 0.0, 0.5e-9, source)
    assert np.max(abs(continued - reset)) > 1e-4
    assert first[-1] != 0
    assert np.isfinite(network.charge_change(np.zeros(3), first))


@pytest.mark.parametrize(
    "parameters",
    [
        InputNetworkParameters(filter_cap_f=-1e-12),
        InputNetworkParameters(filter_cap_f=np.nan),
        InputNetworkParameters(filter_cap_f=0.0, integration_steps=0),
        InputNetworkParameters(filter_cap_f=0.0, pretrack_mode="future-input-truth"),
    ],
)
def test_invalid_network_controls_fail_at_the_active_consumer(parameters):
    with pytest.raises(ValueError, match="input_network"):
        parameters.require_tracking()


def test_an_unspecified_bus_capacitance_is_not_an_ideal_zero_capacitance():
    parameters = InputNetworkParameters()
    with pytest.raises(ValueError, match="filter_cap_f"):
        track_interval(
            [2e-12], [20.0], 30.0, [0.0], 0.0, 25e-9, dc_input(0.1), parameters=parameters
        )
    assert parameters.filter_cap_f is None


def test_configuration_json_round_trip_is_reproducible(configuration_inputs):
    cfg = replace(
        configuration_inputs,
        input_network=InputNetworkParameters(filter_cap_f=35e-12, integration_steps=16),
    )
    restored = Config.from_dict(json.loads(json.dumps(cfg.to_dict())))
    assert restored == cfg
    with pytest.raises(TypeError):
        Config.from_dict({"input_network": {"filter_cap_ff": 35}})


def test_explicit_sadc_capacitance_changes_the_actual_shared_source_network():
    source = dc_input(1.0)
    nodes = []
    for sadc_cap in (1e-12, 4e-12):
        cfg = Config(c_sadc=sadc_cap)
        network = PassiveTrackingNetwork([2.5e-12, cfg.c_sadc], [20.0, 20.0], 30.0, 0.0)
        nodes.append(network.advance([0.0, 0.0], 0.0, 0.1e-9, source))
    assert np.max(abs(nodes[0] - nodes[1])) > 0.01
