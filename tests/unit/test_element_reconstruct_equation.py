"""The element-weight reconstruction must invert its own training equation.

The review supplied a minimal counterexample:

    x = 0.2 V, w = [0.1, 0.05], Vbottom = [+1, -1]
    fine = sum(w*(x - Vbottom)) = 0.1*(-0.8) + 0.05*1.2 = -0.02
    correct x_hat = 0.2
    the old code returned about -0.466667

Two defects produced that: the bottom-rail term was subtracted instead of added,
and the denominator summed every physical slice instead of only the active ones.
Both are equation errors, so the assertion is exact rather than tolerance-based.
"""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from adi_model.calibration.mask_calibration import ElementWeightCalibration


def _probe() -> SimpleNamespace:
    """One sample, two physical slices, one unit each, masks [0, 1]."""
    return SimpleNamespace(
        slice_ids=np.array([[0, 1]]),
        adc2_code=np.zeros(1, dtype=np.int64),
        main_masks=np.array([[[0], [1]]], dtype=np.int64),
        sub_masks=np.zeros((1, 2, 0), dtype=np.int64),
        fine_v=np.array([-0.02]),
        signal_mask=np.ones((1, 2, 1), dtype=np.int64),
    )


def _calibration(weights=None) -> ElementWeightCalibration:
    if weights is None:
        weights = np.array([[0.1], [0.05]])
    return ElementWeightCalibration(
        weights=np.asarray(weights, dtype=float),
        offset_v=0.0,
        residual_rms_v=0.0,
        n_samples=1,
        rank=2,
        condition=1.0,
        reference_high_v=-1.0,
        reference_low_v=1.0,
        n_main=1,
        n_sub=0,
    )


def test_counterexample_from_review():
    x_hat = _calibration().reconstruct(_probe(), None)
    assert np.allclose(x_hat, [0.2], atol=1e-12), x_hat


# NOTE: a second synthetic case (3 physical slices, 2 active) was attempted to
# pin the denominator to the active set. The mask layout expected by
# `_mask_matrix` is stricter than a hand-built namespace reproduces, so that case
# is NOT covered here yet — it needs a proper DigitalObservation fixture. The
# denominator fix itself is in place; only its direct test is outstanding.
