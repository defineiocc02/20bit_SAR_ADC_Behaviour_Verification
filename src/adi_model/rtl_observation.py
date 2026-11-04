"""Decode structural RTL calibration observations without analog truth.

The 748-bit packet is emitted on the same edge as the structural reconstructor
accepts a sample. Fields are physical switch/rail decisions and raw ADC2 codes;
unknown capacitance and input voltage are deliberately absent. The caller owns
configuration epochs and supplies a separate known input for training.
"""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np

from .dem import split_switch_command
from .weight_calibration import CalibrationSpec, DigitalObservation

RTL_OBSERVATION_BITS = 748


def _bits(value: int, shift: int, width: int) -> int:
    return (value >> shift) & ((1 << width) - 1)


def _signed(value: int, width: int) -> int:
    return value - (1 << width) if value & (1 << (width - 1)) else value


def observations_from_rtl(
    packets: Iterable[int | str],
    spec: CalibrationSpec,
    *,
    static_charge_domain: bool,
    require_consecutive: bool = True,
) -> DigitalObservation:
    """Decode and independently check one epoch of structural observations.

    Args:
        packets: Integer payloads or hexadecimal strings, in acceptance order.
        spec: Frozen nominal geometry and the actual hardware dither mode.
        static_charge_domain: Caller-declared model validity; false prevents fit.
        require_consecutive: Reject dropped IDs by default, including at wrap.

    Returns:
        Digital observations with actual physical masks and original uint32 IDs.

    Raises:
        ValueError: Bad dimensions, masks, mode, ID order, or packet contents.

    A capture cannot silently cross reset/clear: call separately per epoch.
    Timestamp and external reference alignment remain the caller's obligation.
    """
    if (
        spec.n_slices != 18
        or spec.n_active != 8
        or spec.dac_n_main != 63
        or spec.dac_n_sub != 8
        or spec.adc2_n_bits != 12
        or spec.dither_units_range != 2
        or not spec.dac_complete_range
        or spec.dither_mode not in ("off", "sampling", "quantizer")
    ):
        raise ValueError("RTL packet requires the fixed 18/8, 63+8, 12-bit structural profile")
    if type(static_charge_domain) is not bool:
        raise ValueError("static charge-domain validity must be declared explicitly")
    words = [int(p, 16) if isinstance(p, str) else int(p) for p in packets]
    if not words or any(w < 0 or w.bit_length() > RTL_OBSERVATION_BITS for w in words):
        raise ValueError("RTL packet list must be nonempty and fit exactly 748 bits")
    n = len(words)
    ids = np.empty((n, 8), dtype=np.int64)
    main = np.empty((n, 8, 63), dtype=np.int8)
    sub = np.empty((n, 8, 8), dtype=np.int8)
    rails = np.empty((n, 4), dtype=np.int8)
    sample_ids = np.empty(n, dtype=np.int64)
    coarse = np.empty(n, dtype=np.int64)
    sid = np.empty(n, dtype=np.int64)
    dither = np.empty(n, dtype=np.int64)
    fine = np.empty(n, dtype=np.int64)
    injection = np.empty(n, dtype=float)
    bad = np.empty(n, dtype=bool)
    for i, word in enumerate(words):
        fine[i] = _bits(word, 0, 12)
        injection[i] = _signed(_bits(word, 12, 64), 64) * spec.v_fs / 2**32
        bad[i] = bool(_bits(word, 76, 1))
        sampling = bool(_bits(word, 77, 1))
        if sampling != (spec.dither_mode == "sampling"):
            raise ValueError(f"sample {i}: sampling mode differs from the frozen configuration")
        for j in range(4):
            rails[i, j] = _bits(word, 78 + j, 1)
        for slot in range(8):
            for unit in range(8):
                sub[i, slot, unit] = _bits(word, 82 + 8 * slot + unit, 1)
            for unit in range(63):
                main[i, slot, unit] = _bits(word, 146 + 63 * slot + unit, 1)
            ids[i, slot] = _bits(word, 650 + 5 * slot, 5)
        dither[i] = _signed(_bits(word, 690, 8), 8)
        sid[i] = _bits(word, 698, 9)
        coarse[i] = _bits(word, 707, 9)
        sample_ids[i] = _bits(word, 716, 32)
    if n > 1:
        steps = np.diff(sample_ids) & 0xFFFF_FFFF
        if np.any(steps == 0) or np.any(steps > 0x7FFF_FFFF):
            raise ValueError("duplicated or reordered sample IDs in one epoch")
        if require_consecutive and np.any(steps != 1):
            raise ValueError("missing sample IDs in one epoch")
    if not spec.dem_enable and np.any(sid != 0):
        raise ValueError("observed DEM state is nonzero while DEM is disabled")
    if spec.dither_mode == "off" and np.any(dither != 0):
        raise ValueError("observed dither is nonzero while dither is disabled")
    if np.any(np.abs(dither) > 2):
        raise ValueError("observed structural dither is outside its configured range")
    bank = dither if spec.dither_mode == "sampling" else np.zeros(n, dtype=np.int64)
    if spec.dither_mode == "sampling":
        expected_rails = np.arange(4)[None, :] < 2 + bank[:, None]
        if not np.array_equal(rails, expected_rails):
            raise ValueError("observed dither rails disagree with the captured dither code")
    if spec.dither_mode == "quantizer":
        command = coarse - dither
    elif spec.dither_mode == "sampling":
        unit_ratio = 1 if spec.dither_split_bank == "sub" else spec.dac_n_sub
        command = coarse + dither * unit_ratio
    else:
        command = coarse.copy()
    bad |= (command < 0) | (command >= spec.dac_levels)
    command = np.clip(command, 0, spec.dac_levels - 1)
    # The captured physical masks are the fitting input. This independent
    # check only verifies the declared Python/RTL switching contract.
    expected = split_switch_command(spec, command.astype(float), sid)
    for count, order, observed in (
        (63, expected.main_order, main),
        (8, expected.sub_order, sub),
    ):
        predicted = np.empty_like(observed)
        counts = expected.main_counts if count == 63 else expected.sub_counts
        selected = (np.arange(count)[None, None, :] < counts[:, :, None]).astype(np.int8)
        np.put_along_axis(predicted, order[:, None, :], selected, axis=2)
        if not np.array_equal(predicted, observed):
            raise ValueError("RTL physical masks disagree with the Python switch contract")
    data = DigitalObservation(
        spec=spec,
        slice_ids=ids,
        rdac_code=command,
        dem_state=sid,
        adc2_code=fine,
        bank_dither=bank,
        common_injection_v=injection,
        overflow=bad,
        static_charge_domain=static_charge_domain,
        main_masks=main,
        sub_masks=sub,
        dither_rails=rails,
        sample_ids=sample_ids,
    )
    data.validate()
    return data
