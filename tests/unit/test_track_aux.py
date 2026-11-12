"""TRACK 预置与 AUX 输入电荷记账的单元测试。

含外部审查 A03/A05 的两个回归点：
* 恒定直流输入的合法预置不得被判"偷看"（判据是时间与前缀，不是数值相等）；
* 正弦相邻样本差是峰值，峰峰值是它的两倍。
"""

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
    peak_current_for_dc_step,
    track_residual_charge,
)

FS = 40e6


def _plan_and_values(n: int = 64, fin: float = 1e6, amp: float = 3.0):
    plan = round_robin_plan(n)
    t = np.arange(n) / FS
    return plan, amp * np.sin(2 * np.pi * fin * t), np.arange(n, dtype=np.int64)


def _build(plan, codes, values, *, lead=5e-9, lookback=2):
    return build_track_plan(
        plan, codes, values_v=values, fs_hz=FS, track_lead_s=lead, lookback=lookback
    )


def test_track_plan_sources_are_strictly_earlier():
    plan, values, codes = _plan_and_values()
    track = _build(plan, codes, values)
    audit = causality_audit(track)
    assert audit["causal"]
    # 两相流水线下，来源至少要回看 2 笔：上一笔的判决要到本笔孔径时刻才完成
    assert audit["valid_sources"] == values.size - 2
    assert not track.valid[0] and not track.valid[1]
    assert np.isnan(track.predicted_v[0])


def test_lookback_one_is_not_causal_in_two_phase_pipeline():
    """lookback=1 时来源判决与本笔孔径同一时刻完成，没有预置窗口，必须全部无效。"""
    plan, values, codes = _plan_and_values(16)
    track = _build(plan, codes, values, lookback=1)
    audit = causality_audit(track)
    assert audit["valid_sources"] == 0
    assert not audit["causal"]  # 来源晚于预置窗口
    assert audit["late_sources"] == values.size - 1


def test_dc_input_is_not_flagged_as_cheating():
    """外部审查 A03：恒定 1 V 输入下预测必然等于目标，那不是偷看。"""
    plan = round_robin_plan(8)
    values = np.ones(8)
    codes = np.zeros(8, dtype=np.int64)
    track = _build(plan, codes, values, lookback=2)
    audit = causality_audit(track)
    assert audit["causal"], audit
    assert audit["future_sources"] == 0
    assert audit["late_sources"] == 0


def test_two_sample_lookback_always_has_time():
    """回看 2 笔时，来源判决总在预置窗口之前完成；lead 只影响窗口长度。"""
    plan = round_robin_plan(8)
    values, codes = np.zeros(8), np.zeros(8, dtype=np.int64)
    track = build_track_plan(
        plan, codes, values_v=values, fs_hz=FS, track_lead_s=0.9 / FS, lookback=2
    )
    assert np.all(track.valid[2:])
    assert not track.valid[0] and not track.valid[1]
    audit = causality_audit(track)
    assert audit["causal"]
    assert audit["late_sources"] == 0


def test_track_rejects_bad_parameters():
    plan, values, codes = _plan_and_values(8)
    with pytest.raises(ConfigError):
        _build(plan, codes[:-1], values)
    with pytest.raises(ConfigError):
        _build(plan, codes, values, lookback=0)
    with pytest.raises(ConfigError):
        _build(plan, codes, values, lead=2 / FS)


def test_track_reduces_input_charge_for_slow_input():
    from tests.unit.test_dither_port import _uniform_pool

    pool = _uniform_pool()
    plan, values, codes = _plan_and_values(64, fin=0.5e6)
    track = _build(plan, codes, values)
    report = track_residual_charge(pool, track, values)
    assert report["charge_reduction"] > 4
    assert report["samples_without_source"] == 2


def test_consecutive_charge_distinguishes_peak_and_pp():
    fs, fin, amp, cap = 40e6, 1e6, 3.0, 20.5e-12
    report = consecutive_sample_charge(cap, amp, fin, fs)
    peak = 2 * amp * np.sin(np.pi * fin / fs)
    assert report["delta_v_peak"] == pytest.approx(peak)
    assert report["delta_v_pp"] == pytest.approx(2 * peak)
    assert report["current_a_pp"] == pytest.approx(2 * report["current_a_peak"])


def test_dc_step_current_is_a_throughput_bound():
    current = peak_current_for_dc_step(20.5e-12, 6.0, FS)
    assert current == pytest.approx(20.5e-12 * 6.0 * FS)
    with pytest.raises(ConfigError):
        peak_current_for_dc_step(20.5e-12, 0.0, FS)


def test_current_and_frequency_reject_bad_arguments():
    with pytest.raises(ConfigError):
        input_current_a(1e-12, 0.0)
    with pytest.raises(ConfigError):
        consecutive_sample_charge(1e-12, 3.0, 30e6, 40e6)
