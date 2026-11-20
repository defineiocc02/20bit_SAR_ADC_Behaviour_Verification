# =============================================================================
# 文件名     : test_fig21_bridge.py
# 模块名     : test_fig21_bridge
# 版本       : v1.0
# 功能       : 检查 Fig.21 跨段候选的开关位序与独立两节点补偿。
#
# 设计说明   :
#   * 承担 单元与独立电荷参考测试 中的一环；输入输出的物理口径与单位以同目录 SPEC/README 为准。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# =============================================================================

"""检查 Fig.21 跨段候选的开关位序与独立两节点补偿。"""

import numpy as np
import pytest

from adi_model.behavioral.charge_ref_task import ref_ra_charge_nodal
from adi_model.control.fig11_dem import Fig11Masks, map_fig11
from adi_model.control.fig21_bridge import map_fig21_bridge


# =========================================================================
# 1. 函数组：test_bridge_wraps_columns_and_slots / test_bridge_model_rejects_out_of_range_fields / _feedback_charge
# =========================================================================
# -------------------------------------------------------------------------
# 尾端保留列和起始片回绕后仍绑定到正确的物理开关。
# 软件层拒绝超出 RTL 位宽的字段。
# -------------------------------------------------------------------------
def test_bridge_wraps_columns_and_slots() -> None:
    """尾端保留列和起始片回绕后仍绑定到正确的物理开关。"""
    # 高位 H=2，纵向 m=3，子阵列 B7/B8 均为 1。
    result = map_fig21_bridge((2 << 5) | (3 << 2) | 3, 15, 7)
    reserved_slots = {0, 1, 7}
    for slot, mask in enumerate(result.masks.main):
        assert mask == 0b11 | ((3 << 15) if slot in reserved_slots else 0)
    assert result.masks.sub == (0b01100,) * 8
    assert result.correction_q32 == 11842741

    # 保留 13 列时，普通列 14、15、0 中的第 15 列展开为两个 1C。
    regular = map_fig21_bridge(3 << 5, 13, 0)
    assert regular.masks.main == ((1 << 14) | (3 << 15) | 1,) * 8
    assert regular.masks.sub == (0,) * 8
    assert regular.correction_q32 == 0


@pytest.mark.parametrize("args", [(512, 0, 0), (-1, 0, 0), (0, 16, 0), (0, 0, 8)])
def test_bridge_model_rejects_out_of_range_fields(args: tuple[int, int, int]) -> None:
    """软件层拒绝超出 RTL 位宽的字段。"""
    with pytest.raises(ValueError):
        map_fig21_bridge(*args)


def _feedback_charge(masks: list[Fig11Masks], sample_v: np.ndarray) -> np.ndarray:
    # 测试明确声明电容与底板电压，由矩阵参考求反馈电荷。C 的绝对值任取 1 fF。
    main = np.asarray([mask.main for mask in masks], dtype=np.int64)
    sub = np.asarray([mask.sub for mask in masks], dtype=np.int64)
    main_on = (main[..., None] & (1 << np.arange(17))) != 0
    sub_on = (sub[..., None] & (1 << np.arange(5))) != 0
    return ref_ra_charge_nodal(
        main_cap_f=1e-15 * np.array([2] * 15 + [1, 1]),
        sub_cap_f=1e-15 * np.array([8, 4, 2, 1, 1]),
        bridge_cap_f=1e-15,
        sub_parasitic_f=0.0,
        feedback_cap_f=1e-15,
        sample_main_v=np.broadcast_to(sample_v[:, None, None], main_on.shape),
        sample_sub_v=np.full(sub_on.shape, -5.0),
        conversion_main_v=np.where(main_on, 5.0, -5.0),
        conversion_sub_v=np.where(sub_on, 5.0, -5.0),
    ).sum(axis=-1)


# =========================================================================
# 2. 函数组：test_bridge_compensates_two_node_charge_for_all_codes_and_phases
# =========================================================================
# -------------------------------------------------------------------------
# 穷举 65,536 组物理状态，验证正补偿把名义残差恢复至 Fig.11 基准。
# -------------------------------------------------------------------------
def test_bridge_compensates_two_node_charge_for_all_codes_and_phases() -> None:
    """穷举 65,536 组物理状态，验证正补偿把名义残差恢复至 Fig.11 基准。"""
    sample_v = np.linspace(-4.5, 4.5, 512)
    codes = np.arange(512)
    bypass = [map_fig11(code, False, 0, 0) for code in range(512)]
    charge_bypass = _feedback_charge(bypass, sample_v)
    expected_delta = 10e-15 * (4 * ((codes >> 3) & 1) + 2 * ((codes >> 2) & 1)) / 17
    normalization = 256e-15 * 5.0
    expected_q32 = np.array([0, 3947580, 7895160, 11842741])[(codes >> 2) & 3]

    for reserved in range(16):
        for vertical in range(8):
            bridged = [map_fig21_bridge(code, reserved, vertical) for code in range(512)]
            delta = charge_bypass - _feedback_charge([value.masks for value in bridged], sample_v)
            q32 = np.asarray([value.correction_q32 for value in bridged])
            np.testing.assert_array_equal(q32, expected_q32)
            np.testing.assert_allclose(delta, expected_delta, atol=1e-26, rtol=0.0)
            error = np.abs(q32 / (1 << 32) - delta / normalization)
            assert np.max(error) <= (0.5 + 1e-5) / (1 << 32)
