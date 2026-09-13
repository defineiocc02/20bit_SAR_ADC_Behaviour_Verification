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

import numpy as np

from .config import Config
from .dac_arch import SplitChip, build_split_chip
from .dem import split_switch_command
from .provenance import SourceGrade
from .timing import SlicePlan, build_slice_plan

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
        chip: SplitChip | None = None,
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
            chip: Optional aggregate split-chip realization to distribute over
                slices; otherwise every physical capacitor is independently drawn.

        Side effects:
            Consumes draws from ``rng``; no global state is touched.
        """
        self.cfg = cfg
        n_slices = int(cfg.n_slices)
        n_up = int(n_unit_per_slice or cfg.n_unit_per_slice)
        self.is_split = cfg.dac_arch == "split"
        self.template_chip = (chip or build_split_chip(cfg)) if self.is_split else None
        if self.is_split:
            n_up = cfg.dac_n_main + cfg.dac_n_sub
        self.n_slices = n_slices
        self.n_unit_per_slice = n_up
        self.n_units = int(cfg.n_active) * n_up

        # ---- variance split: (global, slice, unit) as variance fractions ----
        g_frac, s_frac, u_frac = cfg.mismatch_split
        tot = max(g_frac + s_frac + u_frac, 1e-12)
        s_frac, u_frac = s_frac / tot, u_frac / tot
        c_unit_nom = (
            cfg.dac_unit_cap() / cfg.n_active
            if self.is_split
            else (cfg.c_total0 * cfg.cap_scale) / self.n_units
        )
        sig0 = cfg.sigma_mismatch_unit(c_unit_nom) if self.is_split else float(cfg.mismatch_sigma0)
        sig_slice = float(slice_sigma) if slice_sigma is not None else sig0 * np.sqrt(s_frac)
        sig_unit = float(unit_sigma) if unit_sigma is not None else sig0 * np.sqrt(u_frac)

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
        global_dev = rng.normal(0, sig0 * np.sqrt(g_frac / tot)) if cfg.mismatch_enable else 0.0
        self.unit_caps = c_unit_nom * (1.0 + global_dev + self.slice_dev[:, None] + unit_dev)
        if self.is_split:
            assert self.template_chip is not None
            template = self.template_chip
            if chip is not None:
                self.unit_caps = np.tile(
                    np.concatenate((chip.C_main, chip.C_sub)) / cfg.n_active,
                    (n_slices, 1),
                )
            self.bridge_caps = np.full(n_slices, template.C_bridge_nom / cfg.n_active)
            self.sub_parasitic = np.full(n_slices, template.c_p_sub_nom / cfg.n_active)
            if chip is not None:
                self.bridge_caps[:] = chip.C_bridge / cfg.n_active
                self.sub_parasitic[:] = chip.c_p_sub / cfg.n_active
            elif cfg.mismatch_enable:
                self.bridge_caps *= 1 + cfg.dac_bridge_mismatch_sigma * rng.standard_normal(
                    n_slices
                )
                self.sub_parasitic *= 1 + cfg.dac_parasitic_spread * rng.standard_normal(n_slices)
            if (
                np.any(self.unit_caps <= 0)
                or np.any(self.bridge_caps <= 0)
                or np.any(self.sub_parasitic < 0)
            ):
                raise ValueError(
                    "physical split capacitances must be positive; parasitics nonnegative"
                )
        self.c_slice_total = self.unit_caps.sum(axis=1)
        self.c_total_nom = c_unit_nom * self.n_units

        # ---- per-slice held state -----------------------------------------
        self.v_top = np.zeros(n_slices)
        self.held_sample = np.full(n_slices, -1, dtype=np.int64)
        self.held_valid = np.zeros(n_slices, dtype=bool)
        self.n_conversions = np.zeros(n_slices, dtype=np.int64)
        # Clock/switch properties are chip state, not observation noise. Their
        # dedicated stream is unchanged when capacitor/noise draws are enabled.
        clock_rng = np.random.default_rng(cfg.seed + 8301)
        self.tau_rel = 1 + clock_rng.normal(0, cfg.slice_bw_spread, n_slices)
        self.t_skew = clock_rng.normal(0, cfg.slice_timing_skew_s, n_slices)
        self.v_os = clock_rng.normal(0, cfg.slice_offset_sigma_v, n_slices)
        self.v_top_q = np.zeros(2)

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
        return build_slice_plan(self.cfg, n_samples, rng, strategy)

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
        if self.is_split:
            return self.split_coefficients(conv)["c_signal"]
        return self.unit_caps[np.asarray(conv, dtype=np.int64)].sum(axis=(1, 2))

    def split_coefficients(self, conv: np.ndarray) -> dict[str, np.ndarray]:
        """Derive physical signal/load/noise/mask quantities from selected slices.

        Args:
            conv: Array of actual physical slice IDs, shape (N, n_active).

        Returns:
            Per-sample capacitances [F], physical alpha and per-slice weights.
            Each slice has its own floating subnode and bridge; summing sub
            capacitors before solving beta would incorrectly short those nodes.
        """
        if not self.is_split:
            raise ValueError("split_coefficients requires a split pool")
        ids = np.asarray(conv, dtype=np.int64)
        caps = self.unit_caps
        nm = self.cfg.dac_n_main
        a, b = caps[:, :nm].sum(axis=-1)[ids], caps[:, nm:].sum(axis=-1)[ids]
        beta = self.bridge_caps[ids] / (self.bridge_caps[ids] + b + self.sub_parasitic[ids])
        c_slice = a + beta * b
        c_signal = c_slice.sum(axis=-1)
        mask = np.zeros_like(a)
        weight = np.ones_like(a)
        nd = self.cfg.dither_units_total
        if nd:
            if self.cfg.dither_split_bank == "sub":
                mask = caps[:, -nd:].sum(axis=-1)[ids]
                weight = beta
            else:
                mask = caps[:, nm - nd : nm].sum(axis=-1)[ids]
        signal_slice = c_slice - weight * mask
        return {
            "c_signal": c_signal,
            "c_load": (a + b - mask).sum(axis=-1),
            "c_noise": c_signal**2 / (a + beta**2 * b).sum(axis=-1),
            "alpha": signal_slice.sum(axis=-1) / c_signal,
            "signal_slice": signal_slice,
            "c_slice": c_slice,
            "load_slice": a + b - mask,
            "beta": beta,
        }

    def split_dac_voltage(self, conv: np.ndarray, k: np.ndarray, sid: np.ndarray) -> np.ndarray:
        """Evaluate selected-slice split charge with independent main/sub DEM.

        Args:
            conv: Actual converting slice IDs (N, n_active).
            k: Fine RDAC command (N,), including dither; clipped physically.
            sid: Nominal DEM states (N,). Main/sub axes are decoded separately.

        Returns:
            Input-referred DAC voltage [V], from the same caps used in sampling.
        """
        if not self.is_split:
            raise ValueError("split_dac_voltage requires a split pool")
        cfg = self.cfg
        ids = np.asarray(conv, dtype=np.int64)
        code = np.clip(np.asarray(k, dtype=float), 0, cfg.dac_levels - 1)
        states = (
            np.asarray(sid, dtype=np.int64)
            if cfg.dem_enable
            else np.zeros(len(code), dtype=np.int64)
        )
        result = np.empty(len(code))
        # Bounded scratch memory; no cache can go stale after an explicit chip perturbation.
        for start in range(0, len(code), 512):
            sl = slice(start, start + 512)
            caps = self.unit_caps[ids[sl]]
            coeff = self.split_coefficients(ids[sl])
            command = split_switch_command(cfg, code[sl], states[sl])
            counts = (command.main_counts, command.sub_counts)
            orders = (command.main_order, command.sub_order)
            charge = np.zeros(caps.shape[:2])
            offset = 0
            for bank, size in enumerate((cfg.dac_n_main, cfg.dac_n_sub)):
                bank_caps = np.take_along_axis(
                    caps[..., offset : offset + size], orders[bank][:, None, :], axis=2
                )
                take = np.clip(counts[bank][..., None] - np.arange(size), 0, 1)
                selected = (bank_caps * take).sum(axis=-1)
                q = 2 * selected - bank_caps.sum(axis=-1)
                charge += q if bank == 0 else coeff["beta"] * q
                offset += size
            result[sl] = cfg.v_fs * charge.sum(axis=-1) / coeff["c_signal"]
        return result

    def split_sampling_charge(
        self, conv: np.ndarray, x: np.ndarray, bank_code: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        """Compute masked input and dither voltage from actual selected caps.

        Args:
            conv: Selected sampling slices (N, n_active).
            x: Input voltage at each aperture [V].
            bank_code: Known integer mask code or continuous interpolation.

        Returns:
            Tuple of stored signal-plus-dither voltage and dither voltage [V].
            Thermal noise is generated separately from the same coefficients.
        """
        cfg = self.cfg
        ids = np.asarray(conv, dtype=np.int64)
        coeff = self.split_coefficients(ids)
        injected = np.zeros(len(ids))
        nd = cfg.dither_units_total
        if nd:
            nm = cfg.dac_n_main
            idx = slice(nm - nd, nm) if cfg.dither_split_bank == "main" else slice(-nd, None)
            caps = self.unit_caps[:, idx][ids]
            weights = coeff["beta"] if cfg.dither_split_bank == "sub" else np.ones(ids.shape)
            if cfg.dither_discrete:
                take = np.arange(nd)[None, :] < (nd // 2 + bank_code[:, None])
                q = (caps * (2 * take[:, None, :] - 1)).sum(axis=-1)
            else:
                q = 2 * bank_code[:, None] * caps.mean(axis=-1)
            injected = cfg.v_fs * (q * weights).sum(axis=-1) / coeff["c_signal"]
        return coeff["alpha"] * x + injected, injected

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
        if self.is_split:
            voltage = self.split_dac_voltage(conv, k, sid)
            return 0.5 * (voltage / self.cfg.v_fs + 1) * self.signal_capacitance(conv)
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
        if self.is_split:
            if nominal:
                return -v_fs + np.asarray(k, dtype=float) * self.cfg.nominal_rdac_step
            return self.split_dac_voltage(conv, k, sid)
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
