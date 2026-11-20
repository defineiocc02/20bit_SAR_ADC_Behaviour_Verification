"""radix<2 冗余判据的单元测试（依据 US10516408B2 [09] 正文第 22 页）。

正文逐字："change the 'radix' of the capacitors in the array from 2 ... to a smaller
number such as 1.8 ... This inserts redundancy into the array such that an incorrect
bit decision can be corrected."
本测试只钉住**可复算的判据**，不把 1.8 当成这颗芯片的披露值。
"""

from __future__ import annotations

import numpy as np
import pytest

from adi_model.control.redundancy import (
    has_single_error_margin,
    radix_weights,
    sequence_to_binary_map,
)
from adi_model.simulation.config import ConfigError


def test_radix_two_is_binary_and_has_no_spare_code():
    """radix=2 是纯二进制：没有重复码，即没有纠错余量。"""
    weights = radix_weights(2.0, 4)
    assert np.allclose(weights, [8, 4, 2, 1])
    report = sequence_to_binary_map(weights)
    assert len(np.unique(report.code_of_sequence)) == 16
    assert report.unused_sequences.size == 0


def test_radix_below_two_creates_redundant_sequences():
    """radix<2 时出现重复码（冗余），数量随 radix 减小而增加。"""
    counts = {}
    for radix in (1.95, 1.9, 1.8, 1.7):
        report = sequence_to_binary_map(radix_weights(radix, 4))
        counts[radix] = 16 - len(np.unique(report.code_of_sequence))
    # 实测值（单调化之后）：radix≥1.9 仍是双射；1.8 起出现 3 个重复码，1.7 有 4 个。
    # 这些数字是"4 位阵列 + 满量程归一"这一具体口径下的结果，换位数会变，
    # 因此断言只用于钉住当前实现，不作为专利结论。
    assert counts[1.95] == 0
    assert counts[1.9] == 0
    assert counts[1.8] == 3
    assert counts[1.7] == 4


def test_error_margin_requires_terminator_or_enough_radix_reduction():
    """单错余量：radix=2 不满足；radix≤1.8 在计入终止电容后满足。"""
    assert has_single_error_margin(radix_weights(2.0, 4)) is False
    assert has_single_error_margin(radix_weights(1.8, 4)) is False
    # [09] 正文："terminated by an additional terminating capacitor having a value
    # equal to the least significant capacitor" —— 计入终止电容后才够。
    assert has_single_error_margin(radix_weights(1.8, 4), include_terminator=True) is True
    assert has_single_error_margin(radix_weights(2.0, 4), include_terminator=True) is True


def test_radix_rejects_values_outside_one_to_two():
    """radix 必须落在 (1,2]：>2 会破坏单调性，=1 会退化成全等权。"""
    for bad in (1.0, 0.9, 2.5, 0.0, -1.0, float("nan"), float("inf")):
        with pytest.raises(ConfigError):
            radix_weights(bad, 4)
    with pytest.raises(ConfigError):
        radix_weights(2.0, 0)
    with pytest.raises(ConfigError):
        radix_weights(True, 4)


def test_sequence_to_binary_is_monotone_in_sequence_index():
    """开关序列按二进制计数递增时，映射出的码必须非降（可逆性前提）。"""
    report = sequence_to_binary_map(radix_weights(1.8, 4))
    assert np.all(np.diff(report.code_of_sequence) >= 0)


def test_weights_must_be_strictly_decreasing():
    """判据入口拒绝非严格递减的权重，避免静默给出错误余量结论。"""
    with pytest.raises(ConfigError):
        has_single_error_margin(np.array([4.0, 4.0, 2.0, 1.0]))
    with pytest.raises(ConfigError):
        has_single_error_margin(np.array([4.0, 0.0, 1.0]))
