"""Fig.11 掩码的**总权重不变量**：主码 m 的选中权重必须恰好等于 m（单位 C）。

## 为什么需要这个测试

2026-11-13 做变异测试发现：把 `dem3d` 里构造主掩码的
`main[: m // 2] = True` 改成 `main[: (m + 1) // 2] = True`，
**1775 个测试全部通过**——没有一条能抓到。

该变异在奇数主码时真的会改变掩码：

| m | 正常 | 变异 |
|:---:|:---|:---|
| 3 | 1 个 2C + B4 = **3C** | 2 个 2C + B4 = **5C** |
| 15 | 7 个 2C + B4 = **15C** | 8 个 2C + B4 = **17C** |

原因是既有测试多在"某些码"上抽查，而权重守恒这条最基本的不变量
从未被逐码验证过。

## 不变量

主阵列 17 列 = 15×2C + B4(1C) + S4(1C)。对任意 9 位码 c：
    选中权重 = 2·(#选中的 2C 列) + (B4 选中 ? 1 : 0) == c >> 4
且 S4（列 16）**恒不选中**（它是哑位）。

这与码本律 `v(c) = -v_fs + 2·v_fs·drive/cn` 直接对应：
`drive = c>>4 + (c&15)/16`，其中 `c>>4` 就是上面这个主段权重。
"""

from __future__ import annotations

import numpy as np
import pytest

from adi_model.control.dem import SplitSwitchGeometry
from adi_model.control.dem3d import fig11_masks

N_MAIN = 17
RING = 15          # 构成相位环的 2C 列
B4_COLUMN = 15
S4_COLUMN = 16
N_ACTIVE = 8


@pytest.fixture(scope="module")
def geometry():
    return SplitSwitchGeometry(N_MAIN, 5, 18, 8, 0, 511)


def _main_weights(geometry, codes, state=0):
    """逐码返回主阵列的选中权重（单位 C）与 S4 是否被选中。"""
    slice_ids = np.broadcast_to(np.arange(N_ACTIVE), (len(codes), N_ACTIVE))
    command = fig11_masks(
        geometry, np.asarray(codes, dtype=np.int64), np.full(len(codes), state, np.int64), slice_ids
    )
    masks = np.asarray(command.main_on)
    # 取第 0 个活跃片（相位 = 该片 ID，逐码相同，因此不变量与相位无关）
    slot0 = masks[:, 0, :]
    two_c = np.count_nonzero(slot0[:, :RING], axis=1)
    b4 = slot0[:, B4_COLUMN].astype(int)
    s4 = slot0[:, S4_COLUMN]
    return 2 * two_c + b4, s4


def test_main_weight_equals_upper_code_bits_for_every_code(geometry):
    """对全部 512 个码，主段选中权重必须恰好等于 `code >> 4`。"""
    codes = np.arange(512)
    weight, _ = _main_weights(geometry, codes)
    expected = codes >> 4
    mismatches = np.flatnonzero(weight != expected)
    assert mismatches.size == 0, (
        f"{mismatches.size} of 512 codes violate the invariant "
        f"(weight must equal code>>4); first offenders: "
        + ", ".join(
            f"code {int(c)} -> weight {int(weight[c])} (expected {int(expected[c])})"
            for c in mismatches[:5]
        )
    )


def test_s4_column_is_never_selected(geometry):
    """S4（列 16）在编码里是哑位，任何码、任何相位都不应被选中。"""
    for state in (0, 1, 3, 7):
        _, s4 = _main_weights(geometry, np.arange(512), state=state)
        assert not np.any(s4), f"S4 column selected for some code at state={state}"


def test_weight_invariant_holds_under_every_realizable_phase(geometry):
    """相位 = 物理片 ID % 15 只平移选中列，不改变**权重和**。"""
    codes = np.arange(0, 512, 17)  # 抽样，覆盖各主码
    for state in (0, 5):
        reference, _ = _main_weights(geometry, codes, state=state)
        slice_ids = np.broadcast_to(np.arange(N_ACTIVE), (len(codes), N_ACTIVE))
        command = fig11_masks(
            geometry,
            np.asarray(codes, dtype=np.int64),
            np.full(len(codes), state, np.int64),
            slice_ids,
        )
        masks = np.asarray(command.main_on)
        for slot in range(N_ACTIVE):
            two_c = np.count_nonzero(masks[:, slot, :RING], axis=1)
            b4 = masks[:, slot, B4_COLUMN].astype(int)
            weight = 2 * two_c + b4
            assert np.array_equal(weight, reference), (
                f"slice slot {slot} (phase {slice_ids[0, slot] % RING}) changed the weight sum "
                f"at state={state}"
            )
