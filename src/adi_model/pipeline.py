"""Public phase-pipeline entry point; physical implementation is pipeline_engine.

Acquisition charge is tied to its sample and physical slice group. The first
record is explicitly primed; no conversion consumes a different group's charge.
"""

from __future__ import annotations

from .pipeline_engine import execute_split
from .sampler import input_derivative


def run_pipeline(
    cfg, input_fn, n_samples, chip=None, state=None, rng=None, scheduler=None, *, pool=None
):
    """Run the physical split pipeline with explicit capacitor ownership.

    Args:
        cfg: Configuration (resolved as split topology).
        input_fn: Input voltage callable, with times in seconds.
        n_samples: Number of output samples after priming.
        chip: Optional aggregate fabricated split realization.
        state: Optional frozen digital coefficients.
        rng: Optional sampling/noise random stream.
        scheduler: Optional compatible acquisition scheduler.
        pool: Optional existing PhysicalSlicePool; resets electrical state only.

    Returns:
        SimResult with charge, sample IDs, actual converting slices and errors.
    """
    return execute_split(
        cfg,
        input_fn,
        n_samples,
        chip=chip,
        state=state,
        rng=rng,
        scheduler=scheduler,
        pool=pool,
        derivative_fn=input_derivative,
    )
