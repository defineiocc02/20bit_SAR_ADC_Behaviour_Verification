"""Pretracking estimates from available SADC decisions, never ideal input values.

The first-stage result becomes available at its configured decision-completion
time. A pending queue enforces that time even though an offline simulation may
already have generated all input samples. Estimates use nominal code weights;
physical capacitor/threshold truth is not part of this interface.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class DecisionRecord:
    """One quantized, timestamped source for a pretracking command."""

    sample_id: int
    ready_s: float
    slices: tuple[int, ...]
    estimate_v: float


@dataclass(frozen=True)
class PretrackPrediction:
    """One nominal voltage per slice plus up to three contributing sample IDs."""

    voltage_v: np.ndarray
    source_ids: np.ndarray
    ready_times_s: np.ndarray


@dataclass(frozen=True)
class InputAssistTrace:
    """Pretrack provenance and separate input/clock/auxiliary charge observations."""

    pretrack_start_s: np.ndarray
    pretrack_target_v: np.ndarray
    pretrack_source_ids: np.ndarray
    pretrack_available_s: np.ndarray
    pretrack_source_charge_c: np.ndarray
    auxiliary_voltage_v: np.ndarray
    auxiliary_branch_charge_c: np.ndarray
    auxiliary_source_charge_c: np.ndarray
    auxiliary_reset_charge_c: np.ndarray

    @classmethod
    def allocate(cls, n: int, active: int) -> InputAssistTrace:
        """Allocate an empty trace; unavailable decision IDs are -1 with NaN times."""
        return cls(
            np.full(n, np.nan),
            np.zeros((n, active)),
            np.full((n, active, 3), -1, dtype=np.int64),
            np.full((n, active, 3), np.nan),
            np.zeros(n),
            np.zeros(n),
            np.zeros(n),
            np.zeros(n),
            np.zeros(n),
        )


class QuantizedPretracker:
    """Causal queue and nominal digital estimator for a fabricated slice pool."""

    def __init__(
        self,
        n_slices: int,
        bits: int,
        lower_v: float,
        coarse_step_v: float,
        signal_scale: float = 1.0,
        weights=(0.6, 0.3, 0.1),
    ):
        """Initialize nominal digital weights and an empty decision history."""
        if (
            n_slices < 1
            or bits < 1
            or not np.isfinite(coarse_step_v)
            or coarse_step_v <= 0
            or not np.isfinite(signal_scale)
            or signal_scale <= 0
        ):
            raise ValueError("pretracking requires positive slice/decision counts and nominal step")
        self.bits, self.lower_v, self.step_v = bits, lower_v, coarse_step_v
        self.signal_scale = signal_scale
        self._weights = np.asarray(weights, dtype=float).copy()
        if (
            self._weights.shape != (3,)
            or np.any(~np.isfinite(self._weights))
            or np.any(self._weights < 0)
            or self._weights[0] <= 0
        ):
            raise ValueError(
                "pretracking requires three nonnegative weights with a positive newest weight"
            )
        self._pending: deque[DecisionRecord] = deque()
        self._recent: deque[DecisionRecord] = deque(maxlen=3)
        self._own: list[DecisionRecord | None] = [None] * n_slices
        self._last_id = -1
        self._last_ready = -np.inf
        self._last_query = -np.inf

    def record(self, sample_id, ready_s, slice_ids, coarse_code, known_injection_v=0.0):
        """Queue a completed SADC decision with its actual availability time.

        The nominal reconstruction is the coarse-bin center minus the known
        quantizer/input dither injection. No analog input or physical weight is
        accepted. Calls must preserve sample and completion-time order.
        """
        ids = tuple(int(i) for i in slice_ids)
        if (
            sample_id <= self._last_id
            or ready_s < self._last_ready
            or not np.isfinite(ready_s)
            or not np.isfinite(coarse_code)
            or coarse_code != int(coarse_code)
            or not 0 <= coarse_code < 2**self.bits
            or not ids
            or len(set(ids)) != len(ids)
            or min(ids) < 0
            or max(ids) >= len(self._own)
            or not np.isfinite(known_injection_v)
        ):
            raise ValueError(
                "pretracking decisions require ordered IDs/times and legal integer codes"
            )
        estimate = (
            self.lower_v + (coarse_code + 0.5) * self.step_v - known_injection_v
        ) / self.signal_scale
        self._pending.append(DecisionRecord(sample_id, ready_s, ids, float(estimate)))
        self._last_id, self._last_ready = sample_id, ready_s

    def predict(self, slice_ids, at_s: float, mode: str) -> PretrackPrediction:
        """Choose available nominal decisions at the start of pretracking.

        Modes: reset_mid (zero), latest, own (last use of each physical slice),
        weighted (up to three latest records, default weights 0.6:0.3:0.1). Missing
        history gives a zero command and source ID -1. No future record is used.
        """
        if mode not in ("reset_mid", "latest", "own", "weighted"):
            raise ValueError("unknown pretracking mode")
        if not np.isfinite(at_s) or at_s < self._last_query:
            raise ValueError("pretracking query times must be finite and monotonic")
        self._last_query = at_s
        while self._pending and self._pending[0].ready_s <= at_s:
            record = self._pending.popleft()
            self._recent.appendleft(record)
            for physical in record.slices:
                self._own[physical] = record
        ids = np.asarray(slice_ids, dtype=np.int64)
        if ids.ndim != 1 or np.any(ids < 0) or np.any(ids >= len(self._own)):
            raise ValueError("pretracking slice IDs are out of bounds")
        voltage = np.zeros(len(ids))
        sources = np.full((len(ids), 3), -1, dtype=np.int64)
        ready = np.full((len(ids), 3), np.nan)
        for j, physical in enumerate(ids):
            own = self._own[physical]
            if mode == "own":
                selected = [] if own is None else [own]
            elif mode == "latest":
                selected = list(self._recent)[:1]
            elif mode == "weighted":
                selected = list(self._recent)
            else:
                selected = []
            if selected:
                weights = self._weights[: len(selected)].copy()
                weights /= weights.sum()
                voltage[j] = np.dot(weights, [r.estimate_v for r in selected])
                sources[j, : len(selected)] = [r.sample_id for r in selected]
                ready[j, : len(selected)] = [r.ready_s for r in selected]
        return PretrackPrediction(voltage, sources, ready)
