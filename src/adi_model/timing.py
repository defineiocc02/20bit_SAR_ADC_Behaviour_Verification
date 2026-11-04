"""Causal slice scheduling shared by vectorized and event-driven runners."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .config import Config


@dataclass
class SlicePlan:
    """Cycle assignments with explicit acquisition ownership and startup state.

    ``conv[n]`` holds the acquisition from ``acq[n-1]``. Cycle zero is invalid
    until the consuming runner performs an explicit priming acquisition.
    Group order denotes wiring order, not a new physical sample.
    """

    conv: np.ndarray
    acq: np.ndarray
    conv_source_cycle: np.ndarray
    valid: np.ndarray
    strategy: str
    meta: dict = field(default_factory=dict)

    def __len__(self) -> int:
        """Return the number of conversion cycles."""
        return int(self.conv.shape[0])


def _lfsr8(state: int) -> int:
    for _ in range(8):
        state = (state >> 1) ^ (0x80200003 if state & 1 else 0)
    return state


def rtl_dither_codes(n_samples: int, seed: int) -> np.ndarray:
    """Return the structural D=2 LFSR dither sequence after acquisition priming."""
    if type(n_samples) is not int or n_samples < 0:
        raise ValueError("RTL dither needs a nonnegative integer sample count")
    if type(seed) is not int or not 0 < seed <= 0xFFFF_FFFF:
        raise ValueError("RTL dither seed must be a nonzero uint32")
    out = np.empty(n_samples, dtype=np.int64)
    state = seed
    for i in range(n_samples):
        reduced = (state >> 24) % 8
        out[i] = ((reduced + 1) >> 1) - 2
        state = _lfsr8(state)
    return out


def build_rtl_slice_plan(n_samples: int, *, shuffle: bool, seed: int) -> SlicePlan:
    """Reproduce the structural allocator's priming, ordering and LFSR cursor.

    This fixed 18/8 profile is an engineering implementation, not a claim that
    the paper discloses its PRNG. The first advance is discarded as invalid.
    """
    if type(n_samples) is not int or n_samples < 0:
        raise ValueError("RTL schedule needs a nonnegative integer sample count")
    if type(shuffle) is not bool or type(seed) is not int or not 0 < seed <= 0xFFFF_FFFF:
        raise ValueError("RTL schedule needs a boolean shuffle and a nonzero uint32 seed")
    conv = np.empty((n_samples, 8), dtype=np.int64)
    acq = np.empty_like(conv)
    current = np.arange(8, dtype=np.int64)
    cursor, state = 8, seed
    for cycle in range(n_samples + 1):
        used = set(map(int, current))
        following: list[int] = []
        for k in range(18):
            candidate = (cursor + k) % 18
            if candidate not in used and len(following) < 8:
                following.append(candidate)
        next_ids = np.asarray(following, dtype=np.int64)
        if cycle:
            conv[cycle - 1] = current
            acq[cycle - 1] = next_ids
        current = next_ids
        if shuffle:
            raw = state >> 24
            state = _lfsr8(state)
            if raw < 252:
                cursor = raw % 18
        else:
            cursor = 0
    return SlicePlan(
        conv,
        acq,
        np.arange(n_samples, dtype=np.int64) - 1,
        np.ones(n_samples, dtype=bool),
        "rtl16",
        {"shuffle": shuffle, "seed": seed, "startup_advances": 2},
    )


def build_slice_plan(
    cfg: Config,
    n_samples: int,
    rng: np.random.Generator,
    strategy: str = "pingpong",
    *,
    spare_period: int | None = None,
) -> SlicePlan:
    """Schedule acquisitions from free slices and consume exactly those slices.

    Args:
        cfg: Pool dimensions; no fabricated capacitor values are read.
        n_samples: Nonnegative number of cycles.
        rng: Dedicated scheduling random stream.
        strategy: ``pingpong`` or ``shuffle_causal``.
        spare_period: Optional deterministic spare substitution interval.

    Returns:
        Per-cycle plan. Every transition has full sample ownership.

    Raises:
        ValueError: Invalid dimensions, strategy or spare interval.
    """
    n_active = cfg.n_active
    if n_samples < 0 or cfg.n_slices < 2 * n_active or n_active < 1:
        raise ValueError("invalid cycle count or slice pool dimensions")
    if strategy not in ("pingpong", "shuffle_causal"):
        raise ValueError(f"unknown strategy {strategy!r}")
    if spare_period is not None and spare_period < 1:
        raise ValueError("spare_period must be positive")
    conv = np.empty((n_samples, n_active), dtype=np.int64)
    acq = np.empty_like(conv)
    all_ids = np.arange(cfg.n_slices)
    current = np.arange(n_active)
    if strategy == "pingpong" and spare_period is None:
        groups = np.arange(2 * n_active).reshape(2, n_active)
        parity = np.arange(n_samples) % 2
        source = np.arange(n_samples, dtype=np.int64) - 1
        return SlicePlan(
            groups[parity],
            groups[1 - parity],
            source,
            source >= 0,
            strategy,
            {"n_slices": cfg.n_slices, "n_active": n_active, "spare_period": None},
        )
    for n in range(n_samples):
        conv[n] = current
        free = all_ids[~np.isin(all_ids, current)]
        if strategy == "shuffle_causal":
            nxt = np.sort(rng.permutation(free)[:n_active])
        else:
            nxt = free[:n_active].copy()
            if spare_period is not None and n % spare_period == 0 and free.size > n_active:
                nxt[n // spare_period % n_active] = free[n_active:][
                    n // spare_period % (free.size - n_active)
                ]
                nxt.sort()
        acq[n] = nxt
        current = nxt
    source = np.arange(n_samples, dtype=np.int64) - 1
    return SlicePlan(
        conv,
        acq,
        source,
        source >= 0,
        strategy,
        {"n_slices": cfg.n_slices, "n_active": n_active, "spare_period": spare_period},
    )
