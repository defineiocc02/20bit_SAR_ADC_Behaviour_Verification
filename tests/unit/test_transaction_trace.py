"""V2 事务 trace 的三段主键与连续性判据。

V2 的要求（HANDOVER §4-P2）：
  "事务 trace（epoch + sample_id + event_index）"

三段主键的意义：没有它，顶层对拍只能比总量，无法定位"哪一笔的第几个事件错了"。
"""

from __future__ import annotations

import numpy as np
import pytest

from adi_model.defaults import DefaultModel, DefaultParameters
from adi_model.simulation.config import ConfigError
from adi_model.simulation.transaction_trace import (
    TransactionTrace,
    build_trace_from_result,
    event_index_restarts_each_sample,
    transaction_continuity,
)


def _model():
    return DefaultModel(seed=20261105, parameters=DefaultParameters(), config_overrides=None)


def test_trace_has_one_row_per_sample_and_keys_are_unique():
    """每个样本一行，主键 (sample_id, event_index) 唯一。"""
    result = _model().simulate(lambda t: 0.5, 6)
    trace = build_trace_from_result(result=result.raw, epoch=0)
    assert trace.epoch == 0
    assert trace.n_rows == 6
    assert trace.n_samples == 6
    assert np.all(trace.event_index == 0)


def test_trace_carries_the_terminal_codes():
    """trace 里的码必须与结果里的终态码逐项相同（不做二次换算）。"""
    result = _model().simulate(lambda t: 0.5, 4)
    trace = build_trace_from_result(result=result.raw, epoch=3)
    assert trace.epoch == 3
    assert np.array_equal(trace.rdac_code, np.asarray(result.raw.k, dtype=np.int64))
    assert np.array_equal(trace.coarse_code, np.asarray(result.raw.coarse, dtype=np.int64))


def test_event_index_must_restart_at_zero_for_each_sample():
    """event_index 每笔从 0 开始且连续（否则无法按笔定位事件）。"""
    assert event_index_restarts_each_sample(
        sample_id=[0, 0, 0, 1, 1], event_index=[0, 1, 2, 0, 1]
    ) is True
    # 不从 0 开始
    assert event_index_restarts_each_sample(
        sample_id=[0, 0], event_index=[1, 2]
    ) is False
    # 中间缺一格
    assert event_index_restarts_each_sample(
        sample_id=[0, 0, 0], event_index=[0, 2, 3]
    ) is False


def test_duplicate_keys_are_rejected():
    """主键重复必须拒绝 —— 重复行会让对拍丢事件。"""
    with pytest.raises(ConfigError):
        TransactionTrace(
            epoch=0,
            sample_id=np.array([0, 0]),
            event_index=np.array([0, 0]),
            coarse_code=np.array([1, 2]),
            rdac_code=np.array([1, 2]),
        )


def test_columns_must_share_one_length():
    """各列长度不一致必须拒绝。"""
    with pytest.raises(ConfigError):
        TransactionTrace(
            epoch=0,
            sample_id=np.array([0, 1]),
            event_index=np.array([0]),
            coarse_code=np.array([1, 2]),
            rdac_code=np.array([1, 2]),
        )


def test_sample_conservation():
    """样本守恒：N_accepted = N_retired + N_cancelled + N_pending。"""
    assert transaction_continuity(n_accepted=8, n_retired=6, n_cancelled=1, n_pending=1) is True
    assert transaction_continuity(n_accepted=8, n_retired=6, n_cancelled=0, n_pending=1) is False
    # 凭空多出来
    assert transaction_continuity(n_accepted=8, n_retired=9, n_cancelled=0, n_pending=0) is False


def test_illegal_arguments_are_rejected():
    """非法参数一律报错，不静默修补。"""
    with pytest.raises(ConfigError):
        TransactionTrace(
            epoch=-1,
            sample_id=np.array([0]),
            event_index=np.array([0]),
            coarse_code=np.array([1]),
            rdac_code=np.array([1]),
        )
    with pytest.raises(ConfigError):
        transaction_continuity(n_accepted=-1, n_retired=0, n_cancelled=0, n_pending=0)
    with pytest.raises(ConfigError):
        transaction_continuity(n_accepted=True, n_retired=0, n_cancelled=0, n_pending=0)
    with pytest.raises(ConfigError):
        event_index_restarts_each_sample(sample_id=[0], event_index=[0, 1])
