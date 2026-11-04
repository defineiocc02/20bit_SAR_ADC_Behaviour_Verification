"""Check the resolved RDAC event contract against a recorded structural trace.

The seven-event fixture was observed from the real structural controller at
baseline 2cbd359, Flash prefix plus six resolved bits, target coarse code 173.
It is an engineering contract, not a claim that the paper uniquely specifies
this switching sequence. Re-run the structural trace when RTL timing changes.
"""

import numpy as np

from adi_model.reference_charge import resolved_loading_codes


def test_reference_load_matches_recorded_structural_events():
    """The live DUT trace fixes both prefix and duplicate-decision semantics."""
    modeled = resolved_loading_codes(coarse=173, bits=9, units=1, dither=0)
    structural = np.array([128, 160, 160, 168, 172, 172, 173], dtype=float)
    np.testing.assert_array_equal(modeled, structural)
