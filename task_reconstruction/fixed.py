"""独立 Q28 重构参考及片外系数打包。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

Q = 1 << 28
FULL_SCALE_CODE = 1 << 20
WEIGHT_BITS = 24
GAIN_BITS = 26
MIN_GAIN, MAX_GAIN = Q // 16, 3 * Q // 16
SLICE_COUNT, SLOT_COUNT = 18, 8
FEATURE_COUNT = 21
MAIN_FEATURES = (2,) * 15 + (1, 1)
SUB_FEATURES = (8, 4, 2, 1)


@dataclass(frozen=True)
class Coefficients:
    """每片 21 项有效电荷权重和一项输入增益，单位为 Q28。"""

    weights: tuple[tuple[int, ...], ...]
    gains: tuple[int, ...]

    def __post_init__(self) -> None:
        """拒绝不适合硬件地址、格式或动态范围的训练导出。"""
        if len(self.weights) != SLICE_COUNT or len(self.gains) != SLICE_COUNT:
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
    if delta.shape != (SLICE_COUNT, FEATURE_COUNT) or gain.shape != (SLICE_COUNT,):
        raise ValueError("training dimensions changed")
    if not np.isfinite(delta).all() or not np.isfinite(gain).all():
        raise ValueError("training contains non-finite values")
    nominal = np.array([*(v / 256 for v in MAIN_FEATURES), *(v / 4352 for v in SUB_FEATURES)])
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


def sum_charge(
    coef: Coefficients,
    ids: tuple[int, ...],
    main: tuple[int, ...],
    conv: tuple[int, ...],
    sample: tuple[int, ...],
    adc2: int,
) -> tuple[int, int]:
    """独立按稀疏物理片选择累加带符号特征，不复刻 RTL 流水或除法。"""
    if any(len(x) != SLOT_COUNT for x in (ids, main, conv, sample)):
        raise ValueError("eight ordered slots are required")
    if len(set(ids)) != SLOT_COUNT or any(not 0 <= s < SLICE_COUNT for s in ids):
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
    return ((n * FULL_SCALE_CODE) // g) - FULL_SCALE_CODE // 2, False
