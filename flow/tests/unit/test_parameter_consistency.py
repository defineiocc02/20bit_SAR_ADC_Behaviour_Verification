# =============================================================================
# 文件名     : test_parameter_consistency.py
# 模块名     : Fig.11 量程研究
# 版本       : v1.0
# 功能       : 按指定的增益、一级码步与二级窗口计算剩余余量。
# 设计说明   : 这里的上段需求来自显式码移实验，生产接口按实际开关重构。
# 变更记录   : 区分全字通路与断开采样高段的压力预算。
# =============================================================================
"""理想匹配的 Fig.19 两路抵消 U。这里专门核算人为断开一路后所需的额外余量。"""

from __future__ import annotations

import pytest

from experiments.fig11 import Fig11Model, Fig11Parameters
from adi_model.simulation.config import ConfigError
from adi_model.simulation.parameter_consistency import (
    check_dither_fits_stage2,
    dither_requirement_lsb1,
    stage2_capacity,
)

V_FS = 3.0


# =============================================================================
# 1. 手算窗口和未抵消上段的范围
# =============================================================================
def test_stage2_capacity_matches_hand_calculation():
    """覆盖量 = 窗口 / (增益 × 一级 LSB)，可手算复现。"""
    cap = stage2_capacity(v_fs=V_FS, gain=32.0, adc2_v_min=-0.0375, adc2_v_max=0.4125)
    assert cap.lsb1_v == pytest.approx(2 * V_FS / 512)
    assert cap.window_v == pytest.approx(0.45)
    assert cap.coverage_lsb1 == pytest.approx(0.45 / (32.0 * (2 * V_FS / 512)))


def test_dither_requirement_counts_margin_on_top_of_upper_code():
    """需求 = upper 段最大码 + 量化余量。"""
    assert dither_requirement_lsb1(upper_code_max=5) == pytest.approx(5.5)
    assert dither_requirement_lsb1(upper_code_max=0) == pytest.approx(0.5)
    assert dither_requirement_lsb1(upper_code_max=2, quantization_margin=0.0) == pytest.approx(2.0)


def test_uncancelled_upper_stress_reports_the_known_gap():
    """假设 U 未在余差里抵消，窗口的压力预算缺口可独立复算。"""
    model = Fig11Model(seed=20261105, parameters=Fig11Parameters(), config_overrides=None)
    report = model.dither_stage2_consistency
    assert report.sufficient is False
    # 13 bit / 0.75 V 下实测 2.0 LSB1（原 14 bit/0.45 V 时 1.2）
    assert report.capacity.coverage_lsb1 == pytest.approx(2.0, abs=0.01)
    assert report.needed_lsb1 == pytest.approx(5.5)
    assert report.deficit_lsb1 == pytest.approx(3.5, abs=0.01)
    assert "Uncancelled-upper stress only" in report.summary()


def test_report_is_visible_in_the_inference_table():
    """缺口必须出现在 inference 表里，读者不必从测试失败去反推。"""
    model = Fig11Model(seed=20261105, parameters=Fig11Parameters(), config_overrides=None)
    entry = model.inference_table()["stage2_capacity_vs_dither"]
    assert entry["value"]["scope"] == "uncancelled_upper_stress_only"
    assert entry["value"]["sufficient"] is False
    # 13 bit / 0.75 V 下缺 3.5 LSB1（原 14 bit/0.45 V 时缺 4.3）
    assert entry["value"]["deficit_lsb1"] == pytest.approx(3.5, abs=0.01)
    assert entry["grade"] == "DERIVED_FROM_CURRENT_PARAMETERS"


def test_sufficient_case_is_reported_as_such():
    """把窗口放大到自洽尺度后，报告必须翻成 sufficient（反向检验）。"""
    # 需要 5.5 LSB1 的余量：窗口 = 5.5 × 32 × (6/512) = 2.0625 V
    report = check_dither_fits_stage2(
        v_fs=V_FS, gain=32.0, adc2_v_min=-1.0, adc2_v_max=1.0625, upper_code_max=5
    )
    assert report.sufficient is True
    assert report.deficit_lsb1 == 0.0
    assert "verdict sufficient" in report.summary()


def test_rejects_illegal_parameters():
    """非法参数必须报错，不能静默给出结论。"""
    with pytest.raises(ConfigError):
        stage2_capacity(v_fs=0.0, gain=32.0, adc2_v_min=-0.1, adc2_v_max=0.1)
    with pytest.raises(ConfigError):
        stage2_capacity(v_fs=V_FS, gain=0.0, adc2_v_min=-0.1, adc2_v_max=0.1)
    with pytest.raises(ConfigError):
        stage2_capacity(v_fs=V_FS, gain=32.0, adc2_v_min=0.1, adc2_v_max=0.1)
    with pytest.raises(ConfigError):
        dither_requirement_lsb1(upper_code_max=-1)
    with pytest.raises(ConfigError):
        dither_requirement_lsb1(upper_code_max=True)
