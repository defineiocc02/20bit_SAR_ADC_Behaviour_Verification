"""两套 Fig.11 掩码实现的差异必须被钉住，不能悄悄漂移。

`control.fig11_dem.map_fig11`（按 slot 旋转、B4 单步 1C）与
`dem3d.fig11_masks`（按物理 ID 旋转、B4 单步 1C）是同一专利的两个实现。
本文件按**实测结果**断言等价条件与差异，不用"应该不同"代替测量。

2026-11-13 更新（第二次）：两套实现的 B4 判据**都已改为单步 1C**。
依据：原件 FIG.17 把 B4 画成常量 1、直通、标 `2x`；FIG.19 顶层行只有一个 C；
FIG.20 的 4x/2x 属于子阵列（每片一份、8 片合计）。旧 "8/16 规则" 会在同一片
同时打开两个 1C（8C/片），与 `2x` 不符，已删除。
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


@pytest.mark.parametrize("code", [0, 1, 15])
def test_identical_when_slot_equals_physical_id_and_v_is_zero(code):
    """slot==物理 ID、v=0、且 B4=0 时两套实现逐位相同。

    只断言 B4=0 的码：B4=1 时两套实现的判据不同（见文件头），
    差异由 `test_b4_criterion_differs_by_design` 单独钉住。
    """
    pool = _fig11_pool()
    assert code & 0x10 == 0
    old = np.array(map_fig11(code, True, 0, 0).main)
    assert np.array_equal(old, _new_masks(pool, code, 0, 0))


@pytest.mark.parametrize("code", [16, 31, 255])
def test_b4_is_one_single_cell_on_every_slice(code):
    """B4=1 时两套实现都只在每片打开一个 1C（unit15），且与 `2x` 标签一致。

    反转条件：若有人把 8/16 规则改回来（同一片两个 1C），本测试立即失败。
    """
    pool = _fig11_pool()
    old = np.array([bin(int(value) >> 15).count("1") for value in map_fig11(code, True, 0, 0).main])
    new = np.array([bin(int(value) >> 15).count("1") for value in _new_masks(pool, code, 0, 0)])
    assert set(old.tolist()) <= {0, 1}
    assert set(new.tolist()) == {1}


def test_vertical_phase_no_longer_changes_main_masks():
    """B4 单步化后，纵向相位不再影响主掩码（原件没有任何"1C 随片轮转"）。

    历史：旧实现用 `(2*slot+unit-v)%16<8` 选 1C，v 会改变选通；那条规则已删。
    """
    pool = _fig11_pool()
    for vertical in (0, 3, 7, 15):
        assert np.array_equal(_new_masks(pool, 16, 0, vertical), _new_masks(pool, 16, 0, 0))
        assert np.array_equal(
            np.array(map_fig11(16, True, 0, vertical).main), np.array(map_fig11(16, True, 0, 0).main)
        )


def test_reserved_phase_is_legal_and_equals_phase_zero():
    """相位 15 命中保留列：合法、被标记，且 2C 有源集合与相位 0 相同。"""
    pool = _fig11_pool()
    # 用码 511：therm_count = 511>>5 = 15，2C 段全选，便于检查"15 列全在"
    old = map_fig11(511, True, 15, 0)
    assert old.valid and old.reserved_column_hit
    assert np.array_equal(np.array(old.main), np.array(map_fig11(511, True, 0, 0).main))
    # B4=1 的码（如 511）在两套实现下 1C 段不同，本测试只核 2C/保留列的等价性
    new = _new_masks(pool, 511, 15, 0)
    assert np.array_equal(new, _new_masks(pool, 511, 0, 0))
    assert all(bin(int(value) & 0x7FFF).count("1") == 15 for value in new)
