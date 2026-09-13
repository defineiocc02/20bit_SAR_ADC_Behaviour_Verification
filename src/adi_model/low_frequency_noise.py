"""Stationary band-limited 1/f state at physical timestamps.

Random log-frequency quadrature represents S(f)=S0*fc/f on [fl,fh]. Independent
Gaussian sine/cosine coefficients give stationary Gaussian states with exact
ensemble total variance S0*fc*log(fh/fl). Finite mode density approximates the
continuous covariance/spectrum; quadrature convergence is separately testable.
No claim is made for an unbounded stationary 1/f spectrum or an AZ circuit.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ._arrays import readonly as _readonly


@dataclass(frozen=True)
class BandLimitedFlicker:
    """Frozen oscillator state, reusable across arbitrary physical time queries."""

    frequency_hz: np.ndarray
    cosine_v: np.ndarray
    sine_v: np.ndarray
    white_density_v2_hz: float
    corner_hz: float
    low_hz: float
    high_hz: float

    @classmethod
    def create(cls, white_density_v2_hz, corner_hz, low_hz, rng, *, high_hz=None, modes=128):
        """Draw independent Gaussian modes in stratified log-frequency bands."""
        high = corner_hz if high_hz is None else high_hz
        if (
            not np.all(np.isfinite([white_density_v2_hz, corner_hz, low_hz, high]))
            or white_density_v2_hz < 0
            or not 0 < low_hz < high <= corner_hz
            or type(modes) is not int
            or modes < 4
        ):
            raise ValueError(
                "flicker needs finite density, 0 < low < high <= corner, and >=4 modes"
            )
        width = np.log(high / low_hz) / modes
        frequency = low_hz * np.exp(width * (np.arange(modes) + rng.random(modes)))
        sigma = np.sqrt(white_density_v2_hz * corner_hz * width)
        return cls(
            _readonly(frequency),
            _readonly(rng.normal(0, sigma, modes)),
            _readonly(rng.normal(0, sigma, modes)),
            float(white_density_v2_hz),
            float(corner_hz),
            float(low_hz),
            float(high),
        )

    @property
    def expected_variance_v2(self) -> float:
        """Continuous target band integral; conditional on the assumed low cutoff."""
        return float(self.white_density_v2_hz * self.corner_hz * np.log(self.high_hz / self.low_hz))

    def at_times(self, time_s) -> np.ndarray:
        """Evaluate the same stationary realization at arbitrary absolute timestamps."""
        t = np.asarray(time_s, dtype=float)
        if np.any(~np.isfinite(t)):
            raise ValueError("physical timestamps must be finite")
        out = np.empty(t.size)
        # Fixed-order matrix-vector products make chunk boundaries immaterial.
        for start in range(0, t.size, 4096):
            angle = 2 * np.pi * t.ravel()[start : start + 4096, None] * self.frequency_hz
            out[start : start + 4096] = np.cos(angle) @ self.cosine_v + np.sin(angle) @ self.sine_v
        return out.reshape(t.shape)

    def at_adc_indices(self, sample_indices, sample_rate_hz: float) -> np.ndarray:
        """Query decimated or contiguous ADC timestamps without changing its clock."""
        index = np.asarray(sample_indices)
        if (
            not np.issubdtype(index.dtype, np.integer)
            or not np.isfinite(sample_rate_hz)
            or sample_rate_hz <= 0
        ):
            raise ValueError(
                "ADC queries need integer sample indices and a positive physical clock"
            )
        return self.at_times(index / sample_rate_hz)
