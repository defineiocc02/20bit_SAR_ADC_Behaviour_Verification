"""两套 Fig.11 掩码实现的差异必须被钉住，不能悄悄漂移。

`control.fig11_dem.map_fig11`（旧，按 slot 旋转、纵向 -v）与
`dem3d.fig11_masks`（新，按物理 ID 旋转、纵向 +v）是同一专利的两个实现。
本文件按**实测结果**断言等价条件与差异，不用"应该不同"代替测量。
"""

from __future__ import annotations

import numpy as np
import pytest
from tests.unit.test_dem3d import _fig11_pool

from adi_model.control.dem3d import Dem3State, fig11_masks
from adi_model.control.fig11_dem import map_fig11


def _new_masks(pool, code: int, horizontal: int, vertical: int) -> np.ndarray:
    state = Dem3State(horizontal, vertical).to_int()
    ids = np.broadcast_to(np.arange(8), (1, 8))
    command = fig11_masks(pool.geometry, np.array([code]), np.array([state]), ids)
    mask = np.asarray(command.main_on)[0]
    return np.array([sum(1 << bit for bit in range(17) if mask[slot, bit]) for slot in range(8)])


@pytest.mark.parametrize("code", [0, 1, 16, 31, 32, 255, 511])
def test_identical_when_slot_equals_physical_id_and_v_is_zero(code):
    """slot==物理 ID 且纵向为 0 时，两套实现逐位相同（实测成立）。"""
    pool = _fig11_pool()
    old = np.array(map_fig11(code, True, 0, 0).main)
    assert np.array_equal(old, _new_masks(pool, code, 0, 0))


@pytest.mark.parametrize("code", [16, 48, 255, 511])
def test_horizontal_rotation_agrees_at_nonzero_phase(code):
    """横向非零相位也相同——因为测试里 slot 与物理 ID 一致；差异只在真实调度里出现。"""
    pool = _fig11_pool()
    old = np.array(map_fig11(code, True, 3, 0).main)
    assert np.array_equal(old, _new_masks(pool, code, 3, 0))


def test_vertical_sign_is_a_real_difference():
    """纵向：旧 `-v`、新 `+v`。v=3、b4=1 时实测不同——这是记录在案的缺口。"""
    pool = _fig11_pool()
    old = np.array(map_fig11(16, True, 0, 3).main)
    new = _new_masks(pool, 16, 0, 3)
    assert not np.array_equal(old, new)
    # 但两者 1C 段都只能选 0..2 个（B4 的 8/16 规则）
    for masks in (old, new):
        one_c = [bin(int(value) >> 15).count("1") for value in masks]
        assert all(0 <= count <= 2 for count in one_c)


def test_reserved_phase_is_legal_and_equals_phase_zero():
    """相位 15 命中保留列：合法、被标记，且 2C 有源集合与相位 0 相同。"""
    pool = _fig11_pool()
    # 用码 511：therm_count = 511>>5 = 15，2C 段全选，便于检查"15 列全在"
    old = map_fig11(511, True, 15, 0)
    assert old.valid and old.reserved_column_hit
    assert np.array_equal(np.array(old.main), np.array(map_fig11(511, True, 0, 0).main))
    new = _new_masks(pool, 511, 15, 0)
    assert np.array_equal(new, _new_masks(pool, 511, 0, 0))
    assert all(bin(int(value) & 0x7FFF).count("1") == 15 for value in new)
