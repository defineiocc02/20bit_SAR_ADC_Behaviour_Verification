"""Signed reference-rail charge from physical split-DAC switch events.

Terminal charge is evaluated from each actual main/sub capacitor and the
conserved sampled subnode charge. A newly connected rail supplies the change in
that capacitor's bottom-plate charge; a retained connection also supplies charge
when another switch changes the floating subnode. Consequently a charge pulse
is not a fixed positive bit-weight pattern. Differential rails are separate.

This linearized load program uses nominal rail voltages. A joint reference/RA
solver may apply its reference-voltage perturbation through the returned DAC
sensitivities. Large-droop charge redistribution requires a nonlinear rail solve.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from .dem import split_switch_command

if TYPE_CHECKING:
    from .config import Config
    from .slice_pool import PhysicalSlicePool


@dataclass(frozen=True)
class ReferenceProgram:
    """Physical events for one conversion, in reference-rail [positive, negative] order.

    Attributes:
        times_s: Nondecreasing event times from the sample aperture [s].
        codes: Fine RDAC commands after each event; duplicate codes give zero load.
        charge_c: Signed rail-delivered charge at nominal rail voltages [C], (E, 2).
        sensitivity: Input-referred DAC sensitivity dVdac/dVrail, (E, 2).
        bottom_charge_c: Capacitor bottom-plate charge after the final event [C].
    """

    times_s: np.ndarray
    codes: np.ndarray
    charge_c: np.ndarray
    sensitivity: np.ndarray
    bottom_charge_c: np.ndarray


def sar_loading_codes(coarse: int, bits: int, units: int, dither: float) -> np.ndarray:
    """Return trial/decision loads for a binary-search SADC with a known dither.

    Comparisons are against the resolved static SADC decision, so thresholds and
    offset already included by that quantizer are preserved. The SADC reference
    is assumed independent of the modeled RDAC reference. Each trial is loaded,
    then retained or rejected; the final command includes the transferred dither.
    """
    # 独立审查 2026-09-25：coarse/units/dither 须为有限值
    if (
        not 1 <= bits <= 20
        or not np.isfinite(coarse)
        or not 0 <= coarse < 2**bits
        or not np.isfinite(units)
        or units < 1
    ):
        raise ValueError(
            "SAR load program requires a valid decision and positive finite code units"
        )
    if not np.isfinite(dither):
        raise ValueError("SAR load dither must be finite")
    decided = 0
    codes = [dither]
    for bit in range(bits - 1, -1, -1):
        trial = decided + 2**bit
        codes.append(trial * units + dither)
        if coarse >= trial:
            decided = trial
        codes.append(decided * units + dither)
    return np.asarray(codes, dtype=float)


def sampled_bottom_voltages(cfg: Config, held_v, bank_code: float) -> np.ndarray:
    """Return the actual sampling connections, including the fixed dither mask.

    Args:
        cfg: Nominal capacitor-bank and dither controls.
        held_v: One acquired voltage per active slice [V].
        bank_code: Sampling-dither bank code (zero outside sampling mode).

    Returns:
        Bottom-plate voltages [V], shape (n_active, n_main+n_sub). Fractional
        dither is explicitly the continuous interpolation baseline.
    """
    held = np.asarray(held_v, dtype=float)
    if held.shape != (cfg.n_active,) or np.any(~np.isfinite(held)):
        raise ValueError("held voltages must be finite and match the active slice count")
    result = np.broadcast_to(held[:, None], (cfg.n_active, cfg.dac_n_units)).copy()
    if cfg.dither_mode == "sampling":
        d = cfg.dither_units_range
        nd = int(2 * d)
        if nd:
            offset = (
                cfg.dac_n_main - nd if cfg.dither_split_bank == "main" else cfg.dac_n_units - nd
            )
            mask = np.clip(d + bank_code - np.arange(nd), 0, 1)
            result[:, offset : offset + nd] = cfg.v_fs * (2 * mask - 1)
    return result


def physical_reference_program(
    cfg: Config,
    pool: PhysicalSlicePool,
    slice_ids,
    codes,
    times_s,
    sid: int,
    sampled_bottom_v,
) -> ReferenceProgram:
    """Compute switch loads using each slice's conserved floating-node charge.

    Args:
        cfg: Resolved split topology and nominal rail voltage.
        pool: Fabricated capacitances; only selected slices participate.
        slice_ids: Physical IDs for this conversion, shape (n_active,).
        codes: Fine RDAC commands after successive switching events.
        times_s: Nondecreasing event times [s], shape matching codes.
        sid: Nominal digital DEM state.
        sampled_bottom_v: Physical sampling connections [V], (n_active, n_units).

    Returns:
        Signed rail charges and physical reference sensitivities. No physical
        weights are exported to digital reconstruction or calibration features.

    Raises:
        ValueError: Incompatible dimensions, topology or event timing.
    """
    ids = np.asarray(slice_ids, dtype=np.int64)
    code = np.asarray(codes, dtype=float)
    times = np.asarray(times_s, dtype=float)
    sampled = np.asarray(sampled_bottom_v, dtype=float)
    if (
        not pool.is_split
        or ids.shape != (cfg.n_active,)
        or len(np.unique(ids)) != cfg.n_active
        or np.any(ids < 0)
        or np.any(ids >= cfg.n_slices)
        or code.ndim != 1
        or code.size == 0
        or times.shape != code.shape
        or np.any(~np.isfinite(times))
        or times[0] < 0
        or np.any(np.diff(times) < 0)
        or sampled.shape != (cfg.n_active, cfg.dac_n_units)
        or np.any(~np.isfinite(sampled))
    ):
        raise ValueError("reference program requires physical slices and ordered finite events")
    caps = pool.unit_caps[ids]
    nm, ns = cfg.dac_n_main, cfg.dac_n_sub
    sub_total = caps[:, nm:].sum(axis=1)
    denom = pool.bridge_caps[ids] + sub_total + pool.sub_parasitic[ids]
    beta = pool.bridge_caps[ids] / denom
    sig = (caps[:, :nm].sum(axis=1) + beta * sub_total).sum()
    sampled_q = caps * sampled
    sampled_sub_q = sampled_q[:, nm:].sum(axis=1)
    previous = sampled_q.copy()
    commands = split_switch_command(cfg, code, np.full(code.shape, sid, dtype=np.int64))
    charge = np.empty((len(code), 2))
    sensitivity = np.empty_like(charge)
    for i in range(len(code)):
        plus = np.empty_like(caps)
        for start, size, order, count in (
            (0, nm, commands.main_order[i], commands.main_counts[i]),
            (nm, ns, commands.sub_order[i], commands.sub_counts[i]),
        ):
            take = np.clip(count[:, None] - np.arange(size), 0, 1)
            plus[:, start + order] = take
        vb = cfg.v_fs * (2 * plus - 1)
        subnode = ((caps[:, nm:] * vb[:, nm:]).sum(axis=1) - sampled_sub_q) / denom
        bottom_q = caps * vb
        bottom_q[:, nm:] -= caps[:, nm:] * subnode[:, None]
        change = bottom_q - previous
        charge[i] = [np.sum(plus * change), np.sum((1 - plus) * change)]
        weights = caps.copy()
        weights[:, nm:] *= beta[:, None]
        sensitivity[i, 0] = np.sum(weights * plus) / sig
        sensitivity[i, 1] = np.sum(weights * (1 - plus)) / sig
        previous = bottom_q
    return ReferenceProgram(times.copy(), code.copy(), charge, sensitivity, previous)
