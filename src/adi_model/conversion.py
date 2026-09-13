"""Joint reference recovery, residue-amplifier response and ADC2 acquisition.

Reference switching is a signed charge event. Between events, reference error
recovers exponentially and drives the same finite-bandwidth amplifier sampled
by ADC2. This retains the reference history even when its final voltage is near
ideal. Parameters describe an assumed reduced circuit; they are not silicon
bandwidth or slew-rate disclosures.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from .config import Config
    from .slice_pool import PhysicalSlicePool


@dataclass(frozen=True)
class ConversionParameters:
    """Assumed time-domain parameters; None bandwidth means an ideal follower.

    Attributes:
        quantizer_time_s: Time from sampling to the start of residue amplification.
        fine_reference_tau_s: Fine buffer recovery time; None uses dyn_tau_ref.
        ra_bandwidth_hz: Closed-loop RA signal bandwidth, not OTA unity-gain GBW.
        ra_dc_gain: OTA open-loop gain; None eliminates finite-gain error.
        ra_slew_v_s: Maximum RA output slope; None eliminates slew limiting.
        adc2_wide_bandwidth_hz: ADC2 track bandwidth during early amplification.
        adc2_narrow_bandwidth_hz: Later track bandwidth; None retains wide setting.
        narrow_start_fraction: Fraction of the RA interval at bandwidth change.
        rtol: Relative tolerance for nonlinear slew/clipping integration.
        atol_v: Absolute voltage tolerance for nonlinear integration [V].
    """

    quantizer_time_s: float = 7e-9
    fine_reference_tau_s: float | None = None
    ra_bandwidth_hz: float | None = None
    ra_dc_gain: float | None = None
    ra_slew_v_s: float | None = None
    adc2_wide_bandwidth_hz: float | None = None
    adc2_narrow_bandwidth_hz: float | None = None
    narrow_start_fraction: float = 0.65
    rtol: float = 1e-9
    atol_v: float = 1e-11

    def violations(self) -> list[str]:
        """Return invalid physical or numerical controls."""
        bad = []
        for name in (
            "quantizer_time_s",
            "fine_reference_tau_s",
            "ra_bandwidth_hz",
            "ra_dc_gain",
            "ra_slew_v_s",
            "adc2_wide_bandwidth_hz",
            "adc2_narrow_bandwidth_hz",
            "rtol",
            "atol_v",
        ):
            value = getattr(self, name)
            if value is not None and (not np.isfinite(value) or value <= 0):
                bad.append(f"conversion.{name} must be finite and positive")
        if not 0 < self.narrow_start_fraction < 1:
            bad.append("conversion.narrow_start_fraction must be in (0, 1)")
        if self.ra_slew_v_s is not None and self.ra_bandwidth_hz is None:
            bad.append("conversion.ra_slew_v_s requires a finite ra_bandwidth_hz")
        if self.adc2_narrow_bandwidth_hz is not None and self.adc2_wide_bandwidth_hz is None:
            bad.append("conversion.adc2_narrow_bandwidth_hz requires a finite wide bandwidth")
        return bad

    @property
    def dynamic(self) -> bool:
        """Whether any RA/ADC2 signal-response mechanism is requested."""
        return any(
            x is not None
            for x in (self.ra_bandwidth_hz, self.ra_dc_gain, self.adc2_wide_bandwidth_hz)
        )


@lru_cache(maxsize=256)
def _cascade_matrix(a: float, b: float, c: float, dt: float):
    from scipy.linalg import expm

    # Coordinates: decaying reference drive, RA minus DC target, ADC2 minus
    # DC target. A repeated pole is handled by expm, without divided-by-zero
    # special cases or cancellation of nearly equal exponentials.
    return expm(np.array([[-a, 0, 0], [b, -b, 0], [0, c, -c]]) * dt)


def response_interval(
    y0: float,
    z0: float,
    target_v: float,
    reference_drive_v: float,
    tau_ref_s: float,
    dt_s: float,
    *,
    ra_bw_hz: float | None,
    adc_bw_hz: float | None,
    clip_v: float,
    slew_v_s: float | None = None,
    rtol: float = 1e-9,
    atol_v: float = 1e-11,
) -> tuple[float, float, bool]:
    """Propagate one phase driven by target + reference_drive*exp(-t/tau).

    Returns RA output [V], ADC2 acquisition voltage [V] and RA clipping flag.
    Linear responses use a matrix exponential. Slew-limited or clipped responses
    use an adaptive ODE solve on normalized time; integration failure is fatal.
    """
    if not np.isfinite(dt_s) or dt_s <= 0 or tau_ref_s <= 0 or clip_v <= 0:
        raise ValueError("response interval needs positive duration, reference tau and swing")
    a = 1.0 / tau_ref_s
    b = 0.0 if ra_bw_hz is None else 2 * np.pi * ra_bw_hz
    c = 0.0 if adc_bw_hz is None else 2 * np.pi * adc_bw_hz
    ref_end = reference_drive_v * np.exp(-a * dt_s)
    max_drive = max(abs(target_v), abs(target_v + reference_drive_v), abs(y0))
    slope_bound = b * (max_drive + abs(y0))
    linear = max_drive < clip_v and (slew_v_s is None or slope_bound <= slew_v_s)
    if linear:
        if ra_bw_hz is None and adc_bw_hz is None:
            y = z = target_v + ref_end
        elif ra_bw_hz is None:
            # The second state follows the ideal RA; use the same exact cascade
            # matrix with its last state unused.
            h = _cascade_matrix(a, c, 0.0, dt_s)
            state = h @ [reference_drive_v, z0 - target_v, 0.0]
            y, z = target_v + ref_end, target_v + state[1]
        else:
            state = _cascade_matrix(a, b, c, dt_s) @ [
                reference_drive_v,
                y0 - target_v,
                z0 - target_v,
            ]
            y = target_v + state[1]
            z = y if adc_bw_hz is None else target_v + state[2]
        return float(y), float(z), False

    from scipy.integrate import solve_ivp

    def rhs(s, state):
        target = target_v + reference_drive_v * np.exp(-a * dt_s * s)
        physical_y = np.clip(target if ra_bw_hz is None else state[0], -clip_v, clip_v)
        dy = b * (target - physical_y)
        if slew_v_s is not None:
            dy = np.clip(dy, -slew_v_s, slew_v_s)
        if (physical_y >= clip_v and dy > 0) or (physical_y <= -clip_v and dy < 0):
            dy = 0.0
        return np.array([dy, c * (physical_y - state[1])]) * dt_s

    sol = solve_ivp(
        rhs,
        (0.0, 1.0),
        [np.clip(y0, -clip_v, clip_v), z0],
        method="DOP853",
        rtol=rtol,
        atol=atol_v,
        max_step=1 / 16,
    )
    if not sol.success or np.any(~np.isfinite(sol.y)):
        raise RuntimeError(f"joint conversion integration failed: {sol.message}")
    if ra_bw_hz is None:
        y = np.clip(target_v + ref_end, -clip_v, clip_v)
        saturated = max(abs(target_v + reference_drive_v), abs(target_v + ref_end)) >= clip_v
    else:
        y = np.clip(sol.y[0, -1], -clip_v, clip_v)
        saturated = np.max(np.abs(sol.y[0])) >= clip_v - 2 * atol_v
    z = y if adc_bw_hz is None else sol.y[1, -1]
    return float(y), float(z), bool(saturated)


@dataclass(frozen=True)
class ConversionResult:
    """Aperture observations and physical event diagnostics for one record."""

    ra_v: np.ndarray
    adc2_v: np.ndarray
    ra_sat: np.ndarray
    reference_error_v: np.ndarray  # (N, 2), aperture error [V]
    reference_charge_c: np.ndarray  # (N, E, 2), signed event charge [C]
    reference_peak_v: np.ndarray  # (N,), max rail error at switching [V]
    event_times_s: np.ndarray  # (E,), relative to sample aperture
    adc2_times_s: np.ndarray  # (N,), absolute acquisition aperture
    dac_reference_error_v: np.ndarray  # (N,), final input-referred DAC perturbation
    reference_event_error_v: np.ndarray  # (N, E, 2), immediately after each load
    phase_times_s: np.ndarray  # (3,), RA entry / narrow switch / ADC2 aperture
    phase_ra_v: np.ndarray  # (N, 3), noise-free boundary voltages
    phase_adc2_v: np.ndarray  # (N, 3), noise-free boundary voltages
    phase_reference_v: np.ndarray  # (N, 3, 2), boundary rail errors


class ConversionEngine:
    """Causal shared electrical state, advanced once per acquired sample.

    Noise is applied once by the runner as an output-equivalent aperture source;
    signal bandwidth parameters do not define its spectral transfer function.
    """

    def __init__(self, cfg: Config, pool: PhysicalSlicePool, units: int, n_samples: int):
        """Allocate a trace and cold-start the shared reference and amplifier."""
        self.cfg, self.pool, self.units = cfg, pool, units
        p = cfg.conversion
        self.tra = cfg.dyn_t_conv_frac / cfg.fs
        self.aperture = p.quantizer_time_s + self.tra
        self.fine_tau = p.fine_reference_tau_s or cfg.dyn_tau_ref
        events = 2 * cfg.b1 + 1 if cfg.rdac_bitwise_loading else 1
        times = (
            np.linspace(0, p.quantizer_time_s, events)
            if events > 1
            else np.array([p.quantizer_time_s])
        )
        self.result = ConversionResult(
            ra_v=np.zeros(n_samples),
            adc2_v=np.zeros(n_samples),
            ra_sat=np.zeros(n_samples, dtype=bool),
            reference_error_v=np.zeros((n_samples, 2)),
            reference_charge_c=np.zeros((n_samples, events, 2)),
            reference_peak_v=np.zeros(n_samples),
            event_times_s=times,
            adc2_times_s=np.arange(n_samples) / cfg.fs + self.aperture,
            dac_reference_error_v=np.zeros(n_samples),
            reference_event_error_v=np.zeros((n_samples, events, 2)),
            phase_times_s=np.array(
                [
                    p.quantizer_time_s,
                    p.quantizer_time_s + self.tra * p.narrow_start_fraction,
                    self.aperture,
                ]
            ),
            phase_ra_v=np.zeros((n_samples, 3)),
            phase_adc2_v=np.zeros((n_samples, 3)),
            phase_reference_v=np.zeros((n_samples, 3, 2)),
        )
        self.rail = np.zeros(2)
        self.y, self.z, self.next_sample = 0.0, 0.0, 0

    def step(self, i, ids, coarse, code, sid, held_v, bank_dither, residue_v, gain) -> float:
        """Convert sample i and return its final reference-induced DAC error [V].

        The returned physical residue correction must update the released slice
        state before that slice's next acquisition. Calls out of order are errors.
        """
        from .reference_charge import (
            physical_reference_program,
            sampled_bottom_voltages,
            sar_loading_codes,
        )

        if i != self.next_sample or i >= len(self.result.ra_v):
            raise ValueError(
                "conversion samples must advance consecutively within the allocated record"
            )
        cfg, p, r = self.cfg, self.cfg.conversion, self.result
        if i:
            self.rail *= np.exp(-(1 / cfg.fs - self.aperture) / cfg.dyn_tau_ref)
        if cfg.ra_autozero:
            # Explicit ideal zeroing phase, with a separately declared noise budget.
            self.y = 0.0
        if cfg.dyn_ref_settling:
            dither = code - coarse * self.units
            loads = (
                sar_loading_codes(int(coarse), cfg.b1, self.units, dither)
                if cfg.rdac_bitwise_loading
                else np.array([code])
            )
            program = physical_reference_program(
                cfg,
                self.pool,
                ids,
                loads,
                r.event_times_s,
                int(sid),
                sampled_bottom_voltages(cfg, held_v, bank_dither),
            )
            r.reference_charge_c[i] = program.charge_c
            previous_time = 0.0
            for event, (t, q) in enumerate(zip(r.event_times_s, program.charge_c, strict=True)):
                self.rail *= np.exp(-(t - previous_time) / cfg.dyn_tau_ref)
                self.rail -= q / cfg.dyn_c_decouple
                r.reference_peak_v[i] = max(r.reference_peak_v[i], float(np.max(np.abs(self.rail))))
                r.reference_event_error_v[i, event] = self.rail
                previous_time = t
            sensitivity = program.sensitivity[-1]
        else:
            sensitivity = np.zeros(2)
        actual_gain = gain
        if p.ra_dc_gain is not None:
            actual_gain *= p.ra_dc_gain / (p.ra_dc_gain + 1 + gain)
        target = actual_gain * residue_v
        drive = -actual_gain * np.dot(sensitivity, self.rail)
        # Entry observations precede activation of the RA/ADC2 tracking switches.
        r.phase_ra_v[i, 0], r.phase_adc2_v[i, 0], r.phase_reference_v[i, 0] = (
            self.y,
            self.z,
            self.rail,
        )
        durations = [self.tra * p.narrow_start_fraction, self.tra * (1 - p.narrow_start_fraction)]
        bandwidths = [
            p.adc2_wide_bandwidth_hz,
            p.adc2_narrow_bandwidth_hz or p.adc2_wide_bandwidth_hz,
        ]
        for phase, (dt, bandwidth) in enumerate(zip(durations, bandwidths, strict=True), 1):
            self.y, self.z, clipped = response_interval(
                self.y,
                self.z,
                target,
                drive,
                self.fine_tau,
                dt,
                ra_bw_hz=p.ra_bandwidth_hz,
                adc_bw_hz=bandwidth,
                clip_v=cfg.ra_v_clip,
                slew_v_s=p.ra_slew_v_s,
                rtol=p.rtol,
                atol_v=p.atol_v,
            )
            r.ra_sat[i] |= clipped
            drive *= np.exp(-dt / self.fine_tau)
            self.rail *= np.exp(-dt / self.fine_tau)
            r.phase_ra_v[i, phase], r.phase_adc2_v[i, phase], r.phase_reference_v[i, phase] = (
                self.y,
                self.z,
                self.rail,
            )
        r.ra_v[i], r.adc2_v[i], r.reference_error_v[i] = self.y, self.z, self.rail
        correction = float(np.dot(sensitivity, self.rail))
        r.dac_reference_error_v[i] = correction
        self.next_sample += 1
        return correction
