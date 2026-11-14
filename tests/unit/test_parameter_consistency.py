"""参数自洽性核算的单元测试（ADC2 量程 vs dither 幅度）。

这条检查把"4 个 dither 测试长期失败"变成一条可读断言：
ADC2 窗口只能覆盖约 1.2 个一级 LSB，而 dither 的 upper 段需要 5.5 个。
"""

from __future__ import annotations

import pytest

from adi_model.defaults import DefaultModel, DefaultParameters
from adi_model.simulation.config import ConfigError
from adi_model.simulation.parameter_consistency import (
    check_dither_fits_stage2,
    dither_requirement_lsb1,
    stage2_capacity,
)

V_FS = 3.0


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


def test_default_model_reports_the_known_gap():
    """默认参数下二级 ADC 装不下 dither：缺口必须被显式报出，且量级可核对。"""
    model = DefaultModel(seed=20261105, parameters=DefaultParameters(), config_overrides=None)
    report = model.dither_stage2_consistency
    assert report.sufficient is False
    assert report.capacity.coverage_lsb1 < 1.3
    assert report.needed_lsb1 == pytest.approx(5.5)
    assert report.deficit_lsb1 > 4.0
    assert "not mutually consistent" in report.summary()


def test_report_is_visible_in_the_inference_table():
    """缺口必须出现在 inference 表里，读者不必从测试失败去反推。"""
    model = DefaultModel(seed=20261105, parameters=DefaultParameters(), config_overrides=None)
    entry = model.inference_table()["stage2_capacity_vs_dither"]
    assert entry["value"]["sufficient"] is False
    assert entry["value"]["deficit_lsb1"] > 4.0
    assert entry["grade"] == "DERIVED_FROM_CURRENT_PARAMETERS"


def test_sufficient_case_is_reported_as_such():
    """把窗口放大到自洽尺度后，报告必须翻成 sufficient（反向检验）。"""
    # 需要 5.5 LSB1 的余量：窗口 = 5.5 × 32 × (6/512) = 2.0625 V
    report = check_dither_fits_stage2(
        v_fs=V_FS, gain=32.0, adc2_v_min=-1.0, adc2_v_max=1.0625, upper_code_max=5
    )
    assert report.sufficient is True
    assert report.deficit_lsb1 == 0.0
    assert "dither fits" in report.summary()


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
