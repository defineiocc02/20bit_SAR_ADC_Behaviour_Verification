"""Physical 18-slice pool with cross-cycle causality and slice-bound mismatch.

Why this module exists
----------------------
An external audit of v6.1 demonstrated — numerically — that the interleaving in
the previous model was not physical. Two findings, reproduced independently in
``tests/audit/test_audit_findings.py::TestF2SliceCausality``:

1. ``ShuffledScheduler.reserve_dual`` drew an *independent* 8-of-18 permutation
   every conversion cycle. Over 8191 cycle-to-cycle transitions, exactly **one**
   had the conversion group equal to the previous acquisition group; on average
   only **3.56 of 8** converting slices had actually acquired the sample being
   converted. A real capacitor cannot convert a charge it never sampled.
2. The 8-of-18 choice did not reach the capacitor weights. Running the
   phase-accurate path with a fixed schedule and with a shuffled schedule gave
   **bit-identical** output (max difference 0.0 uV), so "shuffling spreads the
   interleaving tones" was being demonstrated on error *coefficients* rather
   than on the physical capacitor array.

This module replaces both behaviours with an explicit physical pool.

Physical contract
-----------------
The chip has 18 slice DACs. During any conversion cycle:

* 8 slices **convert** — they must be exactly the 8 that completed an
  acquisition in a preceding cycle (one-cycle pipeline latency);
* 8 slices **acquire** the next sample, drawn from the 10 slices that are not
  converting (the 8 just released plus 2 spares);
* 2 slices are idle spares.

Because a slice may only convert a charge it actually holds, randomisation can
only choose *which* 10 − 8 = 2 slices sit out and how the acquisition group is
drawn from the free set. That is a strictly smaller randomisation space than an
unconstrained permutation, which is physically correct: the paper shuffles to
spread residual tones, not to teleport charge.

Unit contract
-------------
* ``unit_caps[i, j]`` — capacitance [F] of unit ``j`` of slice ``i``.
* ``v_top[i]``        — top-plate voltage [V] held by slice ``i``, input-referred.
* ``c_slice_total[i]``— total capacitance [F] of slice ``i``.
* Codes ``k`` are RDAC unit indices in [0, n_units); ``n_units = 8 * 64 = 512``.
* All voltages input-referred unless the name says ``topplate``.

Source grading
--------------
``n_slices`` / ``n_active`` are ``DISCLOSED`` ([00] "pool of 18 sDAC", "8
converting + 8 acquiring"). The *scheduling policy* under the causality
constraint is not disclosed; ``strategy="shuffle_causal"`` is ``ASSUMED``.

Example:
-------
>>> import numpy as np
>>> from adi_model.config import Config
>>> from adi_model.slice_pool import PhysicalSlicePool
>>> cfg = Config()
>>> pool = PhysicalSlicePool(cfg, np.random.default_rng(0))
>>> plan = pool.plan(64, np.random.default_rng(1), strategy="shuffle_causal")
>>> plan.conv.shape, plan.acq.shape
((64, 8), (64, 8))
>>> chk = check_causality(plan)
>>> chk["violations"]
0
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .config import Config
from .provenance import SourceGrade

__all__ = [
    "SlicePlan",
    "PhysicalSlicePool",
    "check_causality",
    "SLICE_POOL_GRADES",
]

SLICE_POOL_GRADES = {
    "n_slices": SourceGrade.DISCLOSED,
    "n_active": SourceGrade.DISCLOSED,
    "strategy": SourceGrade.ASSUMED,
}


@dataclass
class SlicePlan:
    """Per-cycle slice assignment produced by :meth:`PhysicalSlicePool.plan`.

    Attributes:
        conv: ``(N, n_active)`` int64 — slices converting cycle ``n``. Sample
            index convention: these slices hold the charge acquired at cycle
            ``n - 1``, so the emitted output at cycle ``n`` belongs to input
            sample ``n - 1``. See :attr:`conv_source_cycle`.
        acq: ``(N, n_active)`` int64 — slices acquiring during cycle ``n``;
            they will convert at cycle ``n + 1``.
        conv_source_cycle: ``(N,)`` int64 — cycle at which the converting
            slices performed their acquisition (``n - 1``, or ``-1`` during the
            fill phase before the pipeline is primed).
        valid: ``(N,)`` bool — False for cycles where the conversion group had
            not completed a legal acquisition. Consumers must not emit an output
            for those cycles instead of silently converting garbage.
        strategy: Scheduling strategy used.
    """

    conv: np.ndarray
    acq: np.ndarray
    conv_source_cycle: np.ndarray
    valid: np.ndarray
    strategy: str
    meta: dict = field(default_factory=dict)

    def __len__(self) -> int:
        """Number of planned cycles.

        Returns:
            int: Number of planned cycles (``N``), i.e. ``conv.shape[0]``.
        """
        return int(self.conv.shape[0])


def check_causality(plan: SlicePlan) -> dict:
    """Independently verify that every conversion follows a legal acquisition.

    This function is deliberately standalone so that a test can call it without
    going through the pool: it recomputes the relation from the plan arrays
    alone, which is what makes it a check rather than a restatement.

    Args:
        plan: A :class:`SlicePlan`.

    Returns:
        Dict with ``transitions``, ``violations`` (count of cycles whose
        conversion group is not exactly a previously-acquired group),
        ``mean_coverage`` (mean number of converting slices that had acquired
        the sample), and ``causal`` (bool).
    """
    n = len(plan)
    if n < 2:
        return {
            "transitions": 0,
            "violations": 0,
            "mean_coverage": float("nan"),
            "causal": True,
        }
    violations = 0
    coverage = np.empty(n - 1, dtype=float)
    for i in range(1, n):
        # The group converting at cycle i must equal a group that acquired
        # earlier and has not been overwritten since.
        common = len(set(plan.conv[i].tolist()) & set(plan.acq[i - 1].tolist()))
        coverage[i - 1] = common
        if common != plan.conv.shape[1]:
            violations += 1
    return {
        "transitions": n - 1,
        "violations": int(violations),
        "mean_coverage": float(np.mean(coverage)),
        "causal": violations == 0,
    }


class PhysicalSlicePool:
    """18 physical slices: their capacitors, their held charge, their history.

    Each slice owns ``n_unit_per_slice`` unit capacitors drawn once per virtual
    chip. Selecting a different 8-of-18 therefore selects a different set of
    physical capacitors, which changes the DAC weight vector, the signal
    capacitance and hence the residue gain — the coupling that was missing in
    v6.1.

    Attributes:
        cfg: Configuration.
        unit_caps: ``(n_slices, n_unit_per_slice)`` float64, in farads.
        c_slice_total: ``(n_slices,)`` float64, total capacitance per slice.
        v_top: ``(n_slices,)`` float64, held top-plate voltage per slice.
        held_sample: ``(n_slices,)`` int64, sample index currently held, -1 if
            the slice holds nothing valid.
    """

    def __init__(
        self,
        cfg: Config,
        rng: np.random.Generator,
        *,
        slice_sigma: float | None = None,
        unit_sigma: float | None = None,
        n_unit_per_slice: int | None = None,
    ) -> None:
        """Build the pool and draw the physical capacitors for one virtual chip.

        Args:
            cfg: Configuration. Uses ``n_slices``, ``n_active``,
                ``n_unit_per_slice``, ``mismatch_sigma0``, ``mismatch_split``,
                ``c_total0``, ``cap_scale``, ``n_active``.
            rng: Generator dedicated to the chip. Passing a separate generator
                keeps capacitor draws independent of noise realisations, so
                changing a noise setting does not re-roll the chip.
            slice_sigma: Override for the slice-level relative sigma. Default is
                derived from ``cfg.mismatch_split`` (slice variance fraction).
            unit_sigma: Override for the unit-level relative sigma.
            n_unit_per_slice: Override for units per slice.

        Side effects:
            Consumes draws from ``rng``; no global state is touched.
        """
        self.cfg = cfg
        n_slices = int(cfg.n_slices)
        n_up = int(n_unit_per_slice or cfg.n_unit_per_slice)
        self.n_slices = n_slices
        self.n_unit_per_slice = n_up
        self.n_units = int(cfg.n_active) * n_up

        # ---- variance split: (global, slice, unit) as variance fractions ----
        g_frac, s_frac, u_frac = cfg.mismatch_split
        tot = max(g_frac + s_frac + u_frac, 1e-12)
        s_frac, u_frac = s_frac / tot, u_frac / tot
        sig0 = float(cfg.mismatch_sigma0)
        sig_slice = float(slice_sigma) if slice_sigma is not None else sig0 * np.sqrt(s_frac)
        sig_unit = float(unit_sigma) if unit_sigma is not None else sig0 * np.sqrt(u_frac)

        c_unit_nom = (cfg.c_total0 * cfg.cap_scale) / self.n_units
        self.c_unit_nom = float(c_unit_nom)

        # Per-slice mean deviation (a slice-level systematic term) ...
        self.slice_dev = (
            rng.normal(0.0, sig_slice, n_slices) if sig_slice > 0 else np.zeros(n_slices)
        )
        # ... plus per-unit deviation inside each slice.
        unit_dev = (
            rng.normal(0.0, sig_unit, (n_slices, n_up))
            if sig_unit > 0
            else np.zeros((n_slices, n_up))
        )
        if not cfg.mismatch_enable:
            self.slice_dev = np.zeros(n_slices)
            unit_dev = np.zeros((n_slices, n_up))
        self.unit_caps = c_unit_nom * (1.0 + self.slice_dev[:, None] + unit_dev)
        self.c_slice_total = self.unit_caps.sum(axis=1)
        self.c_total_nom = c_unit_nom * self.n_units

        # ---- per-slice held state -----------------------------------------
        self.v_top = np.zeros(n_slices)
        self.held_sample = np.full(n_slices, -1, dtype=np.int64)
        self.held_valid = np.zeros(n_slices, dtype=bool)
        self.n_conversions = np.zeros(n_slices, dtype=np.int64)

        # ---- cumulative-sum table for vectorised DAC selection ------------
        # CUM_ALL[j * n_up + u, m] = sum of the first m units of slice j after
        # rotating its unit order by u. Built once per chip (~600 kB at
        # 18 x 64 x 65 float64) and then gathered per sample.
        self._cum_all = np.empty((n_slices * n_up, n_up + 1), dtype=float)
        for j in range(n_slices):
            cj = self.unit_caps[j]
            for u in range(n_up):
                rot = np.roll(cj, -u)
                self._cum_all[j * n_up + u] = np.concatenate([[0.0], np.cumsum(rot)])
        self._built = True

    # ------------------------------------------------------------------ plan
    def plan(
        self,
        n_samples: int,
        rng: np.random.Generator,
        strategy: str = "shuffle_causal",
    ) -> SlicePlan:
        """Produce a causally valid per-cycle slice assignment.

        Strategies:

        ``"pingpong"``
            Deterministic A/B alternation. Slices ``0..7`` and ``8..15`` take
            turns; the two spares never participate. Reproduces the fixed-group
            interleaving spur baseline.
        ``"shuffle_causal"``
            Causality-constrained randomisation. At every cycle the acquisition
            group is drawn from the 10 slices that are not converting, so the
            pool still randomises *which* slices are used but never converts a
            charge that was not acquired.

        Args:
            n_samples: Number of conversion cycles to plan (``N``).
            rng: Generator for the shuffle. Separate from the chip generator.
            strategy: One of the two names above.

        Returns:
            A :class:`SlicePlan`. The first cycle is always marked invalid
            because the pipeline has not yet primed: at cycle 0 nothing has been
            acquired.

        Raises:
            ValueError: On an unknown strategy, or a pool too small to form two
                disjoint groups.
        """
        if strategy not in ("pingpong", "shuffle_causal"):
            raise ValueError(f"unknown strategy {strategy!r}")
        n_act = int(self.cfg.n_active)
        if self.n_slices < 2 * n_act:
            raise ValueError("slice pool too small for two disjoint groups")

        conv = np.empty((n_samples, n_act), dtype=np.int64)
        acq = np.empty((n_samples, n_act), dtype=np.int64)
        src = np.full(n_samples, -1, dtype=np.int64)
        valid = np.zeros(n_samples, dtype=bool)

        # State: which slices currently hold a valid acquisition, and for which
        # sample. Start empty -> cycle 0 cannot convert.
        held: dict[int, int] = {}  # slice -> sample index it holds

        if strategy == "pingpong":
            a = np.arange(0, n_act)
            b = np.arange(n_act, 2 * n_act)
            for n in range(n_samples):
                if n >= 1:
                    # The group that acquired during cycle n-1 converts now.
                    c = b if (n % 2 == 1) else a
                    conv[n] = c
                    src[n] = n - 1
                    valid[n] = all(held.get(int(s), -1) == n - 1 for s in c)
                else:
                    conv[n] = a
                free = np.setdiff1d(np.arange(self.n_slices), conv[n])
                acq[n] = free[:n_act]
                for s in acq[n]:
                    held[int(s)] = n
        else:
            all_ids = np.arange(self.n_slices)
            for n in range(n_samples):
                if n >= 1:
                    # Converting group = the slices that acquired sample n-1.
                    cand = np.array([s for s, k in held.items() if k == n - 1], dtype=np.int64)
                    if cand.size >= n_act:
                        conv[n] = np.sort(cand[:n_act])
                        src[n] = n - 1
                        valid[n] = True
                    else:  # pragma: no cover - defensive
                        conv[n] = np.sort(cand)[:n_act]
                        valid[n] = False
                else:
                    conv[n] = np.arange(n_act)
                free = np.setdiff1d(all_ids, conv[n])
                # Randomise within the free set: this is where shuffling lives.
                pick = rng.permutation(free)[:n_act]
                acq[n] = np.sort(pick)
                # Slices that were dropped stop holding a valid sample.
                for s in list(held):
                    if s not in set(acq[n].tolist()):
                        held.pop(s, None)
                for s in acq[n]:
                    held[int(s)] = n

        return SlicePlan(
            conv=conv,
            acq=acq,
            conv_source_cycle=src,
            valid=valid,
            strategy=strategy,
            meta={"n_slices": self.n_slices, "n_active": n_act},
        )

    # -------------------------------------------------------------- DAC math
    def signal_capacitance(self, conv: np.ndarray) -> np.ndarray:
        """Total capacitance of the converting slice set, per sample.

        This is the quantity that sets the residue gain in the charge-consistent
        model (``G = C_sig / C_F``). It varies with the 8-of-18 choice, so the
        choice modulates the gain — a physical coupling v6.1 did not have.

        Args:
            conv: ``(N, n_active)`` int64 slice indices.

        Returns:
            ``(N,)`` float64 total capacitance [F].
        """
        return self.c_slice_total[np.asarray(conv, dtype=np.int64)].sum(axis=1)

    def selected_capacitance(
        self,
        conv: np.ndarray,
        k: np.ndarray,
        sid: np.ndarray,
    ) -> np.ndarray:
        """Sum of the capacitances actually switched on, per sample.

        The code ``k`` (in [0, n_units)) is distributed over the converting
        slices: ``q = k // n_active`` units per slice plus one extra unit in
        ``r = k % n_active`` of them, the extra slices rotating with the DEM
        state. Within each slice the first ``k_j`` units of a rotated order are
        selected. Both rotations come from ``sid``, which is how the 3-bit
        horizontal / 3-bit vertical / 8-of-18 dimensions of the paper's DEM
        enter the weight vector.

        Args:
            conv: ``(N, n_active)`` int64 slice indices.
            k: ``(N,)`` float or int RDAC code in unit steps.
            sid: ``(N,)`` int DEM state.

        Returns:
            ``(N,)`` float64 selected capacitance [F].
        """
        conv = np.asarray(conv, dtype=np.int64)
        n_act = conv.shape[1]
        k = np.rint(np.asarray(k, dtype=float)).astype(np.int64)
        k = np.clip(k, 0, self.n_units)
        sid = np.asarray(sid, dtype=np.int64)

        q, r = np.divmod(k, n_act)
        counts = np.repeat(q[:, None], n_act, axis=1).astype(np.int64)
        sid_s = (sid // self.n_unit_per_slice) % n_act  # which slices get extra
        sid_u = sid % self.n_unit_per_slice  # within-slice rotation
        for o in range(n_act):
            m = o < r
            if not m.any():
                continue
            j = (sid_s + o) % n_act
            counts[m, j[m]] += 1

        idx = conv * self.n_unit_per_slice + sid_u[:, None]
        sel = np.zeros(conv.shape[0], dtype=float)
        for j in range(n_act):
            sel += self._cum_all[idx[:, j], counts[:, j]]
        return sel

    def dac_voltage(
        self,
        conv: np.ndarray,
        k: np.ndarray,
        sid: np.ndarray,
        *,
        nominal: bool = False,
    ) -> np.ndarray:
        """Input-referred DAC voltage of the selected units.

        For a unary array the input-referred DAC voltage is
        ``v_D = V_fs * (2 * C_sel / C_total - 1)``. The nominal branch uses the
        ideal unit capacitance so that the digital side, which only knows
        nominal weights, stays exactly linear; the difference between the two
        branches is the DAC error.

        Args:
            conv: ``(N, n_active)`` int64 slice indices.
            k: ``(N,)`` RDAC code in unit steps.
            sid: ``(N,)`` int DEM state.
            nominal: If True, ignore mismatch and use ideal weights.

        Returns:
            ``(N,)`` float64 input-referred DAC voltage [V].
        """
        v_fs = float(self.cfg.v_fs)
        c_tot: float | np.ndarray
        if nominal:
            c_sel = np.asarray(k, dtype=float) * self.c_unit_nom
            c_tot = self.c_total_nom
        else:
            c_sel = self.selected_capacitance(conv, k, sid)
            c_tot = self.signal_capacitance(conv)
        return v_fs * (2.0 * c_sel / np.maximum(c_tot, 1e-30) - 1.0)

    def dac_error(self, conv: np.ndarray, k: np.ndarray, sid: np.ndarray) -> np.ndarray:
        """DAC error [V] = true input-referred voltage minus nominal.

        Args:
            conv: ``(N, n_active)`` int64 slice indices.
            k: ``(N,)`` RDAC code.
            sid: ``(N,)`` int DEM state.

        Returns:
            ``(N,)`` float64 error [V], input-referred.
        """
        return self.dac_voltage(conv, k, sid) - self.dac_voltage(conv, k, sid, nominal=True)

    # ---------------------------------------------------------- held charge
    def acquire(
        self,
        acq: np.ndarray,
        x: float,
        *,
        eps: np.ndarray | float = 0.0,
        skew: np.ndarray | None = None,
        offset: np.ndarray | None = None,
        dxdt: float = 0.0,
    ) -> float:
        """Charge the acquisition group towards ``x`` and record the held value.

        Args:
            acq: ``(n_active,)`` int64 slice indices.
            x: Target input voltage [V].
            eps: Per-slice settling residual, or a scalar. ``x_saved = x -
                eps * (x - v_top)``.
            skew: Per-slice timing skew [s]; if given the slice samples at
                ``x + skew * dxdt``.
            offset: Per-slice input-referred offset [V].
            dxdt: Input slope [V/s] at the sampling instant.

        Returns:
            Mean acquisition error [V] over the group, input-referred.

        Side effects:
            Updates ``v_top``, ``held_valid`` and ``n_conversions`` for the
            acquisition group.
        """
        acq = np.asarray(acq, dtype=np.int64)
        eps_arr = (
            np.full(acq.size, float(np.asarray(eps, dtype=float)))
            if np.isscalar(eps)
            else np.asarray(eps, dtype=float)
        )
        x_eff = float(x)
        if skew is not None:
            x_eff += float(np.mean(np.asarray(skew)[acq])) * dxdt
        if offset is not None:
            pass  # offset applied per-slice below
        v_prev = self.v_top[acq]
        target = np.full(acq.size, x_eff)
        if offset is not None:
            target = target + np.asarray(offset)[acq]
        e = -eps_arr * (target - v_prev)
        self.v_top[acq] = target + e
        self.held_valid[acq] = True
        if offset is not None:
            return float(np.mean(e) + np.mean(np.asarray(offset)[acq]))
        return float(np.mean(e))

    def held_voltage(self, conv: np.ndarray) -> float:
        """Mean held voltage [V] of the converting group, without consuming it.

        Args:
            conv: ``(n_active,)`` int64 slice indices.

        Returns:
            Mean held top-plate voltage [V].

        Raises:
            ValueError: If any converting slice does not hold a valid sample.
                This is the runtime guard that makes the causality contract
                impossible to violate silently.
        """
        conv = np.asarray(conv, dtype=np.int64)
        if not bool(self.held_valid[conv].all()):
            bad = conv[~self.held_valid[conv]].tolist()
            raise ValueError(f"slices {bad} convert without a completed acquisition")
        return float(np.mean(self.v_top[conv]))

    def release(self, conv: np.ndarray) -> None:
        """Mark the converting group as consumed and ready for re-acquisition.

        Args:
            conv: ``(n_active,)`` int64 slice indices.

        Side effects:
            Clears ``held_valid`` for the group and increments its use counter.
        """
        conv = np.asarray(conv, dtype=np.int64)
        self.held_valid[conv] = False
        self.n_conversions[conv] += 1
