# =============================================================================
# 文件名     : test_impairments.py
# 模块名     : src.tests.test_impairments
# 版本       : v1.0
# 功能       : M10a 判据——非理想效应开关的默认全关、单项隔离与未实现项报错。
#
# 设计说明   :
#   * 这里最重要的是「默认全关时逐位等于原配置」和「打开未实现项立刻报错」。
#     前者保证开关不会悄悄改变既有结果，后者保证不会出现「打开了但什么都没发生」
#     的假实验——那比没有开关更坏，因为曲线上看不出异常。
#   * 单项隔离不是靠「看数值不同」验，而是看 sigma 字段：只有被打开的那一项
#     非零，其余必须严格为 0。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，M10a。
# =============================================================================

"""M10a 判据：开关的默认全关、单项隔离与未实现项报错。"""

from __future__ import annotations

import numpy as np
import pytest

from adi_model.capacitors import ExperimentConfig
from adi_model.impairments import (
    IMPLEMENTED,
    PLANNED,
    ImpairmentSet,
    all_of,
    only,
)
from adi_model.noise import NoiseConfig

BUDGET = {"reference": 30e-6, "comparator": 20e-6, "adc2": 3e-6, "ra_offset": 80e-6}


def test_everything_is_off_by_default():
    """默认不打开任何项，且 noise/reference 都退回全关配置。"""
    panel = ImpairmentSet()
    assert panel.enabled_names() == ()
    assert panel.planned_enabled() == ()
    noise = panel.to_noise(BUDGET)
    assert not noise.enabled
    assert noise.reference_sigma_v == 0.0
    assert noise.comparator_sigma_v == 0.0
    assert noise.adc2_sigma_v == 0.0
    assert noise.ra_offset_v == 0.0
    assert not panel.to_reference().enabled


def test_applying_the_empty_panel_leaves_the_config_untouched():
    """空面板套上去，配置必须与原来相等——开关不能悄悄改默认路径。"""
    base = ExperimentConfig()
    applied = ImpairmentSet().apply(base, BUDGET)
    assert applied == base
    assert applied.noise == base.noise
    assert applied.reference == base.reference


@pytest.mark.parametrize("name", IMPLEMENTED)
def test_single_switch_touches_only_its_own_field(name):
    """只开一项时，其它源的 sigma 必须严格为 0，不能有串扰。"""
    panel = only(name)
    assert panel.enabled_names() == (name,)
    noise = panel.to_noise(BUDGET)
    fields = {
        "reference_noise": "reference_sigma_v",
        "comparator_noise": "comparator_sigma_v",
        "adc2_noise": "adc2_sigma_v",
        "ra_offset": "ra_offset_v",
    }
    for other, field in fields.items():
        expected = BUDGET[field.replace("_sigma_v", "").replace("_v", "")] if other == name else 0.0
        assert getattr(noise, field) == pytest.approx(expected, rel=0, abs=1e-18)
    if name == "ktc":
        assert noise.include_thermal
    elif name == "reference_settling":
        # 参考建立不是噪声项：它不该在 NoiseConfig 上留下任何痕迹。
        assert noise == NoiseConfig()
    else:
        assert not noise.include_thermal


def test_ktc_switch_uses_the_capacitance_not_a_sigma_field():
    """kT/C 不给 sigma 字段，由采样电容算；20.5 pF 差分是 20.102 µV。"""
    noise = only("ktc").to_noise(BUDGET)
    assert noise.enabled
    assert noise.sampling_cap_f == pytest.approx(20.5e-12, rel=0)
    assert noise.thermal_sigma_v * np.sqrt(2.0) == pytest.approx(20.102e-6, rel=1e-4)


def test_reference_settling_switch_uses_the_reversed_time_constant():
    """参考建立打开时，τ 必须是 0.65·T_RA/ln(32)，不是随手给的。"""
    t_ra = 12.5e-9
    ref = only("reference_settling").to_reference(t_ra)
    assert ref.enabled
    assert ref.tau_s == pytest.approx(0.187550 * t_ra, rel=1e-5)
    assert ref.t_ra_s == t_ra


@pytest.mark.parametrize("name", PLANNED)
def test_opening_an_unwired_impairment_fails_loudly(name):
    """未接入的项打开就必须报错，并在消息里说清缺什么。"""
    with pytest.raises(NotImplementedError, match=name):
        only(name).apply(ExperimentConfig(), BUDGET)


def test_all_of_combines_switches():
    """组合开关按名字逐项打开，其余仍为关。"""
    panel = all_of(("ktc", "comparator_noise"))
    assert panel.enabled_names() == ("ktc", "comparator_noise")
    assert panel.planned_enabled() == ()
    noise = panel.to_noise(BUDGET)
    assert noise.include_thermal
    assert noise.comparator_sigma_v == pytest.approx(20e-6, rel=0)
    assert noise.reference_sigma_v == 0.0


def test_unknown_name_is_rejected():
    """写错名字要报错并列出可选项，不能静默变成全关。"""
    with pytest.raises(ValueError, match="unknown impairment"):
        only("comparator_noize")


def test_panel_with_a_planned_item_reports_all_of_them():
    """同时打开多个未实现项时，报错要一次列全，不要只报第一个。"""
    panel = all_of(("c_v_nonlinearity", "dac_bandwidth"))
    assert set(panel.planned_enabled()) == {"c_v_nonlinearity", "dac_bandwidth"}
    with pytest.raises(NotImplementedError) as info:
        panel.apply(ExperimentConfig())
    message = str(info.value)
    assert "c_v_nonlinearity" in message
    assert "dac_bandwidth" in message
