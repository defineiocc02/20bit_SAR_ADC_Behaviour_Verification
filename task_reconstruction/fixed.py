# =============================================================================
# 文件名     : fixed.py
# 模块名     : fixed
# 版本       : v1.0
# 功能       : 独立 Q28 重构参考及片外系数打包。
#
# 设计说明   :
#   * 承担 片上冻结 LMS 系数重构 中的一环；输入输出的物理口径与单位以同目录 SPEC/README 为准。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# =============================================================================

"""独立 Q28 重构参考及片外系数打包。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from adi_model.capacitors import (
    GROUP_SLICES,
    MAIN_WEIGHTS,
    POOL_SLICES,
    SUB_FEATURES,
)
from adi_model.codes import OUTPUT_CODES, to_signed

Q = 1 << 28
# 满幅码数与 20 位输出位宽同源，见 adi_model.codes。
FULL_SCALE_CODE = OUTPUT_CODES
WEIGHT_BITS = 24
GAIN_BITS = 26
MIN_GAIN, MAX_GAIN = Q // 16, 3 * Q // 16
# 片数与电容权重不在此处重复定义：见 adi_model.capacitors。
# FEATURE_COUNT 是每片可切换电容项数（17 主 + 4 子 = 21），由权重表长度推出。
# 增益单独占一个字段，不计入特征维度；把它并进来会让 396 配置字每片多一项而错位。
FEATURE_COUNT = len(MAIN_WEIGHTS) + len(SUB_FEATURES)


# =========================================================================
# 1. 函数组：Coefficients / from_training / decode_midpoint
# =========================================================================
# -------------------------------------------------------------------------
# 每片 21 项有效电荷权重和一项输入增益，单位为 Q28。
# 从归档的 LMS 差量与增益生成一次装载，不读取真实失配参数。
# 用整数有理舍入获得 ADC2 原码中点，分母为奇数所以没有半格 tie。
# -------------------------------------------------------------------------
@dataclass(frozen=True)
class Coefficients:
    """每片 21 项有效电荷权重和一项输入增益，单位为 Q28。"""

    weights: tuple[tuple[int, ...], ...]
    gains: tuple[int, ...]

    def __post_init__(self) -> None:
        """拒绝不适合硬件地址、格式或动态范围的训练导出。"""
        if len(self.weights) != POOL_SLICES or len(self.gains) != POOL_SLICES:
            raise ValueError("expected 18 physical slices")
        for row in self.weights:
            if len(row) != FEATURE_COUNT or any(
                not -(1 << (WEIGHT_BITS - 1)) <= value < (1 << (WEIGHT_BITS - 1)) for value in row
            ):
                raise ValueError("expected 21 signed 24-bit weight coefficients per slice")
        if any(not MIN_GAIN <= value <= MAX_GAIN for value in self.gains):
            raise ValueError("gain is outside the validated hardware range")

    def words(self) -> tuple[int, ...]:
        """输出 396 个配置字，signed24 权重扩展到 26 位。"""
        return tuple(
            value & ((1 << GAIN_BITS) - 1)
            for row, gain in zip(self.weights, self.gains, strict=True)
            for value in (*row, gain)
        )


def from_training(path: Path, group: str = "fig21_dem") -> Coefficients:
    """从归档的 LMS 差量与增益生成一次装载，不读取真实失配参数。"""
    with np.load(path) as data:
        delta = data[f"{group}_shared_frozen_weight_v"]
        gain = data["frozen_gain"]
    if delta.shape != (POOL_SLICES, FEATURE_COUNT) or gain.shape != (POOL_SLICES,):
        raise ValueError("training dimensions changed")
    if not np.isfinite(delta).all() or not np.isfinite(gain).all():
        raise ValueError("training contains non-finite values")
    # 归一化分母 256 与 4352 分别是主/子阵列满码的电容计数（32C×8 与 17C×256），
    # 与权重表同为物理量，不能各自维护。
    nominal = np.array([*(v / 256 for v in MAIN_WEIGHTS), *(v / 4352 for v in SUB_FEATURES)])
    total = np.rint((delta / 10 + nominal) * Q)
    return Coefficients(
        tuple(tuple(map(int, row)) for row in total), tuple(map(int, np.rint(gain * Q)))
    )


def decode_midpoint(code: int) -> int:
    """用整数有理舍入获得 ADC2 原码中点，分母为奇数所以没有半格 tie。"""
    if not 0 <= code < 4096:
        raise ValueError("ADC2 code is not 12-bit offset-binary")
    n = (2 * code + 1 - 4096) * 16384
    q, r = divmod(abs(n), 25)
    magnitude = q + (2 * r > 25)
    return -magnitude if n < 0 else magnitude


# =========================================================================
# 2. 函数组：sum_charge / reconstruct
# =========================================================================
# -------------------------------------------------------------------------
# 独立按稀疏物理片选择累加带符号特征，不复刻 RTL 流水或除法。
# 返回有符号码和错误标记，坏样本的码固定为零。
# -------------------------------------------------------------------------
def sum_charge(
    coef: Coefficients,
    ids: tuple[int, ...],
    main: tuple[int, ...],
    conv: tuple[int, ...],
    sample: tuple[int, ...],
    adc2: int,
) -> tuple[int, int]:
    """独立按稀疏物理片选择累加带符号特征，不复刻 RTL 流水或除法。"""
    if any(len(x) != GROUP_SLICES for x in (ids, main, conv, sample)):
        raise ValueError("eight ordered slots are required")
    if len(set(ids)) != GROUP_SLICES or any(not 0 <= s < POOL_SLICES for s in ids):
        raise ValueError("physical slice IDs must be valid and unique")
    if any(not 0 <= m < 1 << 17 for m in main) or any(not 0 <= m < 16 for m in (*conv, *sample)):
        raise ValueError("illegal main/sub mask or nonzero dummy")
    n = decode_midpoint(adc2)
    for s, m, c, a in zip(ids, main, conv, sample, strict=True):
        n += sum(w for i, w in enumerate(coef.weights[s][:17]) if (m >> i) & 1)
        n += sum(w for i, w in enumerate(coef.weights[s][17:]) if (c >> i) & 1)
        n -= sum(w for i, w in enumerate(coef.weights[s][17:]) if (a >> i) & 1)
    return n, sum(coef.gains[s] for s in ids)


def reconstruct(
    coef: Coefficients,
    ids: tuple[int, ...],
    main: tuple[int, ...],
    conv: tuple[int, ...],
    sample: tuple[int, ...],
    adc2: int,
    bad: bool = False,
) -> tuple[int, bool]:
    """返回有符号码和错误标记，坏样本的码固定为零。"""
    if bad:
        return 0, True
    try:
        n, g = sum_charge(coef, ids, main, conv, sample, adc2)
    except ValueError:
        return 0, True
    if not 0 <= n < g:
        return 0, True
    return to_signed((n * FULL_SCALE_CODE) // g), False
