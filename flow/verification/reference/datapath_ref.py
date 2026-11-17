# =============================================================================
# 文件名     : datapath_ref.py
# 模块名     : verification.reference.datapath_ref
# 版本       : v1.0
# 功能       : 提供逐位计数、码仓中心和重构的整数参考。
# 设计说明   : ADC2 位数和 Q32 区间由调用方提供。权重项以显式单元列表计算。
#              参考从算术关系独立推导，用于定位取整、符号、饱和或误接信号项。
#              将整数输出编码转成电压的函数供结果解释，位真比较使用整数。
# 变更记录   : 清除已退役的定点验证路线引用。
# =============================================================================
"""数字数据通路的独立算术参考。

比较器编码按位计数，温度计掩码按物理 rank 与请求数量比较。
码仓中心及定点重构的取整、溢出约定见 RTL_ARITHMETIC_CONTRACT.md §3.1–3.5。
"""

from __future__ import annotations

from dataclasses import dataclass

from verification.reference.fixed_point import (
    check_accumulator,
    floor_div,
    round_half_even_shift,
    saturate_signed,
)

WEIGHT_SCALE = 1 << 30   # Q30
VOLTAGE_SCALE = 1 << 32  # Q32
OUTPUT_COUNT = 1 << 20   # 20-bit


# =============================================================================
# 1. 判决编码和码仓中心
# =============================================================================
def sadc_code(cmp_raw: int, n_cmp: int = 511, bits: int = 9) -> int:
    """Balanced population count.

    ``cmp_raw`` bit ``i`` means "input > threshold[i]". A thermometer code with a
    bubble is still just a bit count; the contract does not reject bubbles, so
    neither does this reference.
    """
    if cmp_raw < 0 or cmp_raw >= (1 << n_cmp):
        raise ValueError("cmp_raw has more bits than the declared comparator count")
    return (cmp_raw & ((1 << n_cmp) - 1)).bit_count() & ((1 << bits) - 1)


def thermometer(logical: list[int], count: int) -> list[int]:
    """Return ``[rank < count]`` for every cell.

    Counts above the array extent saturate to all-ones, never wrap.
    """
    return [1 if rank < count else 0 for rank in logical]


def adc2_bin_center(code: int, min_q: int, max_q: int, bits: int) -> tuple[int, bool]:
    """Return the mid-rise bin centre and the saturation flag.

    Contract §3.1. The shift is by ``bits + 1``; the rounding is ties-to-even on
    a possibly negative product. The result saturates into a signed 64-bit field
    and reports whether it had to clip.
    """
    if code < 0 or code >= (1 << bits):
        raise ValueError("adc2 code outside the declared bit width")
    if max_q <= min_q:
        raise ValueError("empty ADC2 range: max_q must exceed min_q")
    product = (2 * code + 1) * (max_q - min_q)
    offset = round_half_even_shift(product, bits + 1)
    return saturate_signed(min_q + offset, 64)


# =============================================================================
# 2. 逐单元权重归约与重构
# =============================================================================
@dataclass(frozen=True)
class ReconTerms:
    """The three weight sums of contract §3.2, kept separate on purpose.

    They are easy to conflate and impossible to debug once conflated:
    ``rails`` carries the switch polarity, ``gain`` is the signal sum, ``total``
    is switch-independent.
    """

    rails_q30: int
    gain_q30: int
    total_q30: int


def recon_terms(
    weights: list[int],
    on: list[int],
    signal_sign: list[int],
    *,
    signal_connected: list[int] | None = None,
) -> ReconTerms:
    """Accumulate rails/gain/total over the active-unit grid.

    Contract §3.2, read verbatim:

        rails = sum(W * s)      # s = 1 - 2*on for ordinary units
        gain  = sum(W * a)      # a = 1 for ordinary units, 0 for sampling-dither units
        total = sum(W)

    The critical distinction is `a` vs `on`:

    * ``on`` is the **conversion switch state** of this sample. It flips ``s``
      between +1 and -1, i.e. it moves charge between the rails.
    * ``a`` is the **signal-connection flag**. Ordinary units are always
      connected (the contract says so explicitly: "普通单位的 a=1"), so ``a`` is 1
      even when ``on`` is 0.

    The signal gain is constant across conversions that use the same connected
    units. Their conversion switches may still change the signed rail term.

    ``signal_connected`` marks the sampling-dither units that carry ``a = 0``.
    Everything not marked there is an ordinary unit with ``a = 1``.
    """
    if not (len(weights) == len(on) == len(signal_sign)):
        raise ValueError("weights/on/signal_sign must cover the same unit list")
    if signal_connected is not None and len(signal_connected) != len(weights):
        raise ValueError("signal_connected must cover the same unit list")
    rails = 0
    gain = 0
    total = 0
    for index, (weight, _selected, sign) in enumerate(zip(weights, on, signal_sign, strict=True)):
        rails += weight * sign
        connected = 1 if signal_connected is None else int(signal_connected[index])
        gain += weight * connected
        total += weight
    return ReconTerms(rails, gain, total)


@dataclass(frozen=True)
class ReconResult:
    """Output word plus the independent status bits."""

    word: int
    clip_low: bool
    clip_high: bool
    acc_ovf: bool


def reconstruct(
    *,
    fine_q: int,
    offset_q: int,
    injection_q: int,
    terms: ReconTerms,
    pipelined: bool = True,
) -> ReconResult:
    """Return the 20-bit output word and the overflow flag.

    Contract §3.3-3.5. ``pipelined=True`` selects the hardware decomposition
    ``floor((shifted >>> 33) / gain)``, which must equal the direct
    ``floor(shifted / (gain * 2**33))``. Both are computed here and compared by
    the caller's self-test so the identity cannot rot silently.
    """
    if terms.gain_q30 <= 0:
        raise ValueError("gain must be positive; gain<=0 is a structural error")
    overflow = False
    try:
        numerator = check_accumulator(
            (fine_q - offset_q) * WEIGHT_SCALE
            - terms.rails_q30 * VOLTAGE_SCALE
            - terms.total_q30 * injection_q,
            "num",
        )
        shifted = check_accumulator(
            (numerator + terms.gain_q30 * VOLTAGE_SCALE) * OUTPUT_COUNT, "shifted"
        )
    except Exception:
        # 仍以无限精度计算钳位后的码字，独立标记中间值超出累加器范围。
        overflow = True
        numerator = (fine_q - offset_q) * WEIGHT_SCALE - terms.rails_q30 * VOLTAGE_SCALE \
            - terms.total_q30 * injection_q
        shifted = (numerator + terms.gain_q30 * VOLTAGE_SCALE) * OUTPUT_COUNT
    if pipelined:
        word = floor_div(shifted >> 33, terms.gain_q30)
    else:
        word = floor_div(shifted, terms.gain_q30 << 33)
    clip_low = word < 0
    clip_high = word > OUTPUT_COUNT - 1
    word = max(0, min(OUTPUT_COUNT - 1, word))
    return ReconResult(word, clip_low, clip_high, overflow)


def decode_output_voltage(word: int, v_fs: float) -> float:
    """Score-only helper: 20-bit offset-binary word to input-referred volts.

    Lives here but is deliberately *not* used by any bit comparison — the RTL
    never produces volts.
    """
    return v_fs * (2.0 * (word + 0.5) / OUTPUT_COUNT - 1.0)
