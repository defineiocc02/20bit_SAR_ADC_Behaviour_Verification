"""Pure integer width/RTE/floor/MAC mirror for explicit numerical fixtures.

This library has no Config, pipeline, physical mask inference, ADC2 device
parameters or production format defaults. A mirror is not an HDL execution or
formal proof; tests compare these operations against independent integer rules.
"""

from __future__ import annotations


def _positive(value: int, name: str, minimum: int = 1) -> int:
    """Validate an explicitly supplied integer against an inclusive lower bound.

    Args:
        value: Integer input to check.
        name: Input name included in an error message.
        minimum: Inclusive lower bound.

    Returns:
        The unchanged input; an invalid type or value raises ValueError.
    """
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an explicit integer >= {minimum}")
    return value


def _sgn(x: int, w: int) -> int:
    """Apply signed two's-complement port/assignment truncation.

    Args:
        x: Integer port pattern.
        w: Positive port width [bits].

    Returns:
        The low w bits interpreted as a signed integer.
    """
    _positive(w, "signed width")
    x &= (1 << w) - 1
    return x - (1 << w) if x >> (w - 1) else x


def _u(x: int, w: int) -> int:
    """Apply unsigned port/assignment truncation.

    Args:
        x: Integer port pattern.
        w: Positive port width [bits].

    Returns:
        The low w bits interpreted as an unsigned integer.
    """
    _positive(w, "unsigned width")
    return x & ((1 << w) - 1)


def div_widths(P_W_A: int, P_W_D: int, P_STAGES: int) -> tuple[int, int, int]:
    """Derive the explicit restoring-divider width and iteration counts.

    Args:
        P_W_A: Numerator/quotient width, at least two bits.
        P_W_D: Positive denominator width [bits].
        P_STAGES: Positive bit iterations per cycle.

    Returns:
        Remainder width [bits], cycle count, and padded numerator width [bits].
    """
    _positive(P_W_A, "numerator width", 2)
    _positive(P_W_D, "denominator width")
    _positive(P_STAGES, "unroll stages")
    cycles = (P_W_A + P_STAGES - 1) // P_STAGES
    return P_W_A, cycles, cycles * P_STAGES


def adc2_widths(P_ADC2_BITS: int, V_BITS: int) -> tuple[int, int, int]:
    """Derive widths for the explicitly sized integer interpolation.

    Args:
        P_ADC2_BITS: Positive raw-code width [bits].
        V_BITS: Signed endpoint width, at least two bits.

    Returns:
        Rounding shift, product width [bits], and sum width [bits].
    """
    _positive(P_ADC2_BITS, "ADC interpolation code width")
    _positive(V_BITS, "voltage width", 2)
    product = P_ADC2_BITS + 1 + V_BITS + 1
    return P_ADC2_BITS + 1, product, product + 1


def round_half_even_shift(x: int, k: int) -> int:
    """Apply an arithmetic shift with signed nearest-even remainder correction.

    Args:
        x: Signed integer numerator.
        k: Positive shift count; the denominator is 2**k.

    Returns:
        The nearest integer to x/2**k, with ties rounded to an even integer.
    """
    _positive(k, "rounding shift")
    q = x >> k
    remainder = _u(x, k)
    half = 1 << (k - 1)
    return q + int(remainder > half or (remainder == half and q & 1))


