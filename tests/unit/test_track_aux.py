"""TRACK 预置与 AUX 输入电荷记账的单元测试。"""

from __future__ import annotations

import numpy as np
import pytest

from adi_model.config import ConfigError
from adi_model.dem3d import round_robin_plan
from adi_model.track_aux import (
    build_track_plan,
    causality_audit,
    consecutive_sample_charge,
    input_current_a,
    solve_frequency_for_current,
    track_residual_charge,
)


def _plan_and_values(n: int = 64, fs: float = 40e6, fin: float = 1e6, amp: float = 3.0):
    plan = round_robin_plan(n)
    t = np.arange(n) / fs
    return plan, amp * np.sin(2 * np.pi * fin * t), np.arange(n, dtype=np.int64)


def test_track_plan_sources_are_strictly_earlier():
    plan, values, codes = _plan_and_values()
    track = build_track_plan(plan, codes, values_v=values, lookback=1)
    audit = causality_audit(track)
    assert audit["causal"]
    assert audit["valid_sources"] == values.size - 1
    assert not track.valid[0]
    assert np.isnan(track.predicted_v[0])
    assert np.all(track.source_sample[1:] == np.arange(values.size - 1))


def test_self_prediction_is_flagged():
    plan, values, codes = _plan_and_values(16)
    track = build_track_plan(plan, codes, values_v=values, lookback=1)
    # 构造一个"偷看目标自身"的计划
    cheating = type(track)(
        track.target_slices,
        np.arange(16, dtype=np.int64),
        codes,
        values,
        np.ones(16, bool),
    )
    audit = causality_audit(cheating, target_values_v=values)
    assert not audit["causal"]
    assert audit["cheating"]
    assert audit["self_sources"] > 0


def test_track_reduces_input_charge_for_slow_input():
    from tests.unit.test_dither_port import _uniform_pool

    pool = _uniform_pool()
    plan, values, codes = _plan_and_values(64, fin=0.5e6)
    track = build_track_plan(plan, codes, values_v=values, lookback=1)
    report = track_residual_charge(pool, track, values)
    assert report["charge_reduction"] > 10
    assert report["samples_without_source"] == 1


def test_track_rejects_mismatched_shapes():
    plan, values, codes = _plan_and_values(8)
    with pytest.raises(ConfigError):
        build_track_plan(plan, codes[:-1], values_v=values)
    with pytest.raises(ConfigError):
        build_track_plan(plan, codes, values_v=values, lookback=0)


def test_consecutive_sample_charge_matches_direct_difference():
    fs, fin, amp, cap = 40e6, 1e6, 3.0, 20.5e-12
    report = consecutive_sample_charge(cap, amp, fin, fs)
    direct = 2 * amp * np.sin(np.pi * fin / fs)
    assert report["delta_v_pp"] == pytest.approx(direct)
    assert report["current_a_pp"] == pytest.approx(cap * direct * fs)
    # 线性近似会高估，必须能被看出来
    approx = cap * (2 * np.pi * fin / fs * amp) * fs
    assert approx > report["current_a_pp"]


def test_solve_frequency_reproduces_paper_input_current():
    fs, cap = 40e6, 20.5e-12
    frequency = solve_frequency_for_current(350e-6, cap, 3.0, fs)
    report = consecutive_sample_charge(cap, 3.0, frequency, fs)
    assert report["current_a_pp"] == pytest.approx(350e-6, rel=1e-9)
    # 20.5 pF、±3 V、40 MS/s 下，350 uA_pp 对应约 0.91 MHz 的相邻样本差分
    assert 0.7e6 < frequency < 1.2e6


def test_current_and_frequency_reject_bad_arguments():
    with pytest.raises(ConfigError):
        input_current_a(1e-12, 0.0)
    with pytest.raises(ConfigError):
        consecutive_sample_charge(1e-12, 3.0, 30e6, 40e6)
    with pytest.raises(ConfigError):
        solve_frequency_for_current(10.0, 20.5e-12, 3.0, 40e6)
