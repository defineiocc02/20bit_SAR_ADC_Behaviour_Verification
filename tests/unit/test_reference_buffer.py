"""参考缓冲器结构判据的单元测试（依据 US10826519B1 FIG.5）。

FIG.5（Sheet 5 of 16）结构：
  MN1 栅=Vref，MN2 栅=Vref_internal（反馈），MP1/MP2 为电流镜，
  MP3 由 INV 驱动输出 Vref_internal，MN3 为 Vbias 尾电流，
  右侧调节框接 Threshold_adjust 与 Trial<N:1>。

从结构得到三条语义，本文件逐条钉住：
  ① 闭环单位增益（Vref_internal 跟随 Vref）
  ② 电流镜 + 尾电流 ⇒ 可搬运电荷受 I_tail × 时间限制
  ③ 参考不是绝对静止（有阈值调节与多次试验）
"""

from __future__ import annotations

import pytest

from adi_model.control.reference_buffer import (
    closed_loop_output,
    reference_charge_budget,
    reference_is_static,
    tracking_error_v,
)
from adi_model.simulation.config import ConfigError


def test_unity_gain_feedback_tracks_the_reference():
    """① 单位增益：输出等于输入（反馈把 MN2 拉到 Vref）。"""
    for vref in (0.0, 0.5, 1.0, 2.5, -1.0):
        assert closed_loop_output(vref_v=vref) == pytest.approx(vref)
        assert tracking_error_v(vref_v=vref) == pytest.approx(0.0)


def test_offset_is_the_only_steady_state_error_at_unity_gain():
    """① 补：单位增益下的跟随误差就等于闭环失调。"""
    assert tracking_error_v(vref_v=1.2, offset_v=1e-3) == pytest.approx(1e-3)


def test_non_unity_gain_is_a_sensitivity_knob_not_a_claim():
    """① 补：增益偏离 1 时误差随 Vref 成比例 —— 用来做灵敏度对照。"""
    assert closed_loop_output(vref_v=1.0, gain=1.01) == pytest.approx(1.01)
    assert tracking_error_v(vref_v=2.0, gain=1.01) == pytest.approx(0.02)


def test_charge_budget_is_tail_current_times_window():
    """② 可搬运电荷上界 = I_tail × 时间窗口（电流镜把电流限死在尾电流）。"""
    budget = reference_charge_budget(
        tail_current_a=1e-3, window_s=12.5e-9, load_cap_f=2e-12, swing_v=0.5
    )
    assert budget.available_c == pytest.approx(1e-3 * 12.5e-9)
    assert budget.required_c == pytest.approx(2e-12 * 0.5)
    assert budget.sufficient is True
    assert budget.margin_c == pytest.approx(budget.available_c - budget.required_c)


def test_charge_budget_flags_a_drooping_reference():
    """② 补：负载需要的电荷超过 I_tail×t 时，参考会被拉塌（判为不足）。"""
    budget = reference_charge_budget(
        tail_current_a=1e-6, window_s=1e-9, load_cap_f=1e-9, swing_v=1.0
    )
    assert budget.required_c == pytest.approx(1e-9)
    assert budget.available_c == pytest.approx(1e-15)
    assert budget.sufficient is False
    assert budget.margin_c < 0


def test_reference_only_counts_as_static_without_adjust_and_single_trial():
    """③ Threshold_adjust 使能 或 N>1 ⇒ 不能把参考当静止常数。"""
    assert reference_is_static(threshold_adjust_enabled=False, trial_count=1) is True
    assert reference_is_static(threshold_adjust_enabled=True, trial_count=1) is False
    assert reference_is_static(threshold_adjust_enabled=False, trial_count=9) is False
    assert reference_is_static(threshold_adjust_enabled=True, trial_count=9) is False


def test_rejects_illegal_arguments():
    """非法参数必须报错，不能静默给出结论。"""
    with pytest.raises(ConfigError):
        closed_loop_output(vref_v=1.0, gain=0.0)
    with pytest.raises(ConfigError):
        closed_loop_output(vref_v=1.0, gain=3.0)
    with pytest.raises(ConfigError):
        closed_loop_output(vref_v=float("nan"))
    with pytest.raises(ConfigError):
        reference_charge_budget(tail_current_a=0.0, window_s=1e-9, load_cap_f=0.0, swing_v=0.0)
    with pytest.raises(ConfigError):
        reference_charge_budget(tail_current_a=1e-3, window_s=0.0, load_cap_f=0.0, swing_v=0.0)
    with pytest.raises(ConfigError):
        reference_is_static(threshold_adjust_enabled=False, trial_count=0)
    with pytest.raises(ConfigError):
        reference_is_static(threshold_adjust_enabled=False, trial_count=True)
