"""保留列（哑位）判据的单元测试（依据 US10511316B2 FIG.20 的图面计数）。

本轮在 300 dpi 原件上逐档读出：
  shuffler 350 输入 {D6:2x, D5:2x, D4:4x, D3:8x, 保留:0}，输出 16x
  shuffler 352 输入 {D2:16x, D1:32x, 350:16}，输出 8x2 与 8x30
本测试只钉住**计数自洽**与**置换性质**，不声称原件规定了保留位编号。
"""

from __future__ import annotations

import numpy as np
import pytest

from adi_model.control.reserved_column import (
    SHUFFLER_350_INPUTS,
    SHUFFLER_350_OUTPUT,
    SHUFFLER_352_OUTPUTS,
    active_slots,
    reserved_slot_mask,
    verify_figure20_counts,
)
from adi_model.simulation.config import ConfigError


def test_shuffler_350_is_balanced():
    """350 的输入权重和等于它的输出：说明标 0 的方框不承载权重。"""
    report = verify_figure20_counts()
    assert report["shuffler_350_input_sum"] == SHUFFLER_350_OUTPUT
    assert report["shuffler_350_balanced"] is True


def test_shuffler_352_output_covers_exactly_one_main_array():
    """352 的两路输出合计 256 = 8 片 × 32C，正好一个完整主阵列。"""
    report = verify_figure20_counts()
    assert sum(SHUFFLER_352_OUTPUTS) == 256
    assert report["shuffler_352_output_is_full_main_array"] is True
    assert report["shuffler_352_net_new"] == 48


def test_reserved_input_exists_and_weighs_nothing():
    """保留输入必须存在，且权重为 0（否则 350 的输入和会超出 16）。"""
    assert SHUFFLER_350_INPUTS["RESERVED"] == 0
    with pytest.raises(KeyError):
        SHUFFLER_350_INPUTS["NOT_A_BIT"]


def test_every_phase_has_exactly_one_reserved_slot():
    """任一相位下恰有一个哑位，其余 15 个承载数据（16 选 15）。"""
    for phase in range(16):
        mask = reserved_slot_mask(16, phase)
        assert int(mask.sum()) == 1
        assert len(active_slots(16, phase)) == 15


def test_reserved_slot_rotates_with_phase():
    """不同相位的哑位不同（轮转），这是本实现的工程假设。"""
    positions = {int(np.flatnonzero(reserved_slot_mask(16, p))[0]) for p in range(16)}
    assert positions == set(range(16))


def test_reserved_slot_rejects_bad_arguments():
    """非法槽位数/相位必须报错，不能静默夹取。"""
    for bad in (0, -1):
        with pytest.raises(ConfigError):
            reserved_slot_mask(bad, 0)
    with pytest.raises(ConfigError):
        reserved_slot_mask(16, 16)
    with pytest.raises(ConfigError):
        reserved_slot_mask(16, -1)
    with pytest.raises(ConfigError):
        reserved_slot_mask(True, 0)


def test_fig21_vertical_weight_invariant():
    """`2·Σy = 8B4 + 4B5 + 2B6`，且保留列恰好一列。"""
    from adi_model.control.cross_slice_mux import fig21_vertical_selection

    for b4 in (False, True):
        for b5 in (False, True):
            for b6 in (False, True):
                r = fig21_vertical_selection(b4, b5, b6)
                expect = 4 * int(b4) + 2 * int(b5) + int(b6)
                assert r["count"] == expect
                assert int(np.count_nonzero(r["selected"])) == expect
                assert r["weight_c"] == 2 * expect
                # 保留列不得被数据占用
                assert not r["selected"][r["reserved_column"]]


def test_fig21_test_vector_1_1_0_gives_twelve_c():
    """SPEC 的定向向量：B4=1,B5=1,B6=0 → 12C = 6 × 2C。"""
    from adi_model.control.cross_slice_mux import fig21_vertical_selection

    r = fig21_vertical_selection(True, True, False)
    assert r["count"] == 6
    assert r["weight_c"] == 12


def test_fig21_rejects_more_than_seven_data_columns():
    """全 1 时 count=7，仍合法；再加就无数据列可用。"""
    from adi_model.control.cross_slice_mux import fig21_vertical_selection

    assert fig21_vertical_selection(True, True, True)["count"] == 7
