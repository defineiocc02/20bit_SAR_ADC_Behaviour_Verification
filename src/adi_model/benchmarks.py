"""Separate published benchmark records from fitted behavioral noise assumptions."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np


@dataclass(frozen=True)
class PublishedBenchmark:
    """One source's rounded measured headline values, without merging sources."""

    name: str
    source: str
    dr_db: float
    nsd_v_rthz: float
    sample_rate_hz: float = 40e6
    input_peak_v: float = 3.0
    output_bits: int = 20
    flicker_corner_hz: float | None = None

    def to_dict(self) -> dict:
        """Report separately derived DR and flat-density noise estimates."""
        result = asdict(self)
        result.update(
            source_grade="published measurement (not a behavioral prediction)",
            noise_rms_from_dr_v=self.input_peak_v / np.sqrt(2) * 10 ** (-self.dr_db / 20),
            noise_rms_from_flat_nsd_v=self.nsd_v_rthz * np.sqrt(self.sample_rate_hz / 2),
            nsd_dbfs_hz=float(20 * np.log10(self.nsd_v_rthz / (self.input_peak_v / np.sqrt(2)))),
            integration_assumption="flat one-sided density over DC to fs/2; excludes unresolved drift",
        )
        return result

    def configuration(self, **overrides):
        """Create a nine-decision-bit candidate with this source's DR noise anchor.

        RA noise is fitted from the requested DR when ra_out_noise_rms=None.
        Recovering that DR is a budget-consistency check, not silicon validation.
        The topology, backend range and device noise allocation remain assumed.
        """
        from .config import Config

        values = {"target_dr_db": self.dr_db, "fs": self.sample_rate_hz, "v_fs": self.input_peak_v}
        values.update(overrides)
        return Config.paper_literal(**values)


PAPER_BENCHMARK = PublishedBenchmark(
    "isscc2024_digest", "[00] ISSCC 2024 Session 9.8, p.1 and Fig.9.8.7", 94.2, 9.3e-9
)
SLIDES_BENCHMARK = PublishedBenchmark(
    "isscc2024_slides",
    "[00_1] presentation pp.36-37 and p.42",
    94.6,
    8.8e-9,
    flicker_corner_hz=40.0,
)
