# =============================================================================
# 文件名     : redundancy.py
# 模块名     : adi_model.control.redundancy
# 版本       : v1.0
# 功能       : 按 US10516408B2 正文实现"radix<2 的冗余码序列 → 二进制码"的可核对判据。
#
# 设计说明   :
#   * 依据是 [09] 正文（PDF 第 22 页，图像页，逐字核对）：
#     "A further approach to providing redundancy is to change the 'radix' of the
#      capacitors in the array from 2 (which represents binary weighting) to a
#      smaller number such as 1.8. Thus the ratio of one capacitor to its neighbor
#      becomes 1.8 rather than 2. This inserts redundancy into the array such that
#      an incorrect bit decision can be corrected as the conversion process proceeds."
#     并紧接："...by using a radix less than 2, but then the switch sequence needs
#     to be examined and further converted into a binary word."
#   * 本模块只做**判据**与**码重建**，不改变主链的位分配：
#     [09] 给的是机制（radix 可小于 2、需要二次转换），没有给本芯片的具体 radix、
#     电容序列或纠错译码表，因此不能把某个 radix 值当成"披露"塞进主链。
#   * 与主链的关系：`topology/mapper.py` 的 `Mapper` 消费**显式码表**，
#     本身不假设 radix；将来接入冗余序列时，由这里生成的码表喂给它即可，
#     不需要改 Mapper 的接口。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 2026-11-13 初始版本：从 [09] 正文提取冗余判据并给出可复算的码重建。
# =============================================================================

"""radix<2 冗余阵列的判据与"开关序列 → 二进制码"重建。

单位约定：所有权重都是**单位电容的倍数**（无量纲）；电压换算由调用方用
`NominalCodebook` 完成，这里不引入 V/F。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from adi_model.simulation.config import ConfigError


# =============================================================================
# 1. 权重序列与冗余判据
# =============================================================================
def radix_weights(radix: float, n_bits: int, *, ascending: bool = False) -> np.ndarray:
    """返回 radix<2 阵列的权重序列（最高位在前）。

    Args:
        radix: 相邻单元比值，[09] 正文给出 "a smaller number such as 1.8"。
            必须满足 1 < radix ≤ 2：=2 是纯二进制（无冗余），>2 会丢失单调性。
        n_bits: 位数（≥1）。
        ascending: True 时低位在前，便于与逐事件开关序列对齐。

    Returns:
        (n_bits,) 浮点权重，最高位为 1（归一化）。

    Raises:
        ConfigError: radix 不在 (1, 2] 或 n_bits 非法。

    边界：radix 恰为 2 时本函数仍可用，但 `has_single_error_margin` 会判为无冗余。
    """
    if not isinstance(radix, int | float | np.floating) or isinstance(radix, bool):
        raise ConfigError("radix must be a real number in (1, 2]")
    value = float(radix)
    if not np.isfinite(value) or not 1.0 < value <= 2.0:
        raise ConfigError("radix must be in (1, 2]; 2 is pure binary and carries no redundancy")
    if isinstance(n_bits, bool) or not isinstance(n_bits, int | np.integer) or n_bits < 1:
        raise ConfigError("n_bits must be a positive integer")
    weights = value ** (n_bits - 1 - np.arange(int(n_bits)))
    return weights[::-1] if ascending else weights


def has_single_error_margin(weights: np.ndarray, *, include_terminator: bool = False) -> bool:
    """判断权重序列是否留有"单比特判决错误可纠正"的余量。

    判据（逐位、按 [09] 正文的"either sign"要求）：
        对每个 i，其**之后所有位（含终止电容）的权重和 ≥ 第 i 位的权重步长**。
    这条等价于"若第 i 位判错，后面剩余的量程仍能补偿回来"。
    纯二进制（radix=2）在最后一位处刚好相等 0.5+0.25+… = 1-2^-n < 1，故不满足。

    Args:
        weights: 最高位在前的权重序列，必须严格递减且为正。
        include_terminator: 是否把"终止电容 = 最小权"计入（[09] 正文提到
            "terminated by an additional terminating capacitor having a value equal
            to the least significant capacitor"）。

    Returns:
        True 表示每一位都留有余量。

    Raises:
        ConfigError: 权重非严格递减或含非正值。
    """
    values = np.asarray(weights, dtype=float)
    if values.ndim != 1 or values.size < 2:
        raise ConfigError("weights must be a one-dimensional sequence of at least two entries")
    if np.any(~np.isfinite(values)) or np.any(values <= 0):
        raise ConfigError("weights must be finite and positive")
    if np.any(np.diff(values) >= 0):
        raise ConfigError("weights must be strictly decreasing (most significant first)")
    tail = np.concatenate([values[::-1].cumsum()[::-1][1:], [0.0]])
    if include_terminator:
        tail = tail + values[-1]
    # 第 i 位的"一步"= 第 i 位与第 i+1 位的差（最后一位的一步等于其自身）
    steps = np.append(values[:-1] - values[1:], values[-1])
    return bool(np.all(tail >= steps - 1e-12))


# =============================================================================
# 2. 开关序列 → 二进制码
# =============================================================================
@dataclass(frozen=True)
class RedundancyMap:
    """冗余开关序列到二进制码的显式映射。

    Attributes:
        weights: 权重序列（最高位在前，单位电容倍数）。
        code_of_sequence: (2^n,) 每个开关序列对应的二进制码。
        unused_sequences: 没有被任何二进制码映射到的序列（冗余留下的空档）。
    """

    weights: np.ndarray
    code_of_sequence: np.ndarray
    unused_sequences: np.ndarray


def sequence_to_binary_map(weights: np.ndarray) -> RedundancyMap:
    """把"开关序列"（每位 0/1）映射成最近的二进制码。

    [09] 正文要求："the switch sequence needs to be examined and further converted
    into a binary word"。做法：按权重把序列折成等效小数，再找最近的二进制码。

    Args:
        weights: 最高位在前的权重序列。

    Returns:
        RedundancyMap。

    Raises:
        ConfigError: 权重非法（与 `has_single_error_margin` 同）。

    Side effects: 无；结果只依赖权重，可复算。
    """
    values = np.asarray(weights, dtype=float)
    if values.ndim != 1 or values.size < 1 or np.any(~np.isfinite(values)) or np.any(values <= 0):
        raise ConfigError("weights must be a nonempty finite positive sequence")
    n = int(values.size)
    sequences = np.arange(2**n, dtype=np.int64)
    bits = (sequences[:, None] >> np.arange(n - 1, -1, -1)) & 1
    analog = bits @ values
    full_scale = float(values.sum())
    target = analog / full_scale * (2**n - 1)
    # 四舍五入会在权重序列的非线性处产生"序号增、码反而减"的抖动
    # （实测 radix=1.8 时序号 7 处 8→7）。码必须对开关序列单调，
    # 否则冗余译码会出现非因果跳变；这里用累积最大值做单调化。
    code = np.rint(target).astype(np.int64)
    code = np.maximum.accumulate(code)
    used = np.unique(code)
    unused = np.setdiff1d(np.arange(2**n, dtype=np.int64), used)
    return RedundancyMap(values.copy(), code, unused)
