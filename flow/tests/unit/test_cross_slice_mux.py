"""FIG.20 跨片分配判据（US10511316B2 Sheet 18 of 29）。

本轮在 2560×3300 原生图上逐元件核对得到的关键语义：
  跨片分配**不是整片交换**，而是**逐主单元**用 2:1 MUX 选来源：
    · 353 SHUFFLER CONTROLLER 产出 `Shuffler Pointer` 与 `mux_sel`
    · 352 的输出（虚线）→ MX 上输入；另一路（实线）→ MX 下输入
    · MX1..MX4 各送一个 2C 单元到 DS1，由 `mux_sel` 选路
"""

from __future__ import annotations

import numpy as np
import pytest

from adi_model.control.cross_slice_mux import (
    FIGURE_MUX_COUNT,
    MUX_INPUTS,
    figure_gate_count,
    mux_selects_from_two_inputs,
    mux_selection,
    per_unit_not_per_slice,
    selection_is_complete,
    shuffle_pointer_advances,
)
from adi_model.simulation.config import ConfigError


def test_each_mux_is_two_to_one():
    """每个 MX 只有两路输入（352 那路 + 实线那路），不是多路。"""
    assert MUX_INPUTS == 2
    assert mux_selects_from_two_inputs(source=[0, 1, 0]) is True


def test_selection_records_upper_and_lower_counts():
    """选择记录能报出上下两路各被选了多少单元。"""
    selection = mux_selection([0, 1, 0, 1, 0, 0, 1])
    assert selection.n_units == 7
    assert selection.count_upper == 4
    assert selection.count_lower == 3
    assert selection.count_upper + selection.count_lower == selection.n_units


def test_allocation_granularity_is_the_unit_not_the_slice():
    """① 分配粒度是单元：同片内不同单元可以有不同来源。"""
    per_unit = np.array([[0, 1, 0, 1], [1, 1, 1, 1]])
    assert per_unit_not_per_slice(per_unit_source=per_unit, slice_size=4) is True
    # 若某实现退化成一整片同源，这条就检测不到逐单元差异
    uniform = np.array([[0, 0, 0, 0], [1, 1, 1, 1]])
    assert per_unit_not_per_slice(per_unit_source=uniform, slice_size=4) is False


def test_selection_must_cover_every_allocated_unit():
    """③ 选择的单元数必须等于该片参与分配的主单元数（15 个 2C）。"""
    selection = mux_selection([0] * 15)
    assert selection_is_complete(selection=selection, expected_units=15) is True
    assert selection_is_complete(selection=selection, expected_units=16) is False


def test_shuffle_pointer_must_advance_every_cycle():
    """④ Shuffler Pointer 每笔前进，否则"轮换"名存实亡。"""
    assert shuffle_pointer_advances(pointers=[0, 1, 2, 3]) is True
    assert shuffle_pointer_advances(pointers=[0, 1, 4, 9]) is True
    assert shuffle_pointer_advances(pointers=[0, 1, 1, 2]) is False
    assert shuffle_pointer_advances(pointers=[3, 2, 1]) is False


def test_figure_counts_are_recorded():
    """图面计数：画出 4 个 MX + 省略号 ⇒ 结构是"每单元一个"，4 只是样例。"""
    report = figure_gate_count()
    assert report["mux_count_in_figure"] == FIGURE_MUX_COUNT == 4
    assert report["per_unit_mux"] is True
    assert report["shuffler_352_input_c"] == 30


def test_rejects_illegal_selection_codes():
    """选择码只能是 0/1，且必须非空一维。"""
    for bad in ([0, 2], [-1, 0], [[0, 1]]):
        with pytest.raises(ConfigError):
            mux_selection(bad)
    with pytest.raises(ConfigError):
        mux_selection([])
    with pytest.raises(ConfigError):
        per_unit_not_per_slice(per_unit_source=np.array([[0, 2]]), slice_size=2)
    with pytest.raises(ConfigError):
        selection_is_complete(selection=mux_selection([0]), expected_units=0)
    with pytest.raises(ConfigError):
        shuffle_pointer_advances(pointers=[0])
    with pytest.raises(ConfigError):
        shuffle_pointer_advances(pointers=[0, -1])
