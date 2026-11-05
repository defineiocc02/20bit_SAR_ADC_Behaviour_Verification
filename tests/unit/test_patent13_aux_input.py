"""检查显式辅助源电路的边界，不将电荷比例直接等同带宽收益。"""

import numpy as np
import pytest

from adi_model.config import Config
from adi_model.input_network import InputNetworkParameters, PassiveTrackingNetwork
from adi_model.sampler import dc_input


def test_auxiliary_source_removes_only_its_real_load_from_the_main_source():
    # 显式无源小型拓扑不用于推断驱动功耗或带宽收益。
    main_cap, parasitic, filter_cap = 2.5e-12, 8e-12, 12e-12
    main_source, switch, auxiliary_source, auxiliary_switch = 100.0, 20.0, 30.0, 20.0
    duration = 5e-9
    source = dc_input(1.0)
    off = PassiveTrackingNetwork(
        [main_cap, parasitic], [switch, auxiliary_switch], main_source, filter_cap
    )
    on = PassiveTrackingNetwork([main_cap], [switch], main_source, filter_cap)
    auxiliary = PassiveTrackingNetwork([parasitic], [auxiliary_switch], auxiliary_source, 0.0)
    off_end = off.advance(np.zeros(3), 0, duration, source)
    on_end = on.advance(np.zeros(2), 0, duration, source)
    aux_end = auxiliary.advance(np.zeros(1), 0, duration, source)
    q_off = off.charge_change(np.zeros(3), off_end)
    q_main = on.charge_change(np.zeros(2), on_end)
    q_aux = auxiliary.charge_change(np.zeros(1), aux_end)
    assert q_main < q_off
    expected_aux = parasitic * (
        1 - np.exp(-duration / ((auxiliary_source + auxiliary_switch) * parasitic))
    )
    assert q_aux == pytest.approx(expected_aux, rel=1e-13)
    assert q_aux > 0 and q_main > 0
    assert not np.isclose(q_main, q_main + q_aux, rtol=0, atol=1e-25)


def test_independent_auxiliary_input_is_required_not_copied_from_signal():
    parameters = InputNetworkParameters(
        filter_cap_f=12e-12,
        auxiliary_cap_f=8e-12,
        auxiliary_bypass=True,
        auxiliary_source_ohm=30.0,
        auxiliary_resistance_ohm=20.0,
    )
    with pytest.raises(ValueError, match="auxiliary_input_fn"):
        parameters.require_auxiliary_source(None)
    parameters.require_auxiliary_source(dc_input(0.2))


@pytest.mark.parametrize(
    "field,value",
    [
        ("auxiliary_source_ohm", None),
        ("auxiliary_source_ohm", np.nan),
        ("auxiliary_source_ohm", -1.0),
        ("auxiliary_resistance_ohm", None),
        ("auxiliary_resistance_ohm", 0.0),
        ("auxiliary_cap_f", None),
    ],
)
def test_auxiliary_consumers_require_finite_declared_electrical_inputs(field, value):
    values = {
        "filter_cap_f": 0.0,
        "auxiliary_cap_f": 8e-12,
        "auxiliary_bypass": True,
        "auxiliary_source_ohm": 30.0,
        "auxiliary_resistance_ohm": 20.0,
    }
    values[field] = value
    with pytest.raises(ValueError):
        InputNetworkParameters(**values).require_auxiliary_source(dc_input(0.1))


def test_dc_rc_residual_follows_its_actual_time_constant():
    cap, ron, source = 2e-12, 20.0, 30.0
    tau = (ron + source) * cap
    network = PassiveTrackingNetwork([cap], [ron], source, 0.0)
    early = network.advance([0.0], 0, tau, dc_input(1.0))[0]
    late = network.advance([0.0], 0, 3 * tau, dc_input(1.0))[0]
    assert 1 - early == pytest.approx(np.exp(-1), rel=1e-13)
    assert 1 - late == pytest.approx(np.exp(-3), rel=1e-13)
    assert abs(1 - late) < abs(1 - early)


def test_source_voltage_does_not_invent_a_bootstrap_or_ron_improvement():
    for legacy in ("boost_voltage", "r_aux"):
        with pytest.raises(TypeError):
            Config.from_dict({legacy: 3.3})
    network = PassiveTrackingNetwork([2e-12], [20.0], 30.0, 0.0)
    # 非线性开关定律须单独显式输入，不能只用模式标签代替。
    assert np.isfinite(network.advance([0.0], 0, 1e-9, dc_input(0.1))).all()
