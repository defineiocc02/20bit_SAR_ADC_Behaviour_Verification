# =============================================================================
# 文件名     : test_auxiliary_charge.py
# 模块名     : test_auxiliary_charge
# 版本       : v1.0
# 功能       : 核对辅助电荷源的方向、量纲与零电荷兼容性。
# 设计说明   :
#   [独立参考] 用单主电容、单子电容网络的手算解验证 KCL，避免以被测
#   行为模型的 affine 公式构造期望值。电荷源不是已完成的模拟开关实现。
# 变更记录   : 增加显式辅助电荷源合同的定向测试。
# =============================================================================

"""辅助电荷源的独立单节点边界测试。"""

import numpy as np
import pytest

from adi_model.behavioral.charge_ref_task import ref_ra_charge, ref_ra_charge_nodal


def network() -> dict:
    """返回所有底板静止的 1 F 测试网络，仅用于可手算的单位检查。"""
    return {
        "main_cap_f": [1.0],
        "sub_cap_f": [1.0],
        "bridge_cap_f": 1.0,
        "sub_parasitic_f": 0.0,
        "feedback_cap_f": 1.0,
        "sample_main_v": [0.0],
        "sample_sub_v": [0.0],
        "conversion_main_v": [0.0],
        "conversion_sub_v": [0.0],
    }


def test_charge_enters_kcl_before_solving():
    """2Vs=Q、Vs+Vr=0，故 ±2 C 对应 ∓1 V，而不是求解后修正输出。"""
    for charge, expected in ((2.0, -1.0), (-2.0, 1.0), (0.0, 0.0)):
        assert ref_ra_charge_nodal(**network(), extra_sub_charge_c=charge) == expected


def test_zero_auxiliary_charge_preserves_bottom_plate_network():
    """默认电荷为零必须与原有闭式底板网络相同；正向底板与电荷源符号相反。"""
    args = network()
    args["sample_sub_v"] = [2.0]
    assert ref_ra_charge_nodal(**args) == ref_ra_charge(**args) == 1.0


@pytest.mark.parametrize("charge", [float("nan"), float("inf"), 1j])
def test_invalid_charge_is_rejected(charge):
    """拒绝非实有限电荷，不能让 NaN 或复数通过 oracle。"""
    with pytest.raises(ValueError):
        ref_ra_charge_nodal(**network(), extra_sub_charge_c=charge)


def test_charge_broadcast_tracks_each_record():
    """输入前导维度为记录维，辅助电荷不得串笔广播到别的样本。"""
    args = network()
    args["sample_main_v"] = np.zeros((3, 1))
    np.testing.assert_array_equal(
        ref_ra_charge_nodal(**args, extra_sub_charge_c=[0, 2, -2]), [0, -1, 1]
    )
