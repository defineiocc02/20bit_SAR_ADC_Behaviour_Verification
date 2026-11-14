# =============================================================================
# 文件名     : reserved_column.py
# 模块名     : adi_model.control.reserved_column
# 版本       : v1.0
# 功能       : 按 US10511316B2 FIG.20 实现"保留列 / 哑位"的可核对判据与置换模型。
#
# 设计说明   :
#   * 依据是 FIG.20（Sheet 18 of 29）与 FIG.19（Sheet 17 of 29）的**图面计数**，
#     本轮在 300 dpi 原件上逐档读出：
#       shuffler 350 输入 = {D6:2x, D5:2x, D4:4x, D3:8x, 保留:0}，输出 16x
#       shuffler 352 输入 = {D2:16x, D1:32x, 350 输出:16}，输出 8x2 与 8x30
#     350 的输入权重和 2+2+4+8+0 = 16，恰等于它的输出 16 ⇒ 是"16 选 16"的纯置换；
#     352 的输出 8×2 + 8×30 = 256 = 8 片 × 32C ⇒ 正好一个完整主阵列。
#   * 于是"保留列"的语义是：16 个槽位里常驻一个**不承载数据**的哑位，
#     使每次被 D352 选中的 15 个位置之外总有一个空档，用于吸收 shuffler 的置换。
#   * 本模块只做**判据与置换**，不改变主链位分配：原件没有给出"保留位落在哪个槽"
#     的规则（图上只有一个 `0` 方框，无编号），因此默认实现标 `ASSUMED_ROTATING`。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 2026-11-13 初始版本：从 FIG.20 的计数建立保留列模型与三条不变式判据。
# =============================================================================

"""保留列（哑位）模型与判据。

单位约定：权重为"每片 1x 单元"的倍数（无量纲）；**不改任何电压换算**。

    ⚠️ **本模块不参与主链仿真**：`src/` 内没有任何地方 import 它。
    用途是把专利结构约束写成可核对、可测试的判据；是否接入主链属设计选择，
    需人决定（原件未给出本芯片是否采用该机制）。当前唯一使用者是
    `tests/unit/test_reserved_column.py`。
"""

from __future__ import annotations

import numpy as np

from adi_model.simulation.config import ConfigError


# =============================================================================
# 1. 原件计数常量（来自 FIG.20 图面，不含推断）
# =============================================================================
#: shuffler 350 的输入档位与每片权重；`RESERVED` 是图上那个标 `0` 的方框。
SHUFFLER_350_INPUTS = {"D6": 2, "D5": 2, "D4": 4, "D3": 8, "RESERVED": 0}
#: 350 的输出总量（图上标 `16x`）。
SHUFFLER_350_OUTPUT = 16
#: shuffler 352 的两个数据输入，以及它从 350 接收的那一路。
SHUFFLER_352_INPUTS = {"D2": 16, "D1": 32, "FROM_350": SHUFFLER_350_OUTPUT}
#: 352 的两路输出（图上标 `8x2` 与 `8x30`）。
SHUFFLER_352_OUTPUTS = (8 * 2, 8 * 30)


# =============================================================================
# 2. 计数自洽判据
# =============================================================================
def verify_figure20_counts() -> dict:
    """核对两个 shuffler 的"输入总量 == 输出总量"，返回可打印的明细。

    这是**原件自洽性检查**：若图上某档读错，这里会立刻不等。
    例如把 350 的 `0` 误读成数据位，输入和会变成 16 + w(0)，与输出 16 不符。

    Returns:
        dict：两级的输入和、输出和、是否相等，以及主阵列解释。

    Side effects: 无；纯计算。
    """
    l350 = int(sum(SHUFFLER_350_INPUTS.values()))
    l352 = int(sum(SHUFFLER_352_INPUTS.values()))
    o352 = int(sum(SHUFFLER_352_OUTPUTS))
    return {
        "shuffler_350_input_sum": l350,
        "shuffler_350_output": SHUFFLER_350_OUTPUT,
        "shuffler_350_balanced": l350 == SHUFFLER_350_OUTPUT,
        "shuffler_352_input_sum": l352,
        # 352 的输入里有 16 来自 350，因此净新增是 D2+D1
        "shuffler_352_net_new": l352 - SHUFFLER_350_OUTPUT,
        "shuffler_352_output_sum": o352,
        "shuffler_352_output_is_full_main_array": o352 == 8 * 32,
        "reserved_slot_present": "RESERVED" in SHUFFLER_350_INPUTS,
    }


# =============================================================================
# 3. 保留列置换
# =============================================================================
def reserved_slot_mask(n_slots: int, phase: int) -> np.ndarray:
    """返回本次转换里"哪一个是保留（哑）槽"的布尔掩码。

    Args:
        n_slots: 槽位数（FIG.20 的 350 输出口径为 16）。
        phase: 轮转相位，0 ≤ phase < n_slots。

    Returns:
        (n_slots,) bool，恰有一个 True。

    Raises:
        ConfigError: 槽位数非正或相位越界。

    Side effects: 无。

    说明：原件只画了一个 `0` 方框、**没有给编号**，所以"保留位随相位轮转"是
    本实现的**工程假设**（`ASSUMED_ROTATING`），不是披露。它满足两条可核对性质：
    每周期恰一个哑位、任意两个相位之间哑位不同。
    """
    if isinstance(n_slots, bool) or not isinstance(n_slots, int | np.integer) or n_slots < 1:
        raise ConfigError("n_slots must be a positive integer")
    if isinstance(phase, bool) or not isinstance(phase, int | np.integer):
        raise ConfigError("phase must be an integer")
    if not 0 <= int(phase) < int(n_slots):
        raise ConfigError("phase must be in [0, n_slots)")
    mask = np.zeros(int(n_slots), dtype=bool)
    mask[int(phase)] = True
    return mask


def active_slots(n_slots: int, phase: int) -> np.ndarray:
    """返回本次真正承载数据的槽索引（即"16 选 15"里被选中的那些）。

    Args:
        n_slots: 槽位数。
        phase: 轮转相位。

    Returns:
        (n_slots-1,) 整数索引，升序。

    Raises:
        ConfigError: 参数非法（同 `reserved_slot_mask`）。

    与 FIG.20 的关系：352 输出的 8x2=16 与 8x30=240 合计 256 = 8 片 × 32C，
    说明被送到 DAC 的量仍是完整主阵列；保留位只占**位置**，不占权重。
    """
    mask = reserved_slot_mask(n_slots, phase)
    return np.flatnonzero(~mask)
