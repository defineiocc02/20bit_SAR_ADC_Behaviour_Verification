"""Keep the model-versus-structural RDAC event discrepancy visible.

The seven-event fixture was observed from the real structural controller at
baseline 2cbd359, Flash prefix plus six resolved bits, target coarse code 173.
It is an engineering contract, not a claim that the paper uniquely specifies
this switching sequence. Re-run the structural trace when RTL timing changes.
"""

import numpy as np
import pytest

from adi_model.reference_charge import sar_loading_codes


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Known reference-load contract mismatch: model trial/reject events differ "
        "from the recorded structural DUT resolved-bit sequence. "
        "Remove this marker only after rechecking the live DUT and event contract."
    ),
)
def test_reference_load_matches_recorded_structural_events():
    """An expected failure expires when the model matches this DUT fixture."""
    modeled = sar_loading_codes(coarse=173, bits=9, units=1, dither=0)
    structural = np.array([128, 160, 160, 168, 172, 172, 173], dtype=float)
    np.testing.assert_array_equal(modeled, structural)
