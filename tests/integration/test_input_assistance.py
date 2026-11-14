"""组合可用数字判决与显式预充／AUX 网络，保留因果和分源电荷边界。

本文件验证独立组件的连接数学，不将尚未绑定到完整流水线的动态输入相位称为
已实现。预跟踪改善仅针对所列稳定输入；错误预测的反例同样保留。
"""

from dataclasses import replace

import numpy as np
import pytest

from adi_model.behavioral.input_network import InputNetworkParameters, PassiveTrackingNetwork
from adi_model.behavioral.pretracking import InputAssistTrace, QuantizedPretracker
from adi_model.behavioral.sampler import dc_input
from adi_model.simulation.pipeline import run_pipeline


def test_future_or_same_group_decisions_never_become_a_valid_precharge():
    table = -3 + (np.arange(512) + 0.5) * 6 / 512
    tracker = QuantizedPretracker(18, codebook_v=table, signal_scale=1.0)
    tracker.record(0, 7e-9, np.arange(8), 320, known_injection_v=0.0)
    tracker.record(1, 32e-9, np.arange(8, 16), 480, known_injection_v=0.0)
    before = tracker.predict(np.arange(8, 16), 6e-9, "latest")
    assert not before.valid.any() and np.isnan(before.voltage_v).all()
    early = tracker.predict(np.arange(8, 16), 13e-9, "latest")
    assert early.valid.all()
    np.testing.assert_array_equal(early.source_ids[:, 0], 0)
    np.testing.assert_allclose(early.voltage_v, table[320], atol=0, rtol=0)
    later = tracker.predict(np.arange(8, 16), 33e-9, "latest")
    assert not later.valid.any()
    np.testing.assert_array_equal(later.source_ids, -1)
    assert np.isnan(later.voltage_v).all()


@pytest.mark.parametrize("code", [273, 0])
def test_actual_precharge_then_acquisition_has_no_universal_improvement_claim(code):
    cap, pre_ron, pre_source = 1e-12, 10.0, 20.0
    main_ron, main_source = 100.0, 300.0
    pre_end, final = 0.4e-9, 0.6e-9
    table = -3 + (np.arange(512) + 0.5) * 6 / 512
    tracker = QuantizedPretracker(18, codebook_v=table, signal_scale=1.0)
    tracker.record(0, 0.0, np.arange(8), code, known_injection_v=0.0)
    prediction = tracker.predict(np.arange(8, 16), 0.0, "latest")
    assert prediction.valid.all()
    target = prediction.voltage_v[0]
    precharge = PassiveTrackingNetwork([cap], [pre_ron], pre_source, 0.0)
    main = PassiveTrackingNetwork([cap], [main_ron], main_source, 0.0)
    start = precharge.advance([0.0], 0.0, pre_end, dc_input(target))
    acquired = main.advance(start, pre_end, final, dc_input(0.2))
    baseline = main.advance([0.0], pre_end, final, dc_input(0.2))
    expected_start = target * (1 - np.exp(-pre_end / ((pre_ron + pre_source) * cap)))
    expected = 0.2 + (expected_start - 0.2) * np.exp(
        -(final - pre_end) / ((main_ron + main_source) * cap)
    )
    np.testing.assert_allclose(acquired, expected, atol=5e-16, rtol=0)
    q_pre = precharge.charge_change([0.0], start)
    q_main = main.charge_change(start, acquired)
    assert q_pre + q_main == pytest.approx(cap * acquired[0], abs=1e-27)
    if code == 273:
        assert abs(acquired[0] - 0.2) < abs(baseline[0] - 0.2)
        assert abs(q_main) < abs(main.charge_change([0.0], baseline))
    else:
        assert abs(acquired[0] - 0.2) > abs(baseline[0] - 0.2)


