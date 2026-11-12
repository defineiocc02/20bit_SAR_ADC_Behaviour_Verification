# =============================================================================
# 文件名     : parameter_consistency.py
# 模块名     : adi_model.simulation.parameter_consistency
# 版本       : v1.0
# 功能       : 把"参数之间必须自洽"的关系做成可读断言，而不是留在若干失败用例里。
#
# 设计说明   :
#   * 起因：dither 相关的 4 个测试长期失败，根因不是某段代码写错，而是
#     **ADC2 的量程与 dither 的幅度不自洽**：ADC2 窗口只能覆盖约 1.2 个一级 LSB，
#     而 dither 的 upper 段最大 5 个 LSB。两者相差 4 倍以上，任何实现组合都救不回来。
#   * 这类"参数互相打不过"的问题，最好的落点是**一条显式断言**：
#     它在构建时就给出可读的结论（谁需要多少、谁只提供多少、差多少），
#     而不是让读者从 4 个失败用例里反推。
#   * 本模块只做**核算与报告**，不修改任何参数、不自动放宽窗口、不关闭 dither。
#     调用方决定是"拒绝运行"还是"带着已知缺口运行并打印"。
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
    """需求 = U 的最大提前量 + 量化余量。"""
    """dither 的 upper 段需要多少一级 LSB 的残差余量。

    Args:
        upper_code_max: dither upper 段的最大码（`U = 4·D6 + D4`，六位字下最大 5）。
        quantization_margin: 一级量化本身需要的余量（满步长对应 0.5，默认）。

    Returns:
        需要的 LSB1 数。

    Raises:
        ConfigError: upper_code_max 非整数或为负。

    说明：U 通过 `k = c - U` 让码提前，残差从 ±0.5 LSB 扩展到 [-0.5, U+0.5] LSB。
    推导：c 的量化误差 ±0.5Δ，k = c - U，残差 = x - vd0(k) ≈ 量化误差 + U·Δ，
    因此 U 是**累积**提前量（不是"二选一"）。
    `U = 4·D6 + D4` 取 {0,1,4,5}，最大值 5 ⇒ 残差上界 5.5 LSB1。
    这条结论与论文 [00] 的 "dither range enhanced by 2b" 一致：
    D6 给出 4 倍的 RDAC 侧范围，D4 是 1 码的量化器侧范围。
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
        if self.sufficient:
            return (
                f"ADC2 covers {self.capacity.coverage_lsb1:.2f} LSB1 "
                f"(needs {self.needed_lsb1:.2f}); dither fits."
            )
        return (
            f"ADC2 covers {self.capacity.coverage_lsb1:.2f} LSB1 but dither needs "
            f"{self.needed_lsb1:.2f}; short by {self.deficit_lsb1:.2f} LSB1. "
            "The second stage window and the dither range are not mutually consistent; "
            "this is a parameter choice, not an implementation defect."
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