def adc2_dec(code: int, min_q: int, max_q: int, nbits: int, voltage_bits: int) -> tuple[int, bool]:
    """Mirror the increment-RTE decoder with explicit integer endpoints.

    Args:
        code: Raw pattern truncated to nbits.
        min_q: Lower endpoint pattern interpreted at voltage_bits width.
        max_q: Upper endpoint pattern interpreted at voltage_bits width.
        nbits: Positive raw-code width [bits].
        voltage_bits: Signed endpoint/result width, at least two bits.

    Returns:
        Decoded integer in the endpoint units and its signed-range overflow flag.
        An overflowing result saturates at the corresponding signed bound.
    """
    k, product_width, sum_width = adc2_widths(nbits, voltage_bits)
    lo, hi = _sgn(min_q, voltage_bits), _sgn(max_q, voltage_bits)
    delta = _sgn(hi - lo, voltage_bits + 1)
    numerator = _sgn((2 * _u(code, nbits) + 1) * delta, product_width)
    increment = _sgn(round_half_even_shift(numerator, k), product_width)
    total = _sgn(lo + increment, sum_width)
    overflow = not -(1 << (voltage_bits - 1)) <= total < 1 << (voltage_bits - 1)
    if overflow:
        return ((1 << (voltage_bits - 1)) - 1 if total > 0 else -(1 << (voltage_bits - 1))), True
    return _sgn(total, voltage_bits), False


def div_floor_bitwise(
    a: int, d: int, P_W_A: int, P_W_D: int, P_STAGES: int, *, grouped_bit_order: bool = True
) -> tuple[int, bool]:
    """Mirror restoring division; false grouped bit order is a negative control.

    Args:
        a: Numerator pattern interpreted as signed P_W_A bits.
        d: Denominator pattern interpreted as unsigned P_W_D bits.
        P_W_A: Numerator/quotient width, at least two bits.
        P_W_D: Positive denominator width [bits].
        P_STAGES: Positive bit iterations per group.
        grouped_bit_order: Use the RTL order; False deliberately reverses it.

    Returns:
        Signed floor quotient and zero-divisor flag. A zero divisor returns
        quotient zero with the flag set.
    """
    width, cycles, padded = div_widths(P_W_A, P_W_D, P_STAGES)
    signed_a, unsigned_d = _sgn(a, P_W_A), _u(d, P_W_D)
    if unsigned_d == 0:
        return 0, True
    magnitude = abs(signed_a)
    divisor = _u(unsigned_d, width)
    large = bool(unsigned_d >> width)
    remainder = 0
    quotient = 0
    for cycle in range(cycles):
        bits = 0
        for stage in range(P_STAGES):
            position = padded - 1 - (cycle * P_STAGES + stage)
            shifted = ((_u(remainder, width - 1)) << 1) | ((magnitude >> position) & 1)
            difference = _u(shifted - divisor, width + 1)
            take = not large and not ((difference >> width) & 1)
            remainder = _u(difference, width) if take else shifted
            if take:
                bits |= 1 << (P_STAGES - 1 - stage if grouped_bit_order else stage)
        quotient = _u((quotient << P_STAGES) | bits, padded)
    absolute_q = _u(quotient, P_W_A)
    result = -(absolute_q + int(remainder != 0)) if signed_a < 0 else absolute_q
    return _sgn(result, P_W_A), False


def div_floor(a: int, d: int, P_W_A: int, P_W_D: int, P_STAGES: int) -> tuple[int, bool]:
    """Call the bitwise divider with every numerical width supplied explicitly.

    Args:
        a: Signed numerator port pattern.
        d: Unsigned denominator port pattern.
        P_W_A: Numerator/quotient width [bits].
        P_W_D: Denominator width [bits].
        P_STAGES: Number of bit iterations per group.

    Returns:
        Signed floor quotient and zero-divisor flag.
    """
    return div_floor_bitwise(a, d, P_W_A, P_W_D, P_STAGES)


