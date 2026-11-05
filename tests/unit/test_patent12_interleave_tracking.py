"""检查基于已可用转换码的预跟踪，不用理想输入样本冒充转换结果。"""

import numpy as np
import pytest

from adi_model.pretracking import InputAssistTrace, QuantizedPretracker


def _tracker(*, weights=None):
    # 五个数值只用于独立数字查表数学；SADC 工厂仍按 9 位目标声明。
    return QuantizedPretracker(
        18, codebook_v=np.array([-3.0, -1.0, 0.0, 1.0, 3.0]), signal_scale=0.8, weights=weights
    )


def test_future_decision_is_not_available_before_ready_time():
    tracker = _tracker()
    tracker.record(1, 2e-9, [0, 1], 3, known_injection_v=0.2)
    before = tracker.predict([8, 9], 1e-9, "latest")
    assert not before.valid.any() and np.isnan(before.voltage_v).all()
    assert np.all(before.source_ids == -1)
    after = tracker.predict([8, 9], 2e-9, "latest")
    assert after.valid.all()
    np.testing.assert_allclose(after.voltage_v, 1.0)
    np.testing.assert_array_equal(after.source_ids[:, 0], [1, 1])
    np.testing.assert_array_equal(after.ready_times_s[:, 0], [2e-9, 2e-9])


def test_latest_cannot_use_its_own_or_partially_overlapping_group():
    tracker = _tracker()
    tracker.record(1, 1e-9, [0, 1], 3, known_injection_v=0)
    assert not tracker.predict([1, 8], 2e-9, "latest").valid.any()
    assert not tracker.predict([0, 1], 2e-9, "latest").valid.any()


def test_latest_does_not_fall_back_past_an_ineligible_recent_record():
    tracker = _tracker()
    tracker.record(1, 1e-9, [0, 1], 3, known_injection_v=0)
    tracker.record(2, 2e-9, [8, 9], 1, known_injection_v=0)
    result = tracker.predict([8, 9], 3e-9, "latest")
    assert not result.valid.any() and np.isnan(result.voltage_v).all()


def test_weighted_requires_explicit_weights_and_a_complete_foreign_history():
    tracker = _tracker(weights=[0.6, 0.3, 0.1])
    tracker.record(1, 1e-9, [0, 1], 1, known_injection_v=0)
    tracker.record(2, 2e-9, [2, 3], 3, known_injection_v=0)
    assert not tracker.predict([8, 9], 2e-9, "weighted").valid.any()
    tracker.record(3, 3e-9, [4, 5], 4, known_injection_v=0)
    result = tracker.predict([8, 9], 3e-9, "weighted")
    np.testing.assert_allclose(result.voltage_v, (0.6 * 3 + 0.3 * 1 - 0.1) / 0.8)
    np.testing.assert_array_equal(result.source_ids, [[3, 2, 1], [3, 2, 1]])
    with pytest.raises(ValueError, match="weights"):
        _tracker().predict([8, 9], 0.0, "weighted")


@pytest.mark.parametrize("mode", ["own", "reset_mid", "track_other", "ideal_input"])
def test_retired_or_unknown_modes_are_not_an_implicit_ideal_history(mode):
    with pytest.raises(ValueError, match="mode"):
        _tracker().predict([8, 9], 0.0, mode)


@pytest.mark.parametrize("weights", [[1], [-1, 2], [0, 1], [0.6, np.nan], [0.6, np.inf]])
def test_bad_weights_are_refused(weights):
    with pytest.raises(ValueError, match="weights"):
        _tracker(weights=weights)


def test_off_does_not_invent_a_reset_voltage():
    result = _tracker().predict([8, 9], 0.0, "off")
    assert not result.valid.any()
    assert np.isnan(result.voltage_v).all() and np.all(result.source_ids == -1)


@pytest.mark.parametrize("ids", [[-1], [18], [0, 0], [0.5], [True]])
def test_physical_ids_are_discrete_valid_and_unique(ids):
    with pytest.raises(ValueError):
        _tracker().predict(ids, 0.0, "latest")


@pytest.mark.parametrize("code", [-1, 5, 1.5, True])
def test_only_actual_integer_decisions_enter_the_history(code):
    with pytest.raises(ValueError):
        _tracker().record(1, 1e-9, [0, 1], code, known_injection_v=0)


def test_record_and_query_times_cannot_be_reordered():
    tracker = _tracker()
    tracker.record(1, 2e-9, [0, 1], 1, known_injection_v=0)
    with pytest.raises(ValueError):
        tracker.record(2, 1e-9, [2, 3], 3, known_injection_v=0)
    tracker.predict([8, 9], 3e-9, "latest")
    with pytest.raises(ValueError, match="monotonic"):
        tracker.predict([8, 9], 2e-9, "latest")


def test_common_weight_rescaling_does_not_change_the_declared_average():
    results = []
    for weights in ([0.6, 0.3, 0.1], [6, 3, 1]):
        tracker = _tracker(weights=weights)
        for i, code in enumerate([1, 3, 4]):
            tracker.record(i, i * 1e-9, [2 * i, 2 * i + 1], code, known_injection_v=0)
        results.append(tracker.predict([8, 9], 3e-9, "weighted"))
    np.testing.assert_allclose(results[0].voltage_v, results[1].voltage_v)
    assert not results[0].voltage_v.flags.writeable


def test_assistance_trace_keeps_unknown_voltage_distinct_from_zero_charge():
    trace = InputAssistTrace.allocate(3, 8, history=3)
    assert np.isnan(trace.pretrack_target_v).all()
    assert not trace.pretrack_valid.any()
    assert np.all(trace.pretrack_source_ids == -1)
    assert np.all(trace.pretrack_source_charge_c == 0)
    assert np.isnan(trace.auxiliary_voltage_v).all()
