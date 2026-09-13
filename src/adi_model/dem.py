"""Nominal, realizable split-DAC switch maps, independent of fabricated weights."""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil, sqrt

import numpy as np

from .config import Config


@dataclass(frozen=True)
class SplitSwitchCommand:
    """Compact physical masks: capacitor orders and selected counts.

    Orders have shape (N, bank_size); counts have shape (N, n_active).
    Fractional counts are interpolation baselines. Integer commands produce
    integer counts and realizable binary masks in every slice.
    """

    main_order: np.ndarray
    sub_order: np.ndarray
    main_counts: np.ndarray
    sub_counts: np.ndarray


def split_switch_command(cfg: Config, k: np.ndarray, sid: np.ndarray) -> SplitSwitchCommand:
    """Decode independent row/column/subarray states and optional zero-sum bridge.

    Args:
        cfg: Nominal topology, never a physical chip object.
        k: Fine RDAC codes, shape (N,); saturation is physical and explicit.
        sid: Digital DEM states, shape (N,).

    Returns:
        Realizable orders/counts with invariant mean nominal fine code. The
        redundant mapping is a model assumption, not a disclosed switch matrix.

    Raises:
        ValueError: Non-finite codes or inconsistent array shapes.
    """
    code = np.asarray(k, dtype=float)
    states = np.asarray(sid, dtype=np.int64)
    if code.ndim != 1 or states.shape != code.shape or not np.all(np.isfinite(code)):
        raise ValueError("DEM codes and states must be finite, equal-length vectors")
    code = np.clip(code, 0, cfg.dac_levels - 1)
    if not cfg.dem_enable:
        states = np.zeros_like(states)
    nm, ns = cfg.dac_n_main, cfg.dac_n_sub
    width = ceil(sqrt(nm))
    height = ceil(nm / width)
    cells = np.arange(height * width)
    row, col = np.divmod(cells, width)
    rr = (row[None, :] + (states // width % height)[:, None]) % height
    cc = (col[None, :] + (states % width)[:, None]) % width
    order = rr * width + cc
    main_order = order[order < nm].reshape(len(code), nm)
    sub_order = (np.arange(ns)[None, :] + (states // (width * height))[:, None]) % ns
    slice_codes = np.broadcast_to(code[:, None], (len(code), cfg.n_active)).copy()
    if cfg.dem_enable and cfg.dem_bridge_enable:
        rank = (np.arange(cfg.n_active)[None, :] - (states % cfg.n_active)[:, None]) % cfg.n_active
        half = cfg.n_active // 2
        signs = (rank < half).astype(float) - ((rank >= half) & (rank < 2 * half))
        amount = np.minimum(1, np.minimum(code, cfg.dac_levels - 1 - code))
        slice_codes += signs * amount[:, None]
    main_counts = np.floor(slice_codes / ns)
    sub_counts = slice_codes - ns * main_counts
    return SplitSwitchCommand(main_order, sub_order, main_counts, sub_counts)