def reconstruct(
    *,
    code: int,
    adc2_bits: int,
    min_q: int,
    max_q: int,
    offset_q: int,
    known_common_q: int,
    total: int,
    gain: int,
    rails: int,
    weight_fraction_bits: int,
    voltage_fraction_bits: int,
    voltage_bits: int,
    accumulator_bits: int,
    output_bits: int,
    sum_bits: int,
    divider_stages: int,
) -> tuple[int, bool, bool]:
    """Mirror a supplied T/G/R/common integer path and return the unclipped q.

    No capacitance, mask geometry, signal connection or voltage unit is guessed.
    Hardware result retention and sample identity are separate protocol tests.

    Args:
        code: Raw ADC2 code pattern.
        adc2_bits: Raw ADC2 interpolation width [bits].
        min_q: Signed ADC2 lower endpoint pattern.
        max_q: Signed ADC2 upper endpoint pattern.
        offset_q: Signed offset pattern.
        known_common_q: Signed known common contribution.
        total: T, interpreted as unsigned sum_bits.
        gain: G, interpreted as unsigned sum_bits.
        rails: R, interpreted as signed sum_bits+2.
        weight_fraction_bits: Positive weight fractional-bit count.
        voltage_fraction_bits: Positive voltage fractional-bit count.
        voltage_bits: Signed voltage port width [bits].
        accumulator_bits: Signed checked-accumulator width [bits].
        output_bits: Positive output scaling width [bits].
        sum_bits: Positive weight-sum/divisor width [bits].
        divider_stages: Positive bit iterations per divider group.

    Returns:
        Unclipped quotient, arithmetic overflow flag, and zero-gain/zero-divisor
        flag. ADC2 interpolation overflow is not a separate return value.
    """
    for name, value in (
        ("weight_fraction_bits", weight_fraction_bits),
        ("voltage_fraction_bits", voltage_fraction_bits),
        ("voltage_bits", voltage_bits),
        ("accumulator_bits", accumulator_bits),
        ("output_bits", output_bits),
        ("sum_bits", sum_bits),
    ):
        _positive(value, name)
    numerator_bits = accumulator_bits - voltage_fraction_bits - 1
    div_widths(numerator_bits, sum_bits, divider_stages)
    wide = sum_bits + voltage_bits + 4
    if wide <= accumulator_bits or accumulator_bits + 1 < sum_bits:
        raise ValueError("unsupported intermediate-width relation")
    fine, _ = adc2_dec(code, min_q, max_q, adc2_bits, voltage_bits)
    total, gain = _u(total, sum_bits), _u(gain, sum_bits)
    rails = _sgn(rails, sum_bits + 2)
    off = _sgn(offset_q, voltage_bits)
    common = _sgn(known_common_q, voltage_bits)
    op1 = _sgn((fine - off) << weight_fraction_bits, wide)
    op2 = _sgn(rails << voltage_fraction_bits, wide)
    op3 = _sgn(total * common, wide)
    numerator = _sgn(op1 - op2 - op3, wide)
    limit = 1 << (accumulator_bits - 1)
    bad_operand = any(not -limit <= item < limit for item in (op1, op2, op3, numerator))
    denominator_bad = gain >= 1 << (accumulator_bits - voltage_fraction_bits - 2)
    s1_width = accumulator_bits + 1
    s1 = _sgn(_sgn(numerator, s1_width) + _sgn(gain << voltage_fraction_bits, s1_width), s1_width)
    shifted = _sgn(s1 << output_bits, accumulator_bits + output_bits + 1)
    overflow = bad_operand or denominator_bad or not -limit <= shifted < limit
    word, division_error = div_floor_bitwise(
        _sgn(shifted >> (voltage_fraction_bits + 1), numerator_bits),
        gain,
        numerator_bits,
        sum_bits,
        divider_stages,
    )
    return word, overflow, gain == 0 or division_error


def clip_word(quotient: int, output_bits: int) -> tuple[int, bool, bool]:
    """Saturate a quotient to the unsigned half-open output-code range.

    Args:
        quotient: Signed integer quotient before clipping.
        output_bits: Positive output width [bits].

    Returns:
        Saturated code, low-clipping flag, and high-clipping flag.
    """
    _positive(output_bits, "output width")
    low, high = quotient < 0, quotient >= 1 << output_bits
    return min(max(quotient, 0), (1 << output_bits) - 1), low, high


if __name__ == "__main__":
    raise SystemExit(
        "Numerical library only: explicit integer inputs are required; no default pipeline run"
    )
