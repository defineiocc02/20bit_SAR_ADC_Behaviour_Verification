# =============================================================================
# 文件名     : test_fig11_dem.py
# 模块名     : test_fig11_dem
# 版本       : v1.0
# 功能       : Fig.11 映射的名义守恒、位序和独立两节点电荷验证。
#
# 设计说明   :
#   * 承担 单元与独立电荷参考测试 中的一环；输入输出的物理口径与单位以同目录 SPEC/README 为准。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# =============================================================================

"""Fig.11 映射的名义守恒、位序和独立两节点电荷验证。"""

import numpy as np
import pytest

from adi_model.charge_ref_task import ref_ra_charge_nodal
from adi_model.control.fig11_dem import Fig11Masks, map_fig11


# =========================================================================
# 1. 函数组：test_masks_use_physical_bit_order / test_model_rejects_fields_that_do_not_fit_rtl / test_every_code_and_phase_conserves_selected_capacitance
# =========================================================================
# -------------------------------------------------------------------------
# 两种轮转跨尾端后仍对应预期物理位。
# 超出端口位宽的软件输入必须报错，不能静默截断。
# 穷举全部码和合法相位，检查每个节点的选通电容。
# -------------------------------------------------------------------------
def test_masks_use_physical_bit_order() -> None:
    """两种轮转跨尾端后仍对应预期物理位。"""
    # B0～B3=3，B4=1，B5～B8=1010。两条轮转均跨尾端。
    code = (3 << 5) | 0b11010
    rotated = map_fig11(code, True, 14, 15)
    assert rotated.valid
    assert rotated.main[0] == (1 << 14) | 0b11 | (0b11 << 15)
    assert rotated.main[1] == 0b111 | (0b11 << 15)
    assert rotated.main[3] == 0b11100 | (1 << 15)
    assert rotated.main[7] == (0b111 << 6) | (1 << 16)
    assert rotated.sub == (0b0101,) * 8
    assert rotated.main_flat & 0x1FFFF == rotated.main[0]
    assert rotated.main_flat >> (7 * 17) == rotated.main[7]
    assert rotated.sub_flat & 0x1F == rotated.sub[0]
    assert rotated.sub_flat >> (7 * 5) == rotated.sub[7]

    bypass = map_fig11(code, False, 15, 15)
    assert bypass.valid
    assert bypass.main == (0b111 | (1 << 15),) * 8
    assert bypass.sub == rotated.sub
    # 相位 15 现在指向第 16 个"保留列"（哑列，无开关位）：合法，但标记命中，
    # 且其有源集合与相位 0 相同。旧契约（返回全零、valid=False）已废弃。
    reserved = map_fig11(code, True, 15, 15)
    assert reserved.valid and reserved.reserved_column_hit
    assert reserved.main == map_fig11(code, True, 0, 15).main
    assert reserved.sub == map_fig11(code, True, 0, 15).sub


@pytest.mark.parametrize(
    "args",
    [(512, True, 0, 0), (-1, True, 0, 0), (0, 2, 0, 0), (0, True, 16, 0), (0, True, 0, -1)],
)
def test_model_rejects_fields_that_do_not_fit_rtl(args: tuple[int, ...]) -> None:
    """超出端口位宽的软件输入必须报错，不能静默截断。"""
    with pytest.raises(ValueError):
        map_fig11(*args)


def test_every_code_and_phase_conserves_selected_capacitance() -> None:
    """穷举全部码和合法相位，检查每个节点的选通电容。"""
    for code in range(512):
        bypass = map_fig11(code, False, 0, 0)
        expected_high = code >> 5
        expected_ones = 8 * ((code >> 4) & 1)
        for horizontal in range(15):
            for vertical in range(16):
                result = map_fig11(code, True, horizontal, vertical)
                assert result.valid
                assert all((mask & 0x7FFF).bit_count() == expected_high for mask in result.main)
                assert sum((mask >> 15).bit_count() for mask in result.main) == expected_ones
                assert result.sub == bypass.sub


# =========================================================================
# 2. 函数组：_group_feedback_charge / test_two_node_feedback_charge_matches_bypass / test_moving_b5_to_main_changes_charge_with_figure11_bridge
# =========================================================================
# -------------------------------------------------------------------------
# 以同一组实际底板电压独立求解反馈电荷。
# 桥接衰减为 1/17 时禁止将 B5 直接改接主节点。
# -------------------------------------------------------------------------
def _group_feedback_charge(masks: list[Fig11Masks], sample_v: np.ndarray) -> np.ndarray:
    # 测试独立声明电路，再用库中的矩阵节点求解器按底板电压求电荷。
    unit_cap_f = 1e-15
    low_v = -0.5
    high_v = 0.5
    main = np.asarray([mask.main for mask in masks], dtype=np.int64)
    sub = np.asarray([mask.sub for mask in masks], dtype=np.int64)
    main_on = (main[..., None] & (1 << np.arange(17))) != 0
    sub_on = (sub[..., None] & (1 << np.arange(5))) != 0
    return ref_ra_charge_nodal(
        main_cap_f=unit_cap_f * np.array([2] * 15 + [1, 1]),
        sub_cap_f=unit_cap_f * np.array([8, 4, 2, 1, 1]),
        bridge_cap_f=unit_cap_f,
        sub_parasitic_f=0.0,
        feedback_cap_f=unit_cap_f,
        sample_main_v=np.broadcast_to(sample_v[:, None, None], main_on.shape),
        sample_sub_v=np.full(sub_on.shape, low_v),
        conversion_main_v=np.where(main_on, high_v, low_v),
        conversion_sub_v=np.where(sub_on, high_v, low_v),
    ).sum(axis=-1)


@pytest.mark.parametrize("horizontal, vertical", [(0, 0), (14, 15), (7, 8)])
def test_two_node_feedback_charge_matches_bypass(horizontal: int, vertical: int) -> None:
    """以同一组实际底板电压独立求解反馈电荷。"""
    sample_v = np.linspace(-0.4, 0.4, 512)
    bypass = [map_fig11(code, False, 0, 0) for code in range(512)]
    rotated = [map_fig11(code, True, horizontal, vertical) for code in range(512)]
    np.testing.assert_allclose(
        _group_feedback_charge(rotated, sample_v),
        _group_feedback_charge(bypass, sample_v),
        atol=1e-26,
        rtol=0.0,
    )


def test_moving_b5_to_main_changes_charge_with_figure11_bridge() -> None:
    """桥接衰减为 1/17 时禁止将 B5 直接改接主节点。"""
    # B5=1 若搬到主节点的两个 2C 等效位置，主、子节点不再守恒。
    bypass = map_fig11(1 << 3, False, 0, 0)
    moved = Fig11Masks((3 << 15, 3 << 15) + (0,) * 6, (0,) * 8, True)
    charges = _group_feedback_charge([bypass, moved], np.zeros(2))
    assert charges[1] - charges[0] == pytest.approx(-4e-15 / 17, abs=1e-27)