def test_weighted_prediction_requires_complete_other_group_history_and_known_injection():
    # 六节点逻辑夹具只检查加权算术，不是18/8 ADC资源调度。
    table = np.linspace(-1.0, 1.0, 9)
    tracker = QuantizedPretracker(6, codebook_v=table, signal_scale=0.5, weights=[0.6, 0.3, 0.1])
    tracker.record(0, 1e-9, [0, 1], 1, known_injection_v=0.1)
    tracker.record(1, 2e-9, [0, 1], 4, known_injection_v=-0.1)
    warmup = tracker.predict([4, 5], 2e-9, "weighted")
    assert not warmup.valid.any()
    tracker.record(2, 3e-9, [2, 3], 7, known_injection_v=0.2)
    predicted = tracker.predict([4, 5], 3e-9, "weighted")
    expected = (0.6 * (table[7] - 0.2) + 0.3 * (table[4] + 0.1) + 0.1 * (table[1] - 0.1)) / 0.5
    np.testing.assert_allclose(predicted.voltage_v, expected, atol=1e-16)
    np.testing.assert_array_equal(predicted.source_ids, [[2, 1, 0], [2, 1, 0]])
    assert np.all(predicted.ready_times_s <= 3e-9)
    assert predicted.valid.all()
    with pytest.raises(ValueError, match="mode"):
        tracker.predict([4, 5], 3e-9, "own")


def test_auxiliary_branch_uses_a_separate_explicit_source_and_reset_charge():
    main_cap, aux_cap = 3e-12, 8e-12
    main = PassiveTrackingNetwork([main_cap], [20.0], 100.0, 0.0)
    aux = PassiveTrackingNetwork([aux_cap], [10.0], 50.0, 0.0)
    duration = 1e-9
    end_main = main.advance([0.0], 0, duration, dc_input(0.5))
    end_aux = aux.advance([0.0], 0, duration, dc_input(0.1))
    q_main = main.charge_change([0.0], end_main)
    q_aux = aux.charge_change([0.0], end_aux)
    assert q_main == pytest.approx(main_cap * 0.5 * (1 - np.exp(-duration / (120 * main_cap))))
    assert q_aux == pytest.approx(aux_cap * 0.1 * (1 - np.exp(-duration / (60 * aux_cap))))
    assert q_aux > 0 and q_main > 0
    reset_charge = aux.charge_change(end_aux, [0.0])
    assert reset_charge == pytest.approx(-q_aux)
    assert q_main != pytest.approx(q_main + q_aux, rel=0, abs=1e-25)
    np.testing.assert_array_equal(end_main, main.advance([0.0], 0, duration, dc_input(0.5)))


def test_input_assist_trace_does_not_invent_zero_voltage_predictions():
    trace = InputAssistTrace.allocate(5, 8, history=3)
    assert trace.pretrack_target_v.shape == (5, 8)
    assert trace.pretrack_source_ids.shape == (5, 8, 3)
    assert np.isnan(trace.pretrack_target_v).all()
    assert not trace.pretrack_valid.any()
    np.testing.assert_array_equal(trace.pretrack_source_ids, -1)
    np.testing.assert_array_equal(trace.pretrack_source_charge_c, 0)
    np.testing.assert_array_equal(trace.auxiliary_source_charge_c, 0)


@pytest.mark.parametrize("mode", ["latest", "weighted"])
def test_unbound_full_pipeline_dynamics_is_refused_instead_of_point_settling(
    explicit_pipeline_factory, mode
):
    cfg, controls = explicit_pipeline_factory(n_samples=4)
    parameters = InputNetworkParameters(
        filter_cap_f=2e-12,
        pretrack_mode=mode,
        pretrack_time_s=1e-9,
        pretrack_source_ohm=20.0,
        pretrack_weights=(0.6, 0.3, 0.1) if mode == "weighted" else None,
    )
    requested = replace(
        cfg,
        dyn_input_settling=True,
        dyn_r_source=100.0,
        dyn_r_on=20.0,
        dyn_ron_code_coeff=0.0,
        c_sadc=1e-12,
        input_network=parameters,
    )
    with pytest.raises(ValueError, match="continuous|binding"):
        run_pipeline(requested, dc_input(0.2), 4, **controls)
