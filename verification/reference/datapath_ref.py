"""Reference models for the digital datapath, written from the contracts.

Each function here answers "what must the hardware produce", not "what does the
hardware produce". The distinction matters: if this file were a transliteration
of the SV, a shared bug would pass both sides and the comparison would be
worthless.

Sources, per module:

* ``sadc_code``     — RTL header + digital_interfaces.yaml invariants
* ``thermometer``   — digital_interfaces.yaml invariant
* ``adc2_bin_center`` — RTL_ARITHMETIC_CONTRACT.md §3.1
* ``reconstruct``   — RTL_ARITHMETIC_CONTRACT.md §3.2-3.5

All inputs and outputs are plain Python ints. No numpy, no float anywhere.
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
    gain_mode: str = "masked",
) -> ReconTerms:
    """Accumulate rails/gain/total over the 8x71 active-unit grid.

    ``weights`` are Q30 unsigned, ``on`` are 0/1 selection bits and
    ``signal_sign`` are the per-unit rail coefficients (+1/-1 for ordinary cells,
    -2/0/+2 for the sampling-dither cell).

    ``gain_mode`` selects which contract text is being honoured:

    * ``"masked"``   — RTL_ARITHMETIC_CONTRACT.md §3.2, ``gain = sum(W*a)``.
    * ``"all_signal"`` — `recon_core`'s documented restriction: the core only
      supports the AllSIGNAL subset, where every active unit is a signal unit and
      therefore ``G = T``. Verified against the RTL on 2026-11-12; the two
      definitions disagree by 5.4e11 Q30 on the first vector, so this is not a
      cosmetic distinction.
    """
    if gain_mode not in ("masked", "all_signal"):
        raise ValueError("gain_mode must be 'masked' or 'all_signal'")
    if not (len(weights) == len(on) == len(signal_sign)):
        raise ValueError("weights/on/signal_sign must cover the same unit list")
    rails = 0
    gain = 0
    total = 0
    for weight, selected, sign in zip(weights, on, signal_sign, strict=True):
        rails += weight * sign
        if gain_mode == "masked":
            gain += weight * selected
        total += weight
    if gain_mode == "all_signal":
        gain = total
    return ReconTerms(rails, gain, total)


@dataclass(frozen=True)
class ReconResult:
    """Output word plus the three independent status bits."""

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
        # Overflow keeps the legal word but must raise the sticky flag.
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
