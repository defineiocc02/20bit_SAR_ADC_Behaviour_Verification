"""Reference model for `slice_pool_ctrl` — the causal 8-of-18 ownership scheduler.

Written from the RTL header contract and the frozen update rules in
`verification/contracts/digital_interfaces.yaml`, not by calling the DUT.

The scheduler is the one block where a "does it look plausible" check is
worthless: an off-by-one in the cursor or a dropped prime still yields legal
8-hot groups. Only an exact state model catches it, which is why this file
exists separately from the RTL.
"""

from __future__ import annotations

from dataclasses import dataclass

N_SLICES = 18
N_ACTIVE = 8
LFSR_TAP = 0x80200003
CURSOR_GUARD = 252


@dataclass
class SchedulerState:
    """Complete state of the scheduler at a clock boundary."""

    acquiring_ids: list[int]
    acquiring_mask: int
    converting_ids: list[int]
    converting_mask: int
    conversion_valid: int
    random_state: int
    cursor: int
    primed: int
    sample_id: int

    def copy(self) -> "SchedulerState":
        return SchedulerState(
            list(self.acquiring_ids), self.acquiring_mask,
            list(self.converting_ids), self.converting_mask,
            self.conversion_valid, self.random_state, self.cursor,
            self.primed, self.sample_id,
        )


def reset_state(seed: int) -> SchedulerState:
    """Reset / disable values (reset and disable share one branch in the RTL)."""
    return SchedulerState(
        acquiring_ids=list(range(N_ACTIVE)),
        acquiring_mask=0x00FF,
        converting_ids=[0] * N_ACTIVE,
        converting_mask=0,
        conversion_valid=0,
        random_state=seed,
        cursor=8,
        primed=0,
        sample_id=0,
    )


def _lfsr_step(state: int, steps: int = 8) -> int:
    value = state & 0xFFFF_FFFF
    for _ in range(steps):
        value = (value >> 1) ^ (LFSR_TAP if value & 1 else 0)
        value &= 0xFFFF_FFFF
    return value


def _next_group(cursor: int, acquiring_mask: int) -> tuple[list[int], int]:
    """Complement scan: walk from the cursor, keep the first eight free slices."""
    ids: list[int] = []
    mask = 0
    for k in range(N_SLICES):
        candidate = cursor + k
        if candidate >= N_SLICES:
            candidate -= N_SLICES
        if not (acquiring_mask >> candidate) & 1 and len(ids) < N_ACTIVE:
            ids.append(candidate)
            mask |= 1 << candidate
    return ids, mask


def step(state: SchedulerState, *, rst_n: int, enable: int, advance: int,
         shuffle: int, seed: int) -> SchedulerState:
    """Advance one clock edge and return the new state."""
    if not rst_n or not enable:
        return reset_state(seed)
    if not advance:
        return state.copy()

    next_ids, next_mask = _next_group(state.cursor, state.acquiring_mask)
    updated = state.copy()
    updated.converting_ids = list(state.acquiring_ids)
    updated.converting_mask = state.acquiring_mask
    updated.conversion_valid = state.primed
    updated.primed = 1
    updated.sample_id = (state.sample_id + 1) & 0xFFFF_FFFF
    updated.acquiring_ids = next_ids
    updated.acquiring_mask = next_mask
    if shuffle:
        updated.random_state = _lfsr_step(state.random_state)
        byte = (state.random_state >> 24) & 0xFF
        if byte < CURSOR_GUARD:
            updated.cursor = byte % N_SLICES
    else:
        updated.cursor = 0
    return updated


def pack_ids(ids: list[int]) -> int:
    """Pack eight 5-bit ids lane-major, matching the RTL port order."""
    value = 0
    for lane, sid in enumerate(ids):
        value |= (sid & 0x1F) << (5 * lane)
    return value
