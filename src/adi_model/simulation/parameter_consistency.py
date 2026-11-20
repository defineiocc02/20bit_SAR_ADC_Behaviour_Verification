# =============================================================================
# 文件名     : parameter_consistency.py
# 模块名     : adi_model.simulation.parameter_consistency
# 版本       : v1.0
# 功能       : 把"参数之间必须自洽"的关系做成可读断言，而不是留在若干失败用例里。
#
# 设计说明   :
#   * 本模块比较窗口宽度与假设的残差摆幅。U 项只适用于采样端漏掉高段的压力试算。
#     Fig.19 全字进入 RDAC 时，名义等步长码本的 U 会在余差中抵消。
#   * 此处只算所需跨度。实际是否越过上、下限，由 ADC2 按每笔节点电压标记。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 2026-11-13 初始版本：ADC2 量程 vs dither 幅度的自洽性核算。
# =============================================================================

"""参数自洽性核算（ADC2 量程 vs dither 幅度）。

单位：电压 V，量程与幅度都以"一级 LSB"（LSB9 = 2·v_fs / 512）为共同标尺。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from adi_model.simulation.config import ConfigError


@dataclass(frozen=True)
class Stage2Capacity:
    """二级 ADC 相对一级量化步长的覆盖能力与分辨率。

    Attributes:
        lsb1_v: 一级 LSB [V]（= 2·v_fs/512）。
        gain: 残差放大器名义增益（无量纲）。
        window_v: ADC2 模拟窗口宽度 [V]。
        coverage_lsb1: 窗口折算到一级输入端后能覆盖多少 LSB1。
        n_bits: ADC2 位数（None 表示调用方未声明，此时无法判断分辨率）。
        lsb2_at_input_v: 二级量化步长折到输入端的电压 [V]（n_bits 为 None 时也是 None）。
    """

    lsb1_v: float
    gain: float
    window_v: float
    coverage_lsb1: float
    n_bits: int | None = None
    lsb2_at_input_v: float | None = None

    def headroom_lsb1(self, needed_lsb1: float) -> float:
        """相对给定需求还差多少 LSB1（正数表示够用，负数表示缺口）。"""
        return self.coverage_lsb1 - needed_lsb1


def stage2_capacity(
    *,
    v_fs: float,
    gain: float,
    adc2_v_min: float,
    adc2_v_max: float,
    adc2_n_bits: int | None = None,
) -> Stage2Capacity:
    """核算二级 ADC 能覆盖多少个一级 LSB。

    Args:
        v_fs: 差分输入峰值 [V]（>0）。
        gain: 残差放大器增益（>0）。
        adc2_v_min/adc2_v_max: ADC2 模拟窗口 [V]，必须 max>min。
        adc2_n_bits: 可选 ADC2 位数；给了才会计算二级量化步长。

    Returns:
        Stage2Capacity。

    Raises:
        ConfigError: 任一参数非有限或越界。

    Side effects: 无；纯代数，可直接复算。
    """
    for name, value in (("v_fs", v_fs), ("adc2_v_min", adc2_v_min), ("adc2_v_max", adc2_v_max)):
        if not np.isfinite(value):
            raise ConfigError(f"{name} must be finite")
    if v_fs <= 0:
        raise ConfigError("v_fs must be positive [V]")
    if not np.isfinite(gain) or gain <= 0:
        raise ConfigError("gain must be finite and positive")
    if adc2_v_max <= adc2_v_min:
        raise ConfigError("ADC2 window must have v_max > v_min")
    if adc2_n_bits is not None and (
        isinstance(adc2_n_bits, bool) or not isinstance(adc2_n_bits, int | np.integer)
    ):
        raise ConfigError("adc2_n_bits must be an integer when supplied")
    if adc2_n_bits is not None and adc2_n_bits < 1:
        raise ConfigError("adc2_n_bits must be positive when supplied")
    lsb1 = 2.0 * v_fs / 512.0
    window = float(adc2_v_max - adc2_v_min)
    lsb2_input = None if adc2_n_bits is None else window / (2 ** int(adc2_n_bits) * float(gain))
    return Stage2Capacity(
        lsb1_v=lsb1,
        gain=float(gain),
        window_v=window,
        coverage_lsb1=window / (gain * lsb1),
        n_bits=None if adc2_n_bits is None else int(adc2_n_bits),
        lsb2_at_input_v=lsb2_input,
    )


def dither_requirement_lsb1(*, upper_code_max: int, quantization_margin: float = 0.5) -> float:
    """在假设高段未进入 RDAC 采样时计算最大码移加给定量化余量，单位为 LSB1。

    upper_code_max 为最大未抵消的上段整数码，quantization_margin 为假设的余量。
    例如 U=5、余量 0.5 时得到 5.5。这个反例不描述全字预置的 Fig.19 通路。
    """
    if isinstance(upper_code_max, bool) or not isinstance(upper_code_max, int | np.integer):
        raise ConfigError("upper_code_max must be an integer")
    if upper_code_max < 0:
        raise ConfigError("upper_code_max must be nonnegative")
    if not np.isfinite(quantization_margin) or quantization_margin < 0:
        raise ConfigError("quantization_margin must be finite and nonnegative")
    return float(upper_code_max) + float(quantization_margin)


#: 20 bit 目标下允许的二级量化步长上限（以 LSB20 为单位）。
#: 取 0.5 是保守选择：二级量化误差 ≤ 0.5 LSB20 时不会成为新的误差地板。
LSB20_ALLOWANCE = 0.5


@dataclass(frozen=True)
class ConsistencyReport:
    """一条可读的自洽性结论。

    Attributes:
        capacity: 二级能力（含分辨率，若已声明位数）。
        needed_lsb1: dither 需要的残差余量 [LSB1]。
        sufficient: 覆盖量足够**且**（已声明位数时）分辨率达标。
    """

    capacity: Stage2Capacity
    needed_lsb1: float
    sufficient: bool

    @property
    def resolution_ok(self) -> bool:
        """二级量化步长是否满足 20 bit 目标；未声明位数时返回 True（不做无依据判断）。"""
        if self.capacity.lsb2_at_input_v is None:
            return True
        return self.capacity.lsb2_at_input_v <= LSB20_ALLOWANCE * (2.0 * self.capacity.lsb1_v / 1024.0)

    @property
    def deficit_lsb1(self) -> float:
        """缺口（正数表示不足的量）；够用时为 0。"""
        return max(0.0, self.needed_lsb1 - self.capacity.coverage_lsb1)

    def summary(self) -> str:
        """一句话结论，供日志与报告直接引用。"""
        verdict = "sufficient" if self.sufficient else "insufficient"
        return (
            f"Uncancelled-upper stress only: ADC2 window {self.capacity.coverage_lsb1:.2f} LSB1, "
            f"requested {self.needed_lsb1:.2f} LSB1, span deficit {self.deficit_lsb1:.2f} LSB1; "
            f"span/resolution verdict {verdict}."
        )


def check_dither_fits_stage2(
    *,
    v_fs: float,
    gain: float,
    adc2_v_min: float,
    adc2_v_max: float,
    upper_code_max: int,
    adc2_n_bits: int | None = None,
) -> ConsistencyReport:
    """核算"二级 ADC 能否装下 dither 的提前量"。

    Args:
        v_fs/gain/adc2_v_min/adc2_v_max: 见 `stage2_capacity`。
        upper_code_max: 见 `dither_requirement_lsb1`。
        adc2_n_bits: 可选 ADC2 位数；给出时同时判定二级分辨率。

    Returns:
        ConsistencyReport（含可读 summary）。

    Raises:
        ConfigError: 参数非法（继承自两个子函数）。

    Side effects: 无。
    """
    capacity = stage2_capacity(
        v_fs=v_fs,
        gain=gain,
        adc2_v_min=adc2_v_min,
        adc2_v_max=adc2_v_max,
        adc2_n_bits=adc2_n_bits,
    )
    needed = dither_requirement_lsb1(upper_code_max=upper_code_max)
    report = ConsistencyReport(capacity, needed, capacity.coverage_lsb1 >= needed)
    # 分辨率不达标时同样判为不自洽：窗口够宽但量化太粗，20 bit 目标仍达不到。
    return ConsistencyReport(capacity, needed, report.sufficient and report.resolution_ok)
