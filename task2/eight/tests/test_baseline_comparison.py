# =============================================================================
# 文件名     : test_baseline_comparison.py
# 模块名     : test_baseline_comparison
# 版本       : v1.0
# 功能       : 验证跨平台标量包络不会放过真实回归或非有限值。
# 设计说明   :
#   [边界] strict 默认不变；portable 不放宽电压标度、版本和种子。
#   [对抗] 容限外的正负变化均须被拒绝，不把一次通过当作检查器正确。
# 变更记录   : 增加容限内／外、单位、环境与 NaN／Inf 定向用例。
# =============================================================================

"""数值基线比较器的独立边界测试。"""

from copy import deepcopy

import pytest
from tools.snapshot_baseline import METRICS, compare


def fixture():
    """使用人工零标量，避免测试答案取自当前实验结果。"""
    return {
        "environment": {"python": "test"},
        "params": {"seed": 100},
        "metrics": dict.fromkeys((name for name, _ in METRICS), 0.0),
    }


def test_default_remains_strict():
    """未显式选 portable 时，浮点尾数差也须报告。"""
    expected = fixture()
    actual = deepcopy(expected)
    actual["metrics"]["fig21_dem_sfdr_lms_dbc"] = 3.9e-11
    assert compare(expected, actual)
    assert not compare(expected, actual, portable=True)


@pytest.mark.parametrize("delta", [-1.01e-9, 1.01e-9, 1e-6])
def test_beyond_portable_envelope_fails(delta):
    """比包络大 1% 也拒绝，不能将真实指标变化伪装成平台差。"""
    expected = fixture()
    actual = deepcopy(expected)
    actual["metrics"]["fig21_dem_inl_on_lms_lsb20"] = delta
    assert compare(expected, actual, portable=True)


def test_portable_boundary_is_absolute():
    """零参考值与 1e-9 之差可手算，不使用相对容限。"""
    expected = fixture()
    actual = deepcopy(expected)
    actual["metrics"]["fig21_dem_sndr_lms_db"] = 1e-9
    assert not compare(expected, actual, portable=True)


@pytest.mark.parametrize("key", ["native_dither_step_v", "output_lsb20_v"])
def test_voltage_scale_is_exact_in_both_modes(key):
    """单位步长与输出 LSB 的任意变化都不能通过 portable。"""
    expected = fixture()
    actual = deepcopy(expected)
    actual["metrics"][key] = 1e-15
    assert compare(expected, actual, portable=True)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_values_never_pass(value):
    """NaN 的比较特性不得绕过误差包络。"""
    expected = fixture()
    actual = deepcopy(expected)
    actual["metrics"]["fig21_dem_sndr_lms_db"] = value
    assert compare(expected, actual, portable=True)


def test_metadata_remains_exact():
    """软件环境或实验种子变化必须单独报告，不能被数值容限忽略。"""
    expected = fixture()
    actual = deepcopy(expected)
    actual["environment"]["python"] = "other"
    actual["params"]["seed"] = 101
    assert len(compare(expected, actual, portable=True)) == 2
