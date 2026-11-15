"""Bit-true integer primitives shared by every reference model.

Derived from `docs/rtl/RTL_ARITHMETIC_CONTRACT.md` §3.1/§3.5 and §4, **not**
transliterated from the SystemVerilog. Where the two could drift, the contract
text is the authority and `verification/reports/` records the RTL disagreement.

Why this module exists at all: the Python *behavioural* model works in float and
cannot be used as a bit oracle. The three seams that bite are

* floor vs truncate on a negative quotient,
* ties-to-even vs add-half-then-truncate,
* raise vs wrap on overflow.

Each one is a separate function here so a test can point at it directly.
"""

from __future__ import annotations

ACC_LEGAL_MIN = -(2**95)
ACC_LEGAL_MAX_EXCLUSIVE = 2**95


class FixedPointError(ValueError):
    """Raised when an operation leaves the contract's legal integer domain."""


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
