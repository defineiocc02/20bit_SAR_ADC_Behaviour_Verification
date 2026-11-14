"""Backward-compatible alias for :mod:`adi_model.metrics.metrics`.

History: this file used to be a second, independently maintained copy of
`metrics.py`. By 2026-11-12 the two had 17/17 functions with the same names, 14
of them byte-identical and the remaining three differing only in
``int(round(x))`` versus ``round(x)`` — which is not a semantic difference,
because ``round`` on a Python float or a numpy scalar already returns an int.

Two copies of the same production formula are exactly the "multiple drifting
authorities" the acceptance rules forbid, so the duplicate bodies were removed
and this module now re-exports the single implementation. Callers that import
``adi_model.metrics.metrics_lsb20`` keep working unchanged.

Evidence that the collapse is behaviour-preserving:
``tools/check_metrics_equivalence.py`` hashes seven public entry points before
and after the change; both modules and both runs must agree.
"""

from adi_model.metrics.metrics import *  # noqa: F403
from adi_model.metrics.metrics import (  # noqa: F401
    _band_max,
    _bh4,
    _estimate_fund_freq,
    _fft_harmonics,
    _fft_sfdr,
    _harmonic_design,
    _lsq_amp,
    _record,
    _window,
    inl_from_mean_error,
    integrate_noise_band,
    power_spectral_density,
    reference_precision_bits,
    sine_fit_metrics,
    spectrum_dbfs,
    static_test,
    summarize,
)
