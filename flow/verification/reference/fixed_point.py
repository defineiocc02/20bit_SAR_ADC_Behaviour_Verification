# =============================================================================
# 文件名     : fixed_point.py
# 模块名     : verification.reference.fixed_point
# 版本       : v1.0
# 功能       : 提供带合法域检查的整数舍入、除法和饱和。
# 设计说明   : 先以 Python 无界整数运算，遇到明确规定的位宽再检查或饱和。
#              负数商、高位溢出和半 LSB 平局各有单独区间，便于逐项构造用例。
# 变更记录   : 清除已退役的模块对拍报告引用。
# =============================================================================
"""定点重构的整数原语。

舍入、除法和累加器定义见 docs/audit/components/RTL_ARITHMETIC_CONTRACT.md §3.1、§3.5 和 §4。
"""

from __future__ import annotations

ACC_LEGAL_MIN = -(2**95)
ACC_LEGAL_MAX_EXCLUSIVE = 2**95


class FixedPointError(ValueError):
    """Raised when an operation leaves the contract's legal integer domain."""


# =============================================================================
# 1. 保留符号的舍入与除法
# =============================================================================
def round_half_even_shift(value: int, shift: int) -> int:
    """Return ``value / 2**shift`` rounded half-to-even.

    Contract §3.1. ``shift`` must be positive. Works for negative ``value``
    because Python's ``>>`` already floors, which is exactly the contract's
    ``q = floor(x / 2**k)``.
    """
    if not isinstance(shift, int) or shift <= 0:
        raise FixedPointError("round_half_even_shift needs a positive integer shift")
    quotient = value >> shift
    remainder = value - (quotient << shift)
    half = 1 << shift
    twice = remainder << 1
    if twice > half or (twice == half and (quotient & 1) == 1):
        return quotient + 1
    return quotient


def floor_div(numerator: int, denominator: int) -> int:
    """Return ``floor(numerator / denominator)``, including negative numerators.

    Contract §3.5. Python's ``//`` is already floor, so this wrapper exists to
    make the intent explicit at every call site and to reject ``denominator <= 0``
    (which the contract treats as a structural error, not an overflow).
    """
    if not isinstance(denominator, int) or denominator <= 0:
        raise FixedPointError("floor_div needs a strictly positive denominator")
    return numerator // denominator


def truncate_div(numerator: int, denominator: int) -> int:
    """Return C-style truncation toward zero. Only used to *show* the difference."""
    quotient = abs(numerator) // abs(denominator)
    return -quotient if (numerator < 0) != (denominator < 0) else quotient


# =============================================================================
# 2. 累加器合法域与输出钳位
# =============================================================================
def check_accumulator(value: int, name: str = "operand") -> int:
    """Raise unless ``value`` is inside the half-open accumulator range.

    Contract §4.2: legal is ``-2**95 <= value < 2**95``; ``+2**95`` is out.
    """
    if not (ACC_LEGAL_MIN <= value < ACC_LEGAL_MAX_EXCLUSIVE):
        raise FixedPointError(f"{name} outside the 96-bit checked domain: {value}")
    return value


def saturate_signed(value: int, bits: int) -> tuple[int, bool]:
    """Clamp ``value`` into a ``bits``-wide signed integer. Returns (value, clipped)."""
    if bits < 2:
        raise FixedPointError("signed saturation needs at least two bits")
    low = -(1 << (bits - 1))
    high = (1 << (bits - 1)) - 1
    if value < low:
        return low, True
    if value > high:
        return high, True
    return value, False
