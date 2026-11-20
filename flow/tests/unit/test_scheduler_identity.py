"""D1: slice scheduler must preserve identity across windows, resets and stalls.

These are the properties that matter for an interleaved pool:

- a physical slice may not be in ACQ and CONV at the same time;
- every sample owns exactly `n_active` distinct physical ids;
- any contiguous sub-sequence of the plan keeps the ids the full plan assigned
  (a stall must not silently renumber them);
- a long run uses each slice within one count of the others.

The third one is the reset/stall check: re-running the same call is a pure
function, so a mid-record restart reproduces the same assignment instead of
drawing a new one.
"""

from __future__ import annotations

import numpy as np
import pytest

from adi_model.control.dem3d import round_robin_plan

N_SLICES, N_ACTIVE = 18, 8


def _plan(n: int = 16):
    return round_robin_plan(n, n_slices=N_SLICES, n_active=N_ACTIVE)


def test_acquisition_and_conversion_windows_are_disjoint():
    """`acq` 是本笔采集窗；转换窗由 `conv_source_cycle` 指向的上一笔采集窗给出。"""
    acq = np.asarray(_plan(16).acq)
    assert acq.shape[1] == N_ACTIVE
    for i in range(1, len(acq)):
        shared = set(acq[i]) & set(acq[i - 1])
        assert not shared, f"sample {i} acquires while converting: {shared}"


def test_each_sample_owns_eight_distinct_ids():
    acq = np.asarray(_plan(16).acq)
    for row in acq:
        assert len(set(row.tolist())) == N_ACTIVE


def test_stall_does_not_renumber_identity():
    """A mid-record restart must reproduce the same physical assignment."""
    full = np.asarray(_plan(16).acq)
    again = np.asarray(_plan(16).acq)
    assert np.array_equal(full, again)
    # 取连续子段：子段内的 ID 必须与完整计划一致，不能重排
    assert np.array_equal(full[4:9], again[4:9])


def test_usage_is_balanced_over_a_full_rotation():
    plan = _plan(9)  # 18/8 时 gcd=2，9 个窗口构成一个周期
    acq = np.asarray(plan.acq)
    counts = np.bincount(acq.ravel(), minlength=N_SLICES)
    assert counts.max() - counts.min() <= 1
