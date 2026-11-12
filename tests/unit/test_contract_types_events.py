"""下沉类型与事件日志的最小测试。"""

from __future__ import annotations

import numpy as np
import pytest

from adi_model.contracts import EventLog, EventRecord, MapperTable, Q32, SwitchState


def test_switch_state_rejects_mismatched_rows():
    with pytest.raises(ValueError):
        SwitchState(
            plan=np.zeros((2, 2), dtype=int),
            codes=np.zeros(2, dtype=int),
            main=np.zeros((1, 2), dtype=int),
            sub=np.zeros((2, 2), dtype=int),
        )


def test_mapper_table_lookup_shapes():
    table = MapperTable(
        bridge=False,
        main=np.ones((4, 1, 2), dtype=np.uint8),
        sub=np.zeros((4, 1, 2), dtype=np.uint8),
        correction_q32=np.arange(4, dtype=np.int64),
        command_count=4,
        phase_count=1,
    )
    state, corr = table.lookup(
        np.array([0, 3]), np.zeros((2, 2), dtype=int), (np.zeros(2), np.zeros(2))
    )
    assert state.main.shape == (2, 2)
    np.testing.assert_array_equal(corr, np.array([0, 3]))


def test_mapper_table_rejects_out_of_range_command():
    table = MapperTable(
        bridge=False,
        main=np.ones((4, 1, 2), dtype=np.uint8),
        sub=np.zeros((4, 1, 2), dtype=np.uint8),
        correction_q32=np.zeros(4, dtype=np.int64),
        command_count=4,
        phase_count=1,
    )
    with pytest.raises(ValueError):
        table.lookup(np.array([4]), np.zeros((1, 2), dtype=int), (np.zeros(1), np.zeros(1)))


def test_event_log_orders_same_time_by_order_key():
    log = EventLog()
    log.add(EventRecord(1e-9, "digital", "C0", 0, 0, "sadc", "decision_valid", (2, 0)))
    log.add(EventRecord(1e-9, "digital", "C0", 0, 0, "sadc", "aperture", (1, 0)))
    log.add(EventRecord(0.5e-9, "analog", "S", 0, 0, "sampler", "aperture"))
    assert [e.kind for e in log.for_sample(0)] == ["aperture", "aperture", "decision_valid"]
    assert len(log) == 3
    assert Q32 == 1 << 32
