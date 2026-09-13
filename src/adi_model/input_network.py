"""Continuous passive input network, with explicit electrical state and SI units.

The source resistance is shared by all tracking capacitors, including the SADC.
For a zero-capacitance bus, its voltage is eliminated by Kirchhoff's current
law, not by dividing the source resistance among slices. An optional filter
capacitor retains the bus voltage between acquisitions. Linear intervals are
integrated exactly in capacitance-normalized modal coordinates. Arbitrary
waveforms use first-order holds; voltage-dependent switch resistance uses
piecewise midpoint values, with an explicit resolution for convergence studies.

This is an assumed passive topology, not an extracted circuit. Sampling thermal
noise is injected at the aperture elsewhere; it is not integrated a second time.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np


@dataclass(frozen=True)
class InputNetworkParameters:
    """Assumed network controls; all capacitances in F and voltages in V.

    Attributes:
        filter_cap_f: Capacitance at the shared input bus (zero eliminates it).
        integration_steps: Subintervals for nonlinear Ron or arbitrary waveforms.
            Linear DC/sine inputs are exact and require only one interval.
    """

    filter_cap_f: float = 0.0
    integration_steps: int = 8

    def violations(self) -> list[str]:
        """Return invalid physical or numerical settings without mutating them."""
        bad = []
        if not np.isfinite(self.filter_cap_f) or self.filter_cap_f < 0:
            bad.append("input_network.filter_cap_f must be finite and nonnegative")
        if (
            isinstance(self.integration_steps, bool)
            or not isinstance(self.integration_steps, int)
            or not 1 <= self.integration_steps <= 4096
        ):
            bad.append("input_network.integration_steps must be an integer in [1, 4096]")
        return bad


class OffsetWaveform:
    """A continuous source with an interval-constant analog injection [V]."""

    def __init__(self, source, offset_v: float):
        """Retain exact harmonic metadata where the underlying source has it."""
        self.source = source
        self.offset_v = offset_v
        terms = getattr(source, "harmonics", None)
        self.harmonics = (*terms, (0.0, complex(offset_v))) if terms is not None else None

    def __call__(self, t):
        """Return the source voltage plus held injection at time t [s]."""
        return self.source(t) + self.offset_v


class PassiveTrackingNetwork:
    """One linear shared-source RC network; each state is a capacitor voltage.

    Input branches appear first in state arrays. A filter capacitor, if present,
    is the last state. No mutable electrical state is retained by this solver.
    """

    def __init__(self, caps_f, switch_ohm, source_ohm: float, filter_cap_f: float = 0):
        """Construct a positive passive network from branch C/R and shared R.

        Args:
            caps_f: Positive one-dimensional branch capacitances [F].
            switch_ohm: Positive branch resistances [ohm], same shape.
            source_ohm: Nonnegative shared source resistance [ohm].
            filter_cap_f: Nonnegative bus capacitance [F]. With zero source
                resistance its voltage is prescribed by the ideal source.

        Raises:
            ValueError: Invalid shape, sign or nonfinite circuit values.
        """
        caps = np.asarray(caps_f, dtype=float)
        ron = np.asarray(switch_ohm, dtype=float)
        if (
            caps.ndim != 1
            or caps.size == 0
            or ron.shape != caps.shape
            or np.any(~np.isfinite(caps))
            or np.any(caps <= 0)
            or np.any(~np.isfinite(ron))
            or np.any(ron <= 0)
            or not np.isfinite(source_ohm)
            or source_ohm < 0
            or not np.isfinite(filter_cap_f)
            or filter_cap_f < 0
        ):
            raise ValueError("tracking network requires finite positive C/R and nonnegative Rs/Cf")
        self.has_bus_state = bool(filter_cap_f > 0 and source_ohm > 0)
        g = 1.0 / ron
        if source_ohm == 0:
            conductance = np.diag(g)
        elif self.has_bus_state:
            conductance = np.diag(np.r_[g, g.sum() + 1.0 / source_ohm])
            conductance[:-1, -1] = -g
            conductance[-1, :-1] = -g
            caps = np.r_[caps, filter_cap_f]
        else:
            conductance = np.diag(g) - np.outer(g, g) / (g.sum() + 1.0 / source_ohm)
        self.caps_f = caps.copy()
        self._sqrt_c = np.sqrt(caps)
        normalized = conductance / self._sqrt_c[:, None] / self._sqrt_c[None, :]
        self.rates, self._u = np.linalg.eigh(normalized)
        if np.any(self.rates <= 0):
            raise ValueError("RC network is numerically singular; rescale its component values")
        self._drive = self._u.T @ self._sqrt_c

    def advance(self, previous, t0: float, t1: float, input_fn) -> np.ndarray:
        """Advance capacitor voltages for a continuous forcing interval.

        Args:
            previous: Capacitor voltages [V], in the documented state order.
            t0: Interval start [s].
            t1: Interval end [s], strictly after t0.
            input_fn: Voltage callable. ``harmonics`` optionally contains
                ``(frequency_hz, complex_phasor_v)`` terms using real phasors.
                A plain callable is linearly interpolated within this interval.

        Returns:
            New capacitor voltages [V]. Neither input nor state is modified.

        Raises:
            ValueError: Nonfinite state/time or an invalid interval.
        """
        previous = np.asarray(previous, dtype=float)
        dt = t1 - t0
        if (
            previous.shape != self.caps_f.shape
            or np.any(~np.isfinite(previous))
            or not np.isfinite(t0)
            or not np.isfinite(t1)
            or dt <= 0
        ):
            raise ValueError(
                "state must match network and time interval must be finite and positive"
            )
        decay = np.exp(-self.rates * dt)
        modal = self._u.T @ (self._sqrt_c * previous)
        harmonics = getattr(input_fn, "harmonics", None)
        if harmonics is not None:
            forced = np.zeros_like(modal)
            for frequency, phasor in harmonics:
                omega = 2 * np.pi * frequency
                h = self.rates / (self.rates + 1j * omega)
                forced += np.real(
                    h * phasor * (np.exp(1j * omega * t1) - decay * np.exp(1j * omega * t0))
                )
        else:
            u0, u1 = float(input_fn(t0)), float(input_fn(t1))
            if not np.isfinite(u0) or not np.isfinite(u1):
                raise ValueError("input waveform must be finite")
            # Stable FOH integral, including dt << tau. A second-order expansion
            # avoids cancellation in 1 - (1-exp(-z))/z for very small z.
            z = self.rates * dt
            one_minus_decay = -np.expm1(-z)
            slope_weight = np.where(z < 1e-5, z / 2 - z**2 / 6 + z**3 / 24, 1 - one_minus_decay / z)
            forced = one_minus_decay * u0 + slope_weight * (u1 - u0)
        modal = decay * modal + self._drive * forced
        return (self._u @ modal) / self._sqrt_c

    def charge_change(self, before, after) -> float:
        """Return source-delivered charge [C], from capacitor charge conservation."""
        return float(np.dot(self.caps_f, np.asarray(after) - np.asarray(before)))


@lru_cache(maxsize=256)
def _linear_network(caps: tuple, ron: tuple, source: float, filter_cap: float):
    # Only immutable component tuples are keys. Solver instances contain no
    # electrical state; every caller supplies its own previous voltages.
    return PassiveTrackingNetwork(caps, ron, source, filter_cap)


def track_interval(
    caps_f,
    ron_ohm,
    source_ohm: float,
    previous,
    t0: float,
    t1: float,
    input_fn,
    *,
    parameters: InputNetworkParameters,
    ron_coefficient: float = 0.0,
    voltage_scale: float = 1.0,
) -> tuple[np.ndarray, float]:
    """Integrate one acquisition and return state [V] and source charge [C].

    Ron = Ron0 * (1 + ron_coefficient * (Vin/voltage_scale)**2). For nonlinear
    Ron, compare results with twice ``integration_steps`` before relying on a
    precision conclusion. Filter-bus state, if present, is last in ``previous``.
    """
    if parameters.violations():
        raise ValueError("; ".join(parameters.violations()))
    steps = parameters.integration_steps
    harmonics = getattr(input_fn, "harmonics", None)
    if harmonics is not None and (ron_coefficient == 0 or all(f == 0 for f, _ in harmonics)):
        steps = 1
    state = np.asarray(previous, dtype=float).copy()
    initial = state.copy()
    network = None
    for j in range(steps):
        a, b = t0 + (t1 - t0) * j / steps, t0 + (t1 - t0) * (j + 1) / steps
        if network is None or ron_coefficient != 0:
            factor = 1 + ron_coefficient * (float(input_fn((a + b) / 2)) / voltage_scale) ** 2
            network = _linear_network(
                tuple(caps_f),
                tuple(np.asarray(ron_ohm) * factor),
                source_ohm,
                parameters.filter_cap_f,
            )
        state = network.advance(state, a, b, input_fn)
    assert network is not None
    return state, network.charge_change(initial, state)
