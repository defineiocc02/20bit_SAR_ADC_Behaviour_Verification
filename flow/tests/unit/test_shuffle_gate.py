"""FIG.19 的 362 选通门与两级 shuffler 判据（US10511316B2 Sheet 17 of 29）。

本轮在 2560×3300 原生图上逐元件核对得到：
  306 UNARY ENCODER 输入 8x/4x/2x（B5/B6/B7 的每片权重）
  352 左 shuffler 输入 "15x2C"（B0-B3 段 30C）+ 标 `0` 方框
  350 右 shuffler 输入 8x/4x/2x + 标 `0` 方框
  362 = 把两路分配结果选通到下方各片 DS1..DS8 的开关阵列
  DS1 内部：2C…2C + **虚线框内 C,C** + 桥接 C（与 FIG.11 的 S4/占位互证）
"""

from __future__ import annotations

import numpy as np
import pytest

from adi_model.control.shuffle_gate import (
    RESERVED_SLOTS_PER_SHUFFLER,
    SHUFFLER_350_INPUTS,
    SHUFFLER_352_INPUT_2C_UNITS,
    gate_is_permutation,
    gate_preserves_weight_sum,
    reserved_slots_per_cycle,
    unary_encoder_excludes_lsb,
    verify_two_stage_allocation,
)
from adi_model.simulation.config import ConfigError


def test_352_carries_the_b0_b3_segment_as_a_fixed_quantity():
    """352 的输入是固定的 15×2C = 30C（图上标 `15x2C`，不是可变输入）。"""
    assert SHUFFLER_352_INPUT_2C_UNITS == 15
    report = verify_two_stage_allocation()
    assert report["shuffler_352_total_c"] == 30
    assert report["shuffler_352_total_c"] == report["main_segment_c"]


def test_350_carries_only_b5_b6_b7():
    """306→350 只喂 B5/B6/B7（8x/4x/2x），B8 不经过 unary encoder。"""
    assert set(SHUFFLER_350_INPUTS) == {"B5", "B6", "B7"}
    assert verify_two_stage_allocation()["shuffler_350_total_c"] == 14
    assert unary_encoder_excludes_lsb() is True


def test_combined_allocation_covers_main_and_sub_segments():
    """两路汇聚后必须覆盖主段 30C 与子阵列可切换 15C 的量纲关系。"""
    report = verify_two_stage_allocation()
    assert report["combined_c"] == 30 + 14
    assert report["sub_switchable_c"] == 15
    # 主段由 352 独占；子阵列的 8/4/2 由 350 承担，1C(B8) 直通
    assert report["unary_encoder_feed_c"] + 1 == report["sub_switchable_c"]


def test_gate_preserves_total_weight_it_only_reroutes():
    """362 是选通门：只改连接，不改被分配的总权重。"""
    before = np.array([2, 2, 2, 1, 8, 4, 2, 1], dtype=float)
    after = before[np.array([3, 1, 0, 2, 7, 5, 6, 4])]
    assert gate_preserves_weight_sum(weights_before=before, weights_after=after) is True


def test_gate_rejects_added_or_dropped_slots():
    """门的输入输出槽数必须相同 —— 否则就是把权重增删了。"""
    with pytest.raises(ConfigError):
        gate_preserves_weight_sum(weights_before=[1, 2, 3], weights_after=[1, 2])


def test_gate_mapping_must_be_a_permutation():
    """362 的开关连接在一个切片内必须是一一对应的置换。"""
    assert gate_is_permutation(mapping=np.arange(16), n_slots=16) is True
    assert gate_is_permutation(mapping=np.array([1, 0, 3, 2]), n_slots=4) is True
    # 重复取同一源 ⇒ 不是置换
    assert gate_is_permutation(mapping=np.array([0, 0, 1, 2]), n_slots=4) is False
    # 越界
    with pytest.raises(ConfigError):
        gate_is_permutation(mapping=np.array([0, 1, 9]), n_slots=3)
    with pytest.raises(ConfigError):
        gate_is_permutation(mapping=np.array([0.5, 1.5]), n_slots=2)


def test_each_shuffler_leaves_one_reserved_slot():
    """352 与 350 各有一个标 `0` 的空位（与 reserved_column 的 16 选 15 同源）。"""
    assert reserved_slots_per_cycle() == RESERVED_SLOTS_PER_SHUFFLER == 1
